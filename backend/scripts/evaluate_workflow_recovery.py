"""실행 중인 Backend API의 장애 복구를 반복 평가하고 JSON 보고서를 저장한다.

실행 예: python -m backend.scripts.evaluate_workflow_recovery --base-url
http://127.0.0.1:8000 --mode agent_mock --runs 3 --output /tmp/agent-eval.json
장애 사례에는 Backend의 ENABLE_FAULT_INJECTION=true 설정이 필요하다.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
from backend.evaluation.recovery import SCENARIOS, run_suite


def main() -> int:
    """명시한 서버·횟수·모델로 실행하고 실패 여부를 종료 코드에 반영한다."""
    parser = argparse.ArgumentParser(description="Agent Workflow 복구 반복 평가")
    parser.add_argument("--base-url", required=True, help="시험용 Backend API 주소")
    parser.add_argument(
        "--mode",
        required=True,
        choices=(
            "agent_mock",
            "agent_mcp",
            "agent_live",
            "backend_b_mock",
            "backend_b_mcp",
            "backend_b_live",
        ),
        help="서버에 설정한 WORKFLOW_ENGINE 값",
    )
    parser.add_argument("--runs", required=True, type=int, help="시나리오별 반복 횟수")
    parser.add_argument("--output", required=True, type=Path, help="JSON 보고서 저장 경로")
    parser.add_argument(
        "--scenario",
        action="append",
        choices=tuple(item.name for item in SCENARIOS),
        help="선택한 사례만 실행한다. 생략하면 전체 사례를 실행한다.",
    )
    parser.add_argument("--model", help="agent_live 실행에 사용한 OPENAI_MODEL 이름")
    parser.add_argument(
        "--allow-live-llm",
        action="store_true",
        help="실제 LLM의 반복 호출과 요금 발생을 확인한다.",
    )
    args = parser.parse_args()
    if args.runs < 1:
        parser.error("--runs는 1 이상이어야 합니다.")
    if args.mode in {"agent_live", "backend_b_live"} and (
        not args.allow_live_llm or not args.model
    ):
        parser.error("agent_live에는 --model과 --allow-live-llm이 필요합니다.")
    if args.mode not in {"agent_live", "backend_b_live"} and args.model:
        parser.error("--model은 agent_live에서만 사용합니다.")
    if args.output.exists():
        parser.error("보고서 파일이 이미 있습니다. 기존 평가 결과를 덮어쓰지 않습니다.")

    selected = tuple(item for item in SCENARIOS if not args.scenario or item.name in args.scenario)
    with httpx.Client(base_url=args.base_url, timeout=120) as client:
        report = run_suite(client, selected, args.runs)
    report["metadata"] = {
        "evaluated_at": datetime.now(UTC).isoformat(),
        "execution_mode": args.mode,
        "model": args.model,
        "repetitions_per_scenario": args.runs,
        "time_source": "API Trace의 첫 오류부터 재검증 성공 또는 Safe Stop까지",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    summary = report["summary"]
    print(
        f"평가 완료: {summary['passed_runs']}/{summary['total_runs']} 통과, "
        f"오류 관찰 {summary['observed_error_runs']}건, "
        f"자동 복구 {summary['recovered_runs']}건, "
        f"안전 종료 {summary['correct_safe_stop_runs']}/{summary['expected_safe_stop_runs']}건"
    )
    print(f"보고서: {args.output}")
    return 0 if summary["passed_runs"] == summary["total_runs"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
