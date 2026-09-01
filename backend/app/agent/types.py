import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ToolCall:
    id: str
    name: str
    arguments_json: str


@dataclass(frozen=True, slots=True)
class ToolError:
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class ToolResult:
    ok: bool
    data: object | None
    error: ToolError | None
    duration_ms: int
    truncated: bool = False

    def to_json(self) -> str:
        return json.dumps(
            {
                "ok": self.ok,
                "data": self.data,
                "error": asdict(self.error) if self.error else None,
                "meta": {"duration_ms": self.duration_ms, "truncated": self.truncated},
            },
            ensure_ascii=False,
        )


@dataclass(frozen=True, slots=True)
class AssistantTurn:
    content: str | None
    tool_calls: tuple[ToolCall, ...] = ()


TextDeltaSink = Callable[[str], None]


class ModelClient(Protocol):
    def complete(
        self,
        messages: list[dict[str, object]],
        tools: list[dict[str, object]],
        on_text_delta: TextDeltaSink | None = None,
    ) -> AssistantTurn:
        raise NotImplementedError
