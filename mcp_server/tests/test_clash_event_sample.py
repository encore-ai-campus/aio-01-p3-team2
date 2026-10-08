"""Small public API sample is bounded and contains no player identifiers."""

from mcp_server.tools import get_clash_event_sample as sample_tool


def test_clash_sample_aggregates_public_profiles(monkeypatch):
    def fake_get(path):
        if path.startswith("/locations/global/rankings/players"):
            return {"items": [{"tag": f"#P{index}"} for index in range(20)]}
        if path.startswith("/locations/global/rankings/clans"):
            return {"items": [{"tag": f"#C{index}"} for index in range(3)]}
        if path.startswith("/players/"):
            index = int(path.split("P")[-1])
            return {"trophies": 1000 + index * 100, "townHallLevel": 8 + index % 3}
        if path.startswith("/clans/"):
            index = int(path.split("C")[-1])
            return {"members": 20 + index * 5}
        raise AssertionError(path)

    monkeypatch.setattr(sample_tool, "_get_json", fake_get)
    result = sample_tool.get_clash_event_sample()

    assert result["source"] == "clash_of_clans"
    assert result["game_data_version"].startswith("clash-sample-")
    assert result["sample"]["player_count"] == 20
    assert result["sample"]["clan_count"] == 3
    assert result["sample"]["median_trophies"] == 1900
    assert result["sample"]["median_clan_members"] == 25
    assert "#P" not in str(result)
