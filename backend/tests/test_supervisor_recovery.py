"""Keep bounded planning revisions independent of an LLM's agent-role guess."""

import unittest

from backend.app.agents.supervisor import Supervisor
from backend.app.services.llm_service import LLMService


class SupervisorRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_validation_failure_routes_to_planner_without_llm_veto(self) -> None:
        calls = []

        async def reject_recovery(agent_name, context):
            calls.append((agent_name, context))
            return {"approved": False, "reason": "검증 담당자를 다시 실행해야 합니다."}

        supervisor = Supervisor(
            {"context_access": {}}, LLMService(reject_recovery, 5, source="live")
        )

        route = await supervisor.review_failure(
            "event_brief_mismatch", current_agent="balance_rule_agent"
        )

        self.assertEqual(route.action, "RUN_AGENT")
        self.assertEqual(route.next_agent, "event_planner_agent")
        self.assertEqual(route.revision_count, 1)
        self.assertEqual(calls, [])

        exhausted = await supervisor.review_failure(
            "event_brief_mismatch",
            current_agent="balance_rule_agent",
            revision_count=2,
        )
        self.assertEqual(exhausted.action, "SAFE_STOP")

        technical_route = await supervisor.review_failure(
            "agent_failure", current_agent="balance_rule_agent"
        )
        self.assertEqual(technical_route.action, "RETRY")
        self.assertEqual(technical_route.retry_count, 1)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
