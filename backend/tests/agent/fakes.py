from __future__ import annotations

from collections import deque

from app.agent.types import AssistantTurn


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
