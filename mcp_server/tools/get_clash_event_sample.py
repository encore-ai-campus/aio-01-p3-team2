"""Fetch a bounded public Clash of Clans sample for event planning."""

from __future__ import annotations

import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from statistics import median_low
from urllib import error, parse, request

API_ROOT = "https://api.clashofclans.com/v1"
PLAYER_LIMIT = 20
CLAN_LIMIT = 3


def _get_json(path: str) -> dict[str, object]:
    key = os.getenv("CLASH_OF_CLANS_API_KEY", "").strip()
    if not key:
        raise ValueError("CLASH_OF_CLANS_API_KEY 설정이 필요합니다.")
    req = request.Request(
        f"{API_ROOT}{path}",
        headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
    )
    try:
        with request.urlopen(req, timeout=8) as response:
            payload = json.load(response)
    except error.HTTPError as exc:
        if exc.code in {401, 403}:
            raise RuntimeError("Clash API 키 또는 허용 IP를 확인해 주세요.") from exc
        if exc.code == 429:
            raise RuntimeError(
                "Clash API 요청 한도를 초과했습니다. 잠시 후 다시 시도해 주세요."
            ) from exc
        raise RuntimeError(f"Clash API 조회에 실패했습니다 (HTTP {exc.code}).") from exc
    except (error.URLError, TimeoutError) as exc:
        raise ConnectionError("Clash API에 연결할 수 없습니다.") from exc
    if not isinstance(payload, dict):
        raise TypeError("Clash API 응답 형식이 올바르지 않습니다.")
    return payload


def _ranked_tags(path: str, limit: int) -> list[str]:
    payload = _get_json(f"{path}?limit={limit}")
    items = payload.get("items")
    if not isinstance(items, list):
        raise TypeError("Clash API 순위 응답에 목록이 없습니다.")
    tags = []
    for item in items:
        tag = item.get("tag") if isinstance(item, dict) else None
        if isinstance(tag, str) and tag.startswith("#") and tag not in tags:
            tags.append(tag)
    return tags[:limit]


def _profile(path_prefix: str, tag: str) -> dict[str, object]:
    return _get_json(f"/{path_prefix}/{parse.quote(tag, safe='')}")


def get_clash_event_sample() -> dict[str, object]:
    """Return aggregate-only data from a small, explicitly biased public sample.

    Individual player and clan tags are used only during lookup and are not
    returned to the event planner or retained in the task snapshot.
    """
    player_tags = _ranked_tags("/locations/global/rankings/players", PLAYER_LIMIT)
    clan_tags = _ranked_tags("/locations/global/rankings/clans", CLAN_LIMIT)
    if len(player_tags) < 5 or len(clan_tags) < 1:
        raise ValueError("이벤트 기획에 필요한 공개 표본을 확보하지 못했습니다.")

    with ThreadPoolExecutor(max_workers=5) as pool:
        players = list(pool.map(lambda tag: _profile("players", tag), player_tags))
        clans = list(pool.map(lambda tag: _profile("clans", tag), clan_tags))

    trophies = [player.get("trophies") for player in players]
    town_halls = [player.get("townHallLevel") for player in players]
    clan_members = [clan.get("members") for clan in clans]
    if any(type(value) is not int or value < 0 for value in trophies):
        raise ValueError("Clash 플레이어 트로피 데이터가 올바르지 않습니다.")
    if any(type(value) is not int or value < 1 for value in town_halls):
        raise ValueError("Clash 플레이어 마을회관 데이터가 올바르지 않습니다.")
    if any(type(value) is not int or not 1 <= value <= 50 for value in clan_members):
        raise ValueError("Clash 클랜 인원 데이터가 올바르지 않습니다.")

    sampled_at = datetime.now(UTC).isoformat(timespec="seconds")
    summary = {
        "sampled_at": sampled_at,
        "selection": "global_player_ranking_top_20_and_clan_ranking_top_3",
        "player_count": len(players),
        "clan_count": len(clans),
        "median_trophies": int(median_low(trophies)),
        "median_town_hall": int(median_low(town_halls)),
        "median_clan_members": int(median_low(clan_members)),
    }
    digest = hashlib.sha256(json.dumps(summary, sort_keys=True).encode()).hexdigest()[:12]
    return {
        "source": "clash_of_clans",
        "game_data_version": f"clash-sample-{digest}",
        "sample": summary,
    }
