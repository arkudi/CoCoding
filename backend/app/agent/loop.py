"""Deterministic, persisted model-and-tool execution loop."""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict, dataclass
from typing import Callable, Literal

from sqlalchemy import inspect

from app.agent.events import RunEvent
from app.agent.orchestration import SharedStepBudget, delegate_task_schema
from app.agent.provider import ModelProviderError
from app.agent.prompts import build_system_prompt
from app.agent.tools import ToolRegistry
from app.agent.types import AssistantTurn, ModelClient, ToolCall, ToolError, ToolResult
from app.agent.verifier import CompletionVerifier, VerificationPolicy, finish_task_schema
from app.agent.workspace import WorkspaceService
from app.db.run_repository import RunRepository


_EMPTY_FINAL_RESPONSE = "The model returned an empty final response."
_MAX_STEPS_ERROR = "The run reached its model-turn limit."
_CANCELLED_ERROR = "The run was cancelled."
_PROVIDER_ERROR = "The model provider request failed."
_TOOL_ERROR = "The tool could not be executed."
_INTERNAL_ERROR = "The run failed because of an internal error."
_FINISH_REQUIRED = (
    "[Runtime verification] A plain response cannot complete this Run. "
    "Call finish_task with evidence-backed completion details."
)
_PRIOR_HISTORY_CHARACTER_LIMIT = 40_000
_TOOL_PAYLOAD_CHARACTER_LIMIT = 20_000

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AgentRunResult:
    status: Literal["completed", "failed", "max_steps", "cancelled"]
    step_count: int
    final_response: str | None
    error_text: str | None


class CancellationToken:
    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()


