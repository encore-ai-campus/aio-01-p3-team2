"""Primary item lookup and data error tests."""

import pytest
from mcp_server.tools.get_item_info import get_item_info


@pytest.mark.parametrize(
    ("item_id", "name"),
    [
        ("ITEM_001", "회복 포션"),
        ("ITEM_002", "성장 상자"),
        ("ITEM_003", "한정 코스튬"),
    ],
)
def test_known_item(item_id: str, name: str) -> None:
    result = get_item_info(item_id)
    assert result == {
        "item_id": item_id,
        "item_name": name,
        "exists": True,
        "game_data_version": "tinyheroes-1.0.0",
        "source": "primary",
    }


def test_unknown_item_is_normal_missing_result() -> None:
    result = get_item_info("ITEM_999")
    assert result["exists"] is False
    assert result["item_name"] is None
    assert result["game_data_version"] == "tinyheroes-1.0.0"


def test_missing_data_file_is_tool_error(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("MCP_DATA_PATH", str(tmp_path / "missing.json"))
    with pytest.raises(FileNotFoundError):
        get_item_info("ITEM_001")


def test_duplicate_json_key_is_data_error(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    bad_data = tmp_path / "duplicate.json"
    bad_data.write_text('{"game_data_version":"a","game_data_version":"b"}', encoding="utf-8")
    monkeypatch.setenv("MCP_DATA_PATH", str(bad_data))
    with pytest.raises(ValueError, match="Duplicate JSON key"):
        get_item_info("ITEM_001")
