"""실제 MCP 서버와 Backend B MCPClient의 연결을 검증한다.

MCP_TEST_URL이 설정된 경우에만 실행한다. 기본 백엔드 테스트에서는
외부 서버가 없어도 이 테스트가 실패하지 않도록 건너뛴다.
"""

import os
import unittest
from pathlib import Path

import yaml
from backend.app.agents.balance_rule import BalanceRuleAgent
from backend.app.agents.critic import Critic
from backend.app.agents.event_planner import EventPlanner
from backend.app.agents.supervisor import Supervisor
from backend.app.guards.tool_guard import ToolGuard, ToolGuardError
from backend.app.orchestration.engine import WorkflowEngine
from backend.app.services.llm_service import LLMService
from backend.app.services.mcp_client import (
    MCPClient,
    create_streamable_http_transport,
)
from backend.app.services.planning_context_loader import PlanningContextLoader

AGENT_NAME = "balance_rule_agent"
MCP_TEST_URL = os.getenv("MCP_TEST_URL", "").strip()


@unittest.skipUnless(MCP_TEST_URL, "MCP_TEST_URL이 설정되지 않았습니다.")
class LiveMCPConnectionTests(unittest.IsolatedAsyncioTestCase):
    """실제 Streamable HTTP 서버의 Tool과 B Workflow 순서를 확인한다."""

    def setUp(self) -> None:
        config_path = Path(__file__).resolve().parents[1] / "app" / "config" / "agents.yaml"
        with config_path.open(encoding="utf-8") as config_file:
            agents_config = yaml.safe_load(config_file)

        self.transport = create_streamable_http_transport(MCP_TEST_URL)
        self.client = MCPClient(self.transport, ToolGuard(agents_config), 5)

    async def test_planning_context_tool_returns_typed_catalog(self) -> None:
        """새 Tool이 정수형 기간과 요청 대상의 성장 기준을 반환한다."""
        result = await self.client.get_event_planning_context(AGENT_NAME, "new_user", 3)

        self.assertEqual(result["target_user"], "new_user")
        self.assertEqual(result["duration"], 3)
        self.assertEqual(result["source"], "primary")
        self.assertEqual(result["user_segments"]["new_user"]["expected_levels"], {"1": 3, "3": 8})
        self.assertIn("HUNT_001", result["contents"])
        self.assertIn("ITEM_001", result["items"])

    async def test_planning_context_precedes_live_validation_queries(self) -> None:
        """A Loader와 B Engine이 실제 MCP Tool을 순서대로 호출한다."""
        config_path = Path(__file__).resolve().parents[1] / "app" / "config"
        with (config_path / "security_policies.yaml").open(encoding="utf-8") as config_file:
            security_policy = yaml.safe_load(config_file)
        traces: list[dict[str, object]] = []

        async def llm(agent_name: str, context: dict[str, object]):
            if agent_name == "critic_agent":
                return {"status": "PASS", "error_type": None, "reason": "검증된 계획입니다."}
            planning_data = context["planning_context"]
            self.assertIn(
                "HUNT_001", [item["content_id"] for item in planning_data["available_contents"]]
            )
            self.assertIn("ITEM_001", planning_data["reward_item_ids"])
            return {
                "target_user": "new_user",
                "duration": 3,
                "missions": [
                    {
                        "day": 1,
                        "content_id": "HUNT_001",
                        "mission_type": "monster_kill",
                        "target_count": 1,
                        "mission_name": "일반 사냥",
                    }
                ],
                "rewards": [{"day": 1, "item_id": "ITEM_001", "quantity": 1}],
            }

        async def trace_sink(event: dict[str, object]) -> None:
            traces.append(event)

        model = LLMService(llm, 5)
        engine = WorkflowEngine(
            Supervisor(security_policy),
            EventPlanner(model),
            BalanceRuleAgent(self.client),
            Critic(model),
            planning_context_loader=PlanningContextLoader(self.transport).load,
            trace_sink=trace_sink,
        )
        result = await engine.run(
            "task-live-planning",
            {
                "event_name": "성장 이벤트",
                "target_user": "new_user",
                "duration": 3,
                "objective": "신규 사용자 성장 지원",
            },
        )

        self.assertEqual(result.workflow_status, "WAITING_APPROVAL")
        actions = [(event["actor"], event["action"]) for event in traces]
        required = [
            ("mcp", "get_event_planning_context"),
            ("event_planner_agent", "create_plan"),
            ("mcp", "get_game_rules"),
            ("mcp", "get_item_info"),
            ("balance_rule_agent", "validate_plan"),
            ("critic_agent", "final_review"),
        ]
        positions = [actions.index(value) for value in required]
        self.assertEqual(positions, sorted(positions))
        self.assertEqual(traces[0]["phase"], "planning_context")
        self.assertEqual(traces[0]["data_source"], "primary")

    async def test_primary_game_rules(self) -> None:
        result = await self.client.get_game_rules(AGENT_NAME, "HUNT_001")

        self.assertEqual(result["content_id"], "HUNT_001")
        self.assertEqual(result["game_data_version"], "tinyheroes-1.0.0")
        self.assertEqual(result["source"], "primary")
        self.assertTrue(result["exists"])

    async def test_fallback_snapshot_after_primary_failure(self) -> None:
        result = await self.client.get_game_rules_fallback(
            AGENT_NAME,
            "HUNT_001",
            primary_failed=True,
        )

        self.assertEqual(result["content_id"], "HUNT_001")
        self.assertEqual(result["game_data_version"], "tinyheroes-1.0.0")
        self.assertEqual(result["source"], "fallback_snapshot")

    async def test_item_lookup(self) -> None:
        result = await self.client.get_item_info(AGENT_NAME, "ITEM_001")

        self.assertEqual(result["item_id"], "ITEM_001")
        self.assertTrue(result["exists"])
        self.assertTrue(result["item_name"])

    async def test_unknown_content_is_reported_as_missing(self) -> None:
        result = await self.client.get_game_rules(AGENT_NAME, "UNKNOWN")

        self.assertEqual(result["content_id"], "UNKNOWN")
        self.assertFalse(result["exists"])

    async def test_fallback_without_primary_failure_is_blocked(self) -> None:
        with self.assertRaises(ToolGuardError):
            await self.client.get_game_rules_fallback(
                AGENT_NAME,
                "HUNT_001",
                primary_failed=False,
            )
