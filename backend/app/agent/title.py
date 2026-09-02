"""Generate a compact user-visible session title from the first task prompt."""

from __future__ import annotations

import re

from app.agent.types import ModelClient


_AUTO_TITLE_PREFIX = "新任务 · "


def needs_generated_title(title: str) -> bool:
    return title.startswith(_AUTO_TITLE_PREFIX)


def generate_task_title(model: ModelClient, prompt: str) -> str | None:
    turn = model.complete(
        [
            {
                "role": "system",
                "content": (
                    "Generate a concise task title in the user's language. "
                    "Return only the title, with no quotes, markdown, or explanation. "
                    "Use at most 40 characters."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        [],
    )
    if turn.tool_calls or not turn.content:
        return None
    title = re.sub(r"\s+", " ", turn.content).strip().strip("'\"`# ")
    if not title:
        return None
    return title[:40].rstrip()