class AgentLoop:
    def __init__(
        self,
        model: ModelClient,
        registry: ToolRegistry,
        repository: RunRepository,
        workspace: WorkspaceService | None = None,
        event_sink: Callable[[RunEvent], None] | None = None,
        verification_policy: VerificationPolicy | None = None,
        allowed_tools: frozenset[str] | None = None,
        delegator: Callable[[ToolCall], ToolResult] | None = None,
        shared_budget: SharedStepBudget | None = None,
        system_prompt: str | None = None,
        execution_id: str | None = None,
    ) -> None:
        self._model = model
        self._registry = registry
        self._repository = repository
        self._workspace = workspace or registry._workspace
        self._event_sink = event_sink
        self._verification_policy = verification_policy or VerificationPolicy()
        self._allowed_tools = allowed_tools
        self._delegator = delegator
        self._shared_budget = shared_budget
        self._system_prompt = system_prompt
        self._execution_id = execution_id
        self._current_step_count = 0

    def run(
        self,
        *,
        run_id: str,
        session_id: str,
        prompt: str,
        prior_messages: list[dict[str, object]],
        max_steps: int,
        cancellation: CancellationToken | None = None,
    ) -> AgentRunResult:
        self._current_step_count = 0
        try:
            return self._run(
                run_id=run_id,
                session_id=session_id,
                prompt=prompt,
                prior_messages=prior_messages,
                max_steps=max_steps,
                cancellation=cancellation,
            )
        except Exception as error:
            return self._recover_unexpected_failure(run_id, error)

    def _run(
        self,
        *,
        run_id: str,
        session_id: str,
        prompt: str,
        prior_messages: list[dict[str, object]],
        max_steps: int,
        cancellation: CancellationToken | None = None,
    ) -> AgentRunResult:
        token = cancellation or CancellationToken()
        self._workspace.capture_baseline()
        messages = [
            {
                "role": "system",
                "content": self._system_prompt or build_system_prompt(self._workspace.root),
            },
            *self._bounded_prior_messages(prior_messages),
            {"role": "user", "content": prompt},
        ]
        user_message = self._repository.add_message(run_id, session_id, "user", prompt)
        self._emit("message.created", run_id, self._record_data(user_message))
        step_count = 0
        model_tools = [*self._registry.schemas(self._allowed_tools), finish_task_schema()]
        if self._delegator is not None:
            model_tools.append(delegate_task_schema())

        for step_count in range(1, max_steps + 1):
            if token.is_cancelled:
                return self._finish(run_id, self._effective_step_count(step_count - 1), "cancelled", None, _CANCELLED_ERROR)
            if self._shared_budget is not None and not self._shared_budget.consume():
                return self._finish(
                    run_id,
                    self._shared_budget.used,
                    "max_steps",
                    None,
                    _MAX_STEPS_ERROR,
                )
            self._current_step_count = self._effective_step_count(step_count)
            self._assert_no_database_transaction()
            try:
                self._emit("assistant.started", run_id, {})
                turn = self._model.complete(
                    messages,
                    model_tools,
                    on_text_delta=lambda delta: self._emit(
                        "assistant.delta", run_id, {"delta": delta}
                    ),
                )
            except ModelProviderError as error:
                return self._finish(run_id, step_count, "failed", None, error.safe_message)
            except Exception:
                return self._finish(run_id, step_count, "failed", None, _PROVIDER_ERROR)

            self._persist_assistant(run_id, session_id, turn)
            self._emit("assistant.finished", run_id, {})
            messages.append(self._assistant_message(turn))

            if not turn.tool_calls:
                if turn.content is None or not turn.content.strip():
                    return self._finish(run_id, step_count, "failed", None, _EMPTY_FINAL_RESPONSE)
                messages.append({"role": "user", "content": _FINISH_REQUIRED})
                continue

            for call in turn.tool_calls:
                if token.is_cancelled:
                    return self._finish(run_id, step_count, "cancelled", None, _CANCELLED_ERROR)
                tool_call = self._repository.start_tool_call(
                    run_id,
                    call.id,
                    call.name,
                    call.arguments_json,
                    agent_execution_id=self._execution_id,
                )
                self._emit("tool.started", run_id, self._record_data(tool_call))
                if call.name == "finish_task":
                    if len(turn.tool_calls) != 1:
                        result = ToolResult(
                            False,
                            None,
                            ToolError(
                                "INVALID_COMPLETION",
                                "finish_task must be the only tool call in its model turn.",
                            ),
                            0,
                        )
                        completion = None
                    else:
                        verification = CompletionVerifier(
                            self._repository,
                            self._workspace,
                            self._verification_policy,
                        ).verify(run_id, call.arguments_json)
                        result = ToolResult(
                            verification.ok,
                            verification.payload(),
                            None
                            if verification.ok
                            else ToolError(
                                "COMPLETION_VERIFICATION_FAILED",
                                "The completion claims did not match recorded evidence.",
                            ),
                            0,
                        )
                        completion = verification.completion
                elif call.name == "delegate_task" and self._delegator is not None:
                    result = self._delegator(call)
                    completion = None
                else:
                    result = self._execute_tool(call)
                    completion = None
                payload = self._tool_payload(result)
                finished_call = self._repository.finish_tool_call(
                    tool_call.id,
                    "succeeded" if result.ok else "failed",
                    payload,
                    result.duration_ms,
                )
                self._emit("tool.finished", run_id, self._record_data(finished_call))
                tool_message = self._repository.add_message(
                    run_id, session_id, "tool", payload, tool_call_id=call.id
                )
                self._emit("message.created", run_id, self._record_data(tool_message))
                messages.append(
                    {"role": "tool", "tool_call_id": call.id, "content": payload}
                )
                if call.name == "finish_task" and result.ok and completion is not None:
                    return self._finish(
                        run_id,
                        self._effective_step_count(step_count),
                        "completed",
                        completion.summary,
                        None,
                    )

        return self._finish(
            run_id,
            self._effective_step_count(step_count),
            "max_steps",
            None,
            _MAX_STEPS_ERROR,
        )

    def _finish(
        self,
        run_id: str,
        step_count: int,
        status: Literal["completed", "failed", "max_steps", "cancelled"],
        final_response: str | None,
        error_text: str | None,
    ) -> AgentRunResult:
        file_changes = self._repository.replace_file_changes(
            run_id, self._workspace.changes()
        )
        self._emit(
            "files.changed",
            run_id,
            [self._record_data(change) for change in file_changes],
        )
        self._finish_agent_execution(status, step_count, final_response, error_text)
        self._repository.finish_run(
            run_id,
            status,
            step_count=step_count,
            final_response=final_response,
            error_text=error_text,
        )
        if self._event_sink is not None:
            detail = self._repository.get_run_detail(run_id)
            if detail is not None:
                self._emit("run.finished", run_id, asdict(detail))
        return AgentRunResult(status, step_count, final_response, error_text)

    def _persist_assistant(self, run_id: str, session_id: str, turn: AssistantTurn) -> None:
        tool_calls_json = None
        if turn.tool_calls:
            tool_calls_json = json.dumps(
                [self._tool_call_metadata(call) for call in turn.tool_calls], ensure_ascii=False
            )
        message = self._repository.add_message(
            run_id, session_id, "assistant", turn.content, tool_calls_json=tool_calls_json
        )
        self._emit("message.created", run_id, self._record_data(message))

    def _emit(self, event_type: str, run_id: str, data: object) -> None:
        if self._event_sink is None:
            return
        try:
            self._event_sink(RunEvent.create(event_type, run_id, data))
        except Exception as error:
            logger.warning(
                "Run event delivery failed (type=%s, error_type=%s)",
                event_type,
                type(error).__name__,
            )

    @staticmethod
    def _record_data(record: object) -> dict[str, object]:
        state = inspect(record)
        return {
            attribute.key: getattr(record, attribute.key)
            for attribute in state.mapper.column_attrs
        }

    @staticmethod
    def _bounded_prior_messages(
        prior_messages: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        selected: list[dict[str, object]] = []
        characters = 0
        for message in reversed(prior_messages):
            role = message.get("role")
            content = message.get("content")
            if role not in {"user", "assistant"}:
                continue
            content_length = len(content) if isinstance(content, str) else 0
            if characters + content_length > _PRIOR_HISTORY_CHARACTER_LIMIT:
                break
            selected.append({"role": role, "content": content})
            characters += content_length
        selected.reverse()
        return selected

    def _execute_tool(self, call: ToolCall) -> ToolResult:
        self._assert_no_database_transaction()
        try:
            if self._allowed_tools is None:
                return self._registry.execute(call)
            return self._registry.execute(call, self._allowed_tools)
        except Exception:
            return ToolResult(False, None, ToolError("TOOL_EXECUTION_ERROR", _TOOL_ERROR), 0)

    def _recover_unexpected_failure(
        self, run_id: str, error: Exception
    ) -> AgentRunResult:
        logger.exception(
            "Unexpected agent run failure (type=%s)",
            type(error).__name__,
        )
        self._rollback_repository()

        try:
            changes = self._workspace.changes()
            self._repository.replace_file_changes(run_id, changes)
        except Exception as evidence_error:
            logger.exception(
                "Could not persist final run evidence (type=%s)",
                type(evidence_error).__name__,
            )
            self._rollback_repository()

        failed_state_persisted = False
        try:
            self._finish_agent_execution(
                "failed", self._current_step_count, None, _INTERNAL_ERROR
            )
            self._repository.finish_run(
                run_id,
                "failed",
                step_count=self._current_step_count,
                error_text=_INTERNAL_ERROR,
            )
            failed_state_persisted = True
        except Exception as finish_error:
            logger.exception(
                "Could not persist failed run state (type=%s)",
                type(finish_error).__name__,
            )
            self._rollback_repository()

        if failed_state_persisted and self._event_sink is not None:
            terminal_data: object = {"status": "failed"}
            try:
                detail = self._repository.get_run_detail(run_id)
            except Exception as detail_error:
                logger.exception(
                    "Could not reload failed run detail (type=%s)",
                    type(detail_error).__name__,
                )
                self._rollback_repository()
            else:
                if detail is None:
                    logger.error("Could not reload failed run detail (run missing)")
                else:
                    terminal_data = asdict(detail)
            self._emit("run.finished", run_id, terminal_data)

        return AgentRunResult(
            "failed", self._current_step_count, None, _INTERNAL_ERROR
        )

    def _rollback_repository(self) -> None:
        try:
            self._repository.db.rollback()
        except Exception as rollback_error:  # pragma: no cover - defensive logging only
            logger.exception(
                "Could not roll back failed run transaction (type=%s)",
                type(rollback_error).__name__,
            )

    def _effective_step_count(self, local_step_count: int) -> int:
        if self._shared_budget is not None:
            return self._shared_budget.used
        return local_step_count

    def _finish_agent_execution(
        self,
        run_status: str,
        step_count: int,
        final_response: str | None,
        error_text: str | None,
    ) -> None:
        if self._execution_id is None:
            return
        execution_status = (
            "completed"
            if run_status == "completed"
            else "cancelled"
            if run_status == "cancelled"
            else "failed"
        )
        record = self._repository.finish_agent_execution(
            self._execution_id,
            execution_status,
            step_count=step_count,
            final_result_json=json.dumps(
                {"final_response": final_response, "error": error_text},
                ensure_ascii=False,
            ),
        )
        self._emit("agent.finished", record.run_id, self._record_data(record))

    def _assert_no_database_transaction(self) -> None:
        if self._repository.db.in_transaction():
            raise RuntimeError("Database transaction remained open across external agent work.")

    @staticmethod
    def _tool_payload(result: ToolResult) -> str:
        """Keep the normalized envelope; oversized prefixes and lengths live in data."""
        serialized = result.to_json()
        if len(serialized) <= _TOOL_PAYLOAD_CHARACTER_LIMIT:
            return serialized

        error = None
        if result.error is not None:
            error = {
                "code": result.error.code[:256],
                "message": result.error.message[:256],
            }

        def encode(prefix: str) -> str:
            return json.dumps(
                {
                    "ok": result.ok,
                    "data": {
                        "result_prefix": prefix,
                        "original_length": len(serialized),
                    },
                    "error": error,
                    "meta": {
                        "duration_ms": result.duration_ms,
                        "truncated": True,
                    },
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )

        low, high = 0, len(serialized)
        payload = encode("")
        while low <= high:
            midpoint = (low + high) // 2
            candidate = encode(serialized[:midpoint])
            if len(candidate) <= _TOOL_PAYLOAD_CHARACTER_LIMIT:
                payload = candidate
                low = midpoint + 1
            else:
                high = midpoint - 1
        return payload

    @classmethod
    def _assistant_message(cls, turn: AssistantTurn) -> dict[str, object]:
        message: dict[str, object] = {"role": "assistant", "content": turn.content}
        if turn.tool_calls:
            message["tool_calls"] = [cls._tool_call_metadata(call) for call in turn.tool_calls]
        return message

    @staticmethod
    def _tool_call_metadata(call: ToolCall) -> dict[str, object]:
        return {
            "id": call.id,
            "type": "function",
            "function": {"name": call.name, "arguments": call.arguments_json},
        }
