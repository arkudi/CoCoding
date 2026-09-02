from __future__ import annotations

from collections import deque
import json

from app.agent.types import AssistantTurn, ToolCall


def finish(
    summary: str,
    *,
    changed_files: list[str] | None = None,
    tests: list[dict[str, object]] | None = None,
    unresolved_issues: list[str] | None = None,
    call_id: str = "finish-task",
) -> AssistantTurn:
    """Build the mandatory structured completion turn used by agent tests."""
    return AssistantTurn(
        summary,
        (
            ToolCall(
                call_id,
                "finish_task",
                json.dumps({
                    "summary": summary,
                    "changed_files": changed_files or [],
                    "tests": tests or [],
                    "unresolved_issues": unresolved_issues or [],
                }),
            ),
        ),
    )


class ScriptedModelClient:
    """Deterministic model fake that records each request."""

    def __init__(self, scripted_turns: list[AssistantTurn | BaseException]) -> None:
        self._scripted_turns = deque(scripted_turns)
        self.calls: list[dict[str, object]] = []

    def complete(
        self,
        messages: list[dict[str, object]],
        tools: list[dict[str, object]],
        on_text_delta=None,
    ) -> AssistantTurn:
        self.calls.append({"messages": list(messages), "tools": tools})
        if not self._scripted_turns:
            raise AssertionError("Unexpected model call")
        next_turn = self._scripted_turns.popleft()
        if isinstance(next_turn, BaseException):
            raise next_turn
        if on_text_delta is not None and next_turn.content:
            on_text_delta(next_turn.content)
        return next_turn
