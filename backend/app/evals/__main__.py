"""CLI entry point: python -m app.evals path/to/suite.json"""

from __future__ import annotations

import argparse
import json

from app.agent.provider import DeepSeekClient
from app.config import get_settings
from app.evals.runner import EvalRunner, load_suite


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a CoCoding agent evaluation suite")
    parser.add_argument("suite", help="Path to a JSON evaluation suite")
    args = parser.parse_args()
    settings = get_settings()
    if not (settings.deepseek_api_key or "").strip():
        parser.error("DEEPSEEK_API_KEY is required to run model evaluations")
    suite = load_suite(args.suite)
    report = EvalRunner(lambda _case: DeepSeekClient.from_settings(settings)).run(suite)
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
