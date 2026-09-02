from __future__ import annotations

import json

from app.agent.types import AssistantTurn, ToolCall
from app.evals.runner import EvalCase, EvalExpectations, EvalRunner, EvalSuite, load_suite
from tests.agent.fakes import ScriptedModelClient, finish


def tool_turn(name: str, arguments: dict[str, object], call_id: str) -> AssistantTurn:
    return AssistantTurn(None, (ToolCall(call_id, name, json.dumps(arguments)),))


def test_eval_runner_checks_complete_agent_trajectory() -> None:
    case = EvalCase(
        id="edit",
        prompt="edit the file",
        files={"note.txt": "before"},
        expect=EvalExpectations(
            files_equal={"note.txt": "after"},
            required_tools=["replace_in_file"],
            final_response_contains=["Updated"],
            max_steps=2,
        ),
    )
    model = ScriptedModelClient(
        [
            AssistantTurn(
                None,
                (ToolCall("call-1", "replace_in_file", json.dumps({
                    "path": "note.txt", "old_text": "before", "new_text": "after"
                })),),
            ),
            finish("Updated note.txt.", changed_files=["note.txt"]),
        ]
    )

    report = EvalRunner(lambda _case: model).run(EvalSuite(name="test-suite", cases=[case]))

    assert report.passed is True
    assert report.passed_cases == 1
    assert report.cases[0].tool_calls == 2
    assert report.cases[0].orchestration == "single"
    assert report.modes[0].orchestration == "single"
    assert all(check.passed for check in report.cases[0].checks)


def test_eval_runner_reports_failed_expectations() -> None:
    case = EvalCase(
        id="wrong-answer",
        prompt="answer",
        expect=EvalExpectations(final_response_contains=["wanted"]),
    )
    report = EvalRunner(lambda _case: ScriptedModelClient([finish("actual")])).run(
        EvalSuite(name="test-suite", cases=[case])
    )

    assert report.passed is False
    assert report.cases[0].passed is False
    assert any(check.name == "response_contains:wanted" and not check.passed for check in report.cases[0].checks)


def test_load_suite_rejects_unknown_fields(tmp_path) -> None:
    path = tmp_path / "suite.json"
    path.write_text(json.dumps({
        "name": "suite",
        "cases": [{"id": "case", "prompt": "work", "unexpected": True}],
    }), encoding="utf-8")

    try:
        load_suite(path)
    except ValueError:
        pass
    else:
        raise AssertionError("invalid suite should be rejected")


def test_eval_runner_reports_multi_agent_roles_and_mode_metrics() -> None:
    case = EvalCase(
        id="multi-edit",
        prompt="create a note",
        orchestration="multi",
        expect=EvalExpectations(
            files_equal={"note.txt": "done"},
            required_agent_roles=["manager", "implementer"],
            max_steps=4,
        ),
    )
    model = ScriptedModelClient(
        [
            tool_turn(
                "delegate_task",
                {
                    "role": "implementer",
                    "task": "Create note.txt",
                    "expected_output": "The changed file.",
                },
                "delegate",
            ),
            tool_turn(
                "write_file", {"path": "note.txt", "content": "done"}, "write"
            ),
            tool_turn(
                "finish_subtask",
                {
                    "summary": "Created the note.",
                    "relevant_files": ["note.txt"],
                    "findings": [],
                    "changed_files": ["note.txt"],
                    "tests": [],
                    "unresolved_issues": [],
                },
                "worker-finish",
            ),
            finish("Created note.txt.", changed_files=["note.txt"]),
        ]
    )

    report = EvalRunner(lambda _case: model).run(
        EvalSuite(name="multi-suite", cases=[case])
    )

    assert report.passed is True
    assert report.cases[0].agent_executions == 2
    assert report.cases[0].step_count == 4
    assert report.modes == (
        report.modes[0],
    )
    assert report.modes[0].orchestration == "multi"
    assert report.modes[0].average_steps == 4
