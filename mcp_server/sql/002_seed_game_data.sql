-- Reference draft only. Backend A owns shared DB seed; do not apply separately.
-- Seed the same tiny_heroes-1.0.0 catalog used by the existing MCP tools.
-- Re-running this file updates only the tiny_heroes row.
INSERT INTO game_data_catalog (game_id, game_data_version, data)
VALUES (
    'tiny_heroes',
    'tinyheroes-1.0.0',
    $game_data$
{
  "game_data_version": "tinyheroes-1.0.0",
  "contents": {
    "HUNT_001": {
      "content_name": "일반 사냥",
      "required_level": 1
    },
    "DUNGEON_001": {
      "content_name": "던전",
      "required_level": 10
    },
    "RAID_001": {
      "content_name": "레이드",
      "required_level": 30
    }
  },
  "items": {
    "ITEM_001": {
      "item_name": "회복 포션"
    },
    "ITEM_002": {
      "item_name": "성장 상자"
    },
    "ITEM_003": {
      "item_name": "한정 코스튬"
    }
  },
  "user_segments": {
    "new_user": {
      "validation_policy": "expected_level",
      "expected_levels": {
        "1": 3,
        "3": 8,
        "5": 12,
        "7": 15
      },
      "expected_levels_basis": {
        "type": "mock_historical_aggregate",
        "metric": "median_level",
        "description": "과거 신규 사용자 플레이 데이터를 집계했다고 가정한 MVP 기준값"
      }
    },
    "returning_user": {
      "validation_policy": "expected_level",
      "expected_levels": {
        "1": 25,
        "3": 28,
        "5": 32,
        "7": 35
      },
      "expected_levels_basis": {
        "type": "mock_historical_aggregate",
        "metric": "median_level",
        "description": "과거 복귀 사용자 플레이 데이터를 집계했다고 가정한 MVP 기준값"
      }
    },
    "all_user": {
      "validation_policy": "content_access_only",
      "common_access_max_required_level": 1
    }
  }
}
$game_data$::jsonb
)
ON CONFLICT (game_id) DO UPDATE
SET game_data_version = EXCLUDED.game_data_version,
    data = EXCLUDED.data,
    updated_at = CURRENT_TIMESTAMP;
