"""Mock 성장 기준과 게임 규칙의 핵심 판정 사례를 검증한다."""

import unittest

from backend.app.rules.game_rule_engine import GameDataError, GameRuleEngine


class GameRuleTests(unittest.TestCase):
    """대상별 성장 기준과 콘텐츠 접근 판정의 차이를 시험한다."""

    def setUp(self) -> None:
        """기획안에서 사용하는 세 가지 사용자 Segment 기준을 만든다."""
        self.segments = {
            "new_user": {"expected_levels": {"1": 3, "3": 8, "5": 12, "7": 15}},
            "returning_user": {"expected_levels": {"1": 25, "3": 28, "5": 32, "7": 35}},
            "all_user": {"common_access_max_required_level": 1},
        }
        self.raid = {"content_id": "RAID_001", "required_level": 30, "exists": True}

    def test_new_user_day_seven_cannot_enter_raid(self) -> None:
        """신규 사용자 Day 7 예상 레벨은 레이드 필요 레벨보다 낮다."""
        result = GameRuleEngine.validate_content_access(
            "RAID_001", "new_user", 7, self.raid, self.segments
        )
        self.assertEqual(result.error_type, "mission_unreachable")
        self.assertEqual(result.evidence["expected_level"], 15)

    def test_returning_user_day_five_can_enter_raid(self) -> None:
        """복귀 사용자 Day 5 예상 레벨은 레이드 필요 레벨 이상이다."""
        result = GameRuleEngine.validate_content_access(
            "RAID_001", "returning_user", 5, self.raid, self.segments
        )
        self.assertTrue(result.is_valid)

    def test_all_user_uses_common_access_policy(self) -> None:
        """전체 사용자에게는 예상 성장 대신 공통 접근 상한을 적용한다."""
        result = GameRuleEngine.validate_content_access(
            "RAID_001", "all_user", 7, self.raid, self.segments
        )
        self.assertEqual(result.error_type, "mission_unreachable")
        self.assertEqual(result.evidence["common_access_max_required_level"], 1)

    def test_unknown_content_is_planning_error(self) -> None:
        """MCP의 exists=false는 데이터 장애와 구분해 기획 오류로 판단한다."""
        result = GameRuleEngine.validate_content_access(
            "UNKNOWN",
            "new_user",
            1,
            {"content_id": "UNKNOWN", "exists": False},
            self.segments,
        )
        self.assertEqual(result.error_type, "mission_unreachable")

    def test_growth_checkpoint_uses_latest_previous_day(self) -> None:
        """Day 4는 가장 가까운 이전 기준인 Day 3 레벨을 사용한다."""
        self.assertEqual(GameRuleEngine.expected_level_for_day(self.segments["new_user"], 4), 8)

    def test_corrupt_growth_data_does_not_pass_as_valid(self) -> None:
        """잘못된 Mock 성장 데이터는 기획 오류로 둔갑시키지 않는다."""
        with self.assertRaises(GameDataError):
            GameRuleEngine.expected_level_for_day({"expected_levels": {"1": True}}, 1)
