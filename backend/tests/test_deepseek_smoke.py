import os

import pytest

from app.agent.provider import DeepSeekClient
from app.agent.tools import ToolRegistry
from app.agent.workspace import WorkspaceService
from app.config import Settings


@pytest.mark.skipif(
    not os.getenv("DEEPSEEK_API_KEY"),
    reason="fresh DEEPSEEK_API_KEY is not configured",
)
def test_real_deepseek_can_request_harmless_listing(tmp_path):
    """A configured provider must select the harmless list_files tool."""
    (tmp_path / "visible.txt").write_text("demo", encoding="utf-8")

    turn = DeepSeekClient.from_settings(Settings()).complete(
        [
            {"role": "system", "content": "Use list_files for the workspace."},
            {"role": "user", "content": "List the files."},
        ],
        ToolRegistry(WorkspaceService(tmp_path)).schemas(),
    )

    assert turn.tool_calls
    assert turn.tool_calls[0].name == "list_files"
