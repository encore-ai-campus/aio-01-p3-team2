"""서버 재시작 후 기존 Workflow 복구 평가 보고서를 API와 대조한다.

실행 예: python -m backend.scripts.verify_workflow_recovery_report
--base-url http://127.0.0.1:8000 --report /tmp/backend_b_recovery_report.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import httpx
from backend.evaluation.recovery import verify_persisted_suite


def main() -> int:
    """기존 보고서의 모든 실행이 재시작한 서버에서도 동일한지 판정한다."""
    parser = argparse.ArgumentParser(description="Backend B Workflow 재시작 보존 검증")
    parser.add_argument("--base-url", required=True, help="재시작한 Backend API 주소")
    parser.add_argument("--report", required=True, type=Path, help="이전 반복 평가 JSON 파일")
    args = parser.parse_args()

    report = json.loads(args.report.read_text(encoding="utf-8"))
    if not isinstance(report, dict) or not isinstance(report.get("runs"), list):
        parser.error("--report에는 실행 목록이 있는 평가 JSON이 필요합니다.")
    if not report["runs"]:
        parser.error("빈 실행 목록은 재시작 보존 검증에 사용할 수 없습니다.")

    with httpx.Client(base_url=args.base_url, timeout=30) as client:
        verification = verify_persisted_suite(client, report)
    summary = verification["summary"]
    for run in verification["runs"]:
        if not run["passed"]:
            print(
                f"불일치: {run['scenario']} #{run['repetition']} "
                f"{run['task_id']} {run.get('error', '원본과 저장된 판정이 다릅니다.')}"
            )
    print(f"재시작 보존 검증: {summary['passed_runs']}/{summary['total_runs']} 통과")
    return 0 if summary["passed_runs"] == summary["total_runs"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
