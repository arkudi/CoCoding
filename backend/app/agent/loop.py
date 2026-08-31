"""Deterministic, persisted model-and-tool execution loop."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from typing import Literal

from app.agent.provider import ModelProviderError
from app.agent.prompts import build_system_prompt
from app.agent.tools import ToolRegistry
from app.agent.types import AssistantTurn, ModelClient, ToolCall, ToolError, ToolResult
from app.agent.workspace import WorkspaceService
from app.db.run_repository import RunRepository


_EMPTY_FINAL_RESPONSE = "The model returned an empty final response."
_MAX_STEPS_ERROR = "The run reached its model-turn limit."
_CANCELLED_ERROR = "The run was cancelled."
_PROVIDER_ERROR = "The model provider request failed."
_TOOL_ERROR = "The tool could not be executed."
_PRIOR_HISTORY_CHARACTER_LIMIT = 40_000


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
    ) -> None:
        self._model = model
        self._registry = registry
        self._repository = repository
        self._workspace = workspace or registry._workspace

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
        token = cancellation or CancellationToken()
        messages = [
            {
                "role": "system",
                "content": build_system_prompt(self._workspace.root, max_steps),
            },
            *self._bounded_prior_messages(prior_messages),
            {"role": "user", "content": prompt},
        ]
        self._repository.add_message(run_id, session_id, "user", prompt)
        step_count = 0

        for step_count in range(1, max_steps + 1):
            if token.is_cancelled:
                return self._finish(run_id, step_count - 1, "cancelled", None, _CANCELLED_ERROR)
            try:
                turn = self._model.complete(messages, self._registry.schemas())
            except ModelProviderError as error:
                return self._finish(run_id, step_count, "failed", None, error.safe_message)
            except Exception:
                return self._finish(run_id, step_count, "failed", None, _PROVIDER_ERROR)

            self._persist_assistant(run_id, session_id, turn)
            messages.append(self._assistant_message(turn))

            if not turn.tool_calls:
                if turn.content is None or not turn.content.strip():
                    return self._finish(run_id, step_count, "failed", None, _EMPTY_FINAL_RESPONSE)
                return self._finish(run_id, step_count, "completed", turn.content, None)

            for call in turn.tool_calls:
                if token.is_cancelled:
                    return self._finish(run_id, step_count, "cancelled", None, _CANCELLED_ERROR)
                tool_call = self._repository.start_tool_call(
                    run_id, call.id, call.name, call.arguments_json
                )
                result = self._execute_tool(call)
                self._repository.finish_tool_call(
                    tool_call.id,
                    "succeeded" if result.ok else "failed",
                    result.to_json(),
                    result.duration_ms,
                )
                self._repository.add_message(
                    run_id, session_id, "tool", result.to_json(), tool_call_id=call.id
                )
                messages.append(
                    {"role": "tool", "tool_call_id": call.id, "content": result.to_json()}
                )

        return self._finish(run_id, step_count, "max_steps", None, _MAX_STEPS_ERROR)

    def _finish(
        self,
        run_id: str,
        step_count: int,
        status: Literal["completed", "failed", "max_steps", "cancelled"],
        final_response: str | None,
        error_text: str | None,
    ) -> AgentRunResult:
        self._repository.replace_file_changes(run_id, self._workspace.changes())
        self._repository.finish_run(
            run_id,
            status,
            step_count=step_count,
            final_response=final_response,
            error_text=error_text,
        )
        return AgentRunResult(status, step_count, final_response, error_text)

    def _persist_assistant(self, run_id: str, session_id: str, turn: AssistantTurn) -> None:
        tool_calls_json = None
        if turn.tool_calls:
            tool_calls_json = json.dumps(
                [self._tool_call_metadata(call) for call in turn.tool_calls], ensure_ascii=False
            )
        self._repository.add_message(
            run_id, session_id, "assistant", turn.content, tool_calls_json=tool_calls_json
        )

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
        try:
            return self._registry.execute(call)
        except Exception:
            return ToolResult(False, None, ToolError("TOOL_EXECUTION_ERROR", _TOOL_ERROR), 0)

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
