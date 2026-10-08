"""실제 OpenAI 응답으로 Backend B의 네 Agent 경로를 검증한다.

LLM_TEST_MODEL을 명시하고 OPENAI_API_KEY가 환경변수 또는 backend/.env에
있는 경우에만 실행한다. 호출마다 API 요금이 발생하므로 기본 백엔드
테스트에서는 건너뛴다. MCP 응답은 파일 안의 고정 자료를 사용한다.
"""

import os
import unittest
from collections.abc import Mapping
from pathlib import Path

import yaml
from backend.app.agents.balance_rule import BalanceRuleAgent
from backend.app.agents.critic import Critic
from backend.app.agents.event_planner import EventPlanner
from backend.app.agents.supervisor import Supervisor
from backend.app.guards.tool_guard import ToolGuard
from backend.app.orchestration.engine import WorkflowEngine
from backend.app.orchestration.planning_context import validate_planning_context
from backend.app.services.llm_service import LLMService, create_openai_transport
from backend.app.services.mcp_client import MCPClient
from dotenv import dotenv_values

LLM_TEST_MODEL = os.getenv("LLM_TEST_MODEL", "").strip()
if LLM_TEST_MODEL:
    api_key = dotenv_values(Path(__file__).resolve().parents[1] / ".env").get("OPENAI_API_KEY")
    if api_key:
        os.environ.setdefault("OPENAI_API_KEY", api_key)
GAME_DATA_VERSION = "tinyheroes-1.0.0"
CONTENTS = {
    "HUNT_001": {"content_name": "일반 사냥", "required_level": 1},
    "DUNGEON_001": {"content_name": "일반 던전", "required_level": 10},
    "RAID_001": {"content_name": "레이드", "required_level": 30},
}
ITEMS = {
    "ITEM_001": {"item_name": "성장 상자"},
    "ITEM_002": {"item_name": "강화석"},
}
USER_SEGMENTS = {
    "new_user": {
        "validation_policy": "expected_level",
        "expected_levels": {"1": 3, "3": 8, "5": 12, "7": 15},
    },
    "returning_user": {
        "validation_policy": "expected_level",
        "expected_levels": {"1": 25, "3": 28, "5": 32, "7": 35},
    },
    "all_user": {
        "validation_policy": "content_access_only",
        "common_access_max_required_level": 1,
    },
}
BRIEF = {
    "event_name": "새싹 모험가 성장 이벤트",
    "target_user": "new_user",
    "duration": 5,
    "objective": "신규 사용자가 5일 동안 접속해 성장하도록 돕는다.",
}


