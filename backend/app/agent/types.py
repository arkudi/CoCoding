from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ToolCall:
    id: str
    name: str
    arguments_json: str


@dataclass(frozen=True, slots=True)
class AssistantTurn:
    content: str | None
    tool_calls: tuple[ToolCall, ...] = ()


class ModelClient(Protocol):
    def complete(
        self,
        messages: list[dict[str, object]],
        tools: list[dict[str, object]],
    ) -> AssistantTurn:
        raise NotImplementedError