@unittest.skipUnless(
    LLM_TEST_MODEL and os.getenv("OPENAI_API_KEY"),
    "LLM_TEST_MODEL 또는 OPENAI_API_KEY가 설정되지 않았습니다.",
)
class LiveLLMTests(unittest.IsolatedAsyncioTestCase):
    """실제 LLM이 제한된 기획 자료와 출력 계약을 지키는지 확인한다."""

    def setUp(self) -> None:
        config_path = Path(__file__).resolve().parents[1] / "app" / "config"
        with (config_path / "agents.yaml").open(encoding="utf-8") as config_file:
            self.agents_config = yaml.safe_load(config_file)
        with (config_path / "security_policies.yaml").open(encoding="utf-8") as config_file:
            self.security_policy = yaml.safe_load(config_file)
        self.llm = LLMService(
            create_openai_transport(LLM_TEST_MODEL, self.agents_config),
            60,
            source="live",
        )

    @staticmethod
    async def _load(target_user: str, duration: int) -> Mapping[str, object]:
        """확정된 기획용 MCP 응답 형식의 고정 자료를 반환한다."""
        levels = USER_SEGMENTS[target_user]["expected_levels"]
        segment = {
            **USER_SEGMENTS[target_user],
            "expected_levels": {day: lv for day, lv in levels.items() if int(day) <= duration},
        }
        return {
            "target_user": target_user,
            "duration": duration,
            "contents": CONTENTS,
            "items": ITEMS,
            "user_segments": {target_user: segment},
            "game_data_version": GAME_DATA_VERSION,
            "source": "primary",
        }

    @staticmethod
    async def _mcp(tool_name: str, arguments: dict[str, object]) -> Mapping[str, object]:
        """검증용 ID별 Tool의 구조화된 응답 형식을 흉내 낸다."""
        if tool_name == "get_item_info":
            item_id = str(arguments["item_id"])
            item = ITEMS.get(item_id)
            return {
                "item_id": item_id,
                "item_name": item["item_name"] if item else None,
                "exists": item is not None,
                "game_data_version": GAME_DATA_VERSION,
                "source": "primary",
            }
        content_id = str(arguments["content_id"])
        content = CONTENTS.get(content_id)
        return {
            "content_id": content_id,
            "content_name": content["content_name"] if content else None,
            "required_level": content["required_level"] if content else None,
            "exists": content is not None,
            "game_data_version": GAME_DATA_VERSION,
            "source": "fallback_snapshot" if tool_name.endswith("fallback") else "primary",
            "user_segments": USER_SEGMENTS,
        }

    def _assert_plan_uses_listed_ids(
        self, plan: Mapping[str, object], planning_context: Mapping[str, object]
    ) -> None:
        """기획안의 대상·기간과 선택 ID가 전달한 자료 안에 있는지 확인한다."""
        content_ids = {item["content_id"] for item in planning_context["available_contents"]}
        self.assertEqual(plan["target_user"], BRIEF["target_user"])
        self.assertEqual(plan["duration"], BRIEF["duration"])
        for mission in plan["missions"]:
            self.assertIn(mission["content_id"], content_ids)
        for reward in plan["rewards"]:
            self.assertIn(reward["item_id"], planning_context["reward_item_ids"])

    async def test_planner_selects_ids_from_planning_context(self) -> None:
        """Planner가 접근 불가 콘텐츠와 목록에 없는 ID를 고르지 않는다."""
        planning_context = validate_planning_context(await self._load("new_user", 5), "new_user", 5)
        self.assertNotIn(
            "RAID_001", [item["content_id"] for item in planning_context["available_contents"]]
        )

        plan = await EventPlanner(self.llm).plan(
            {"user_request": BRIEF, "planning_context": planning_context, "revision_count": 0}
        )

        self._assert_plan_uses_listed_ids(plan, planning_context)

    async def test_live_llm_workflow_reaches_approval_candidate(self) -> None:
        """네 Agent의 실제 판단이 규칙 검증을 거쳐 승인 후보가 된다."""
        traces: list[dict[str, object]] = []

        async def trace_sink(event: dict[str, object]) -> None:
            traces.append(event)

        engine = WorkflowEngine(
            Supervisor(self.security_policy, self.llm),
            EventPlanner(self.llm),
            BalanceRuleAgent(
                MCPClient(self._mcp, ToolGuard(self.agents_config), 5),
                llm_service=self.llm,
            ),
            Critic(self.llm),
            planning_context_loader=self._load,
            trace_sink=trace_sink,
        )
        result = await engine.run("task-live-llm", BRIEF)

        summary = [(event["actor"], event["action"], event["status"]) for event in traces]
        self.assertEqual(result.workflow_status, "WAITING_APPROVAL", (result.error, summary))
        self.assertEqual(result.critic_status, "PASS")
        self.assertTrue(result.rule_validation["is_valid"])
        self.assertEqual(result.rule_validation["game_data_version"], GAME_DATA_VERSION)
        self.assertIn("LLM 판단:", result.rule_validation["reason"])
        routing = [
            event
            for event in traces
            if event["actor"] == "supervisor" and event["phase"] == "routing"
        ]
        self.assertEqual(
            [(event["action"], event["next_agent"]) for event in routing],
            [
                ("RUN_AGENT", "event_planner_agent"),
                ("RUN_AGENT", "balance_rule_agent"),
                ("RUN_AGENT", "critic_agent"),
                ("WAIT_APPROVAL", None),
            ],
        )
        self.assertTrue(all("LLM 판단:" in event["reason"] for event in routing))
        planning_context = validate_planning_context(await self._load("new_user", 5), "new_user", 5)
        self._assert_plan_uses_listed_ids(result.current_result, planning_context)
