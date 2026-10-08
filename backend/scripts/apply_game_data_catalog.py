"""Create and seed A-owned game data in the shared PostgreSQL database.

Run from a repository checkout with DATABASE_URL set. Existing data is never
overwritten automatically; changing a catalog version needs an explicit review.
"""

import json
import os
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = REPO_ROOT / "backend" / "sql" / "001_game_data_catalog.sql"
DATA_PATH = REPO_ROOT / "mcp_server" / "data" / "tiny_heroes.json"
GAME_ID = "tiny_heroes"


def _psycopg_url(database_url: str) -> str:
    if database_url.startswith("postgresql+psycopg://"):
        return "postgresql://" + database_url.removeprefix("postgresql+psycopg://")
    if database_url.startswith("postgresql://"):
        return database_url
    raise ValueError("PostgreSQL DATABASE_URL이 필요합니다.")


def apply_catalog(database_url: str, *, data_path: Path = DATA_PATH) -> str:
    data = json.loads(data_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise TypeError("게임 데이터는 JSON 객체여야 합니다.")
    version = data.get("game_data_version")
    if not isinstance(version, str) or not version.strip():
        raise ValueError("game_data_version이 필요합니다.")
    for key in ("contents", "items", "user_segments"):
        if not isinstance(data.get(key), dict) or not data[key]:
            raise ValueError(f"{key} 데이터가 필요합니다.")

    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
    with (
        psycopg.connect(_psycopg_url(database_url), connect_timeout=5) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(schema_sql)
        cursor.execute(
            "SELECT game_data_version, data FROM game_data_catalog WHERE game_id = %s",
            (GAME_ID,),
        )
        existing = cursor.fetchone()
        if existing is None:
            cursor.execute(
                "INSERT INTO game_data_catalog (game_id, game_data_version, data) "
                "VALUES (%s, %s, %s)",
                (GAME_ID, version, Jsonb(data)),
            )
        elif existing[0] != version or existing[1] != data:
            raise ValueError(
                "공용 DB의 게임 데이터가 초기값과 다릅니다. 변경 내용을 검토한 뒤 적용하세요."
            )
    return version


if __name__ == "__main__":
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        raise SystemExit("DATABASE_URL을 설정하세요.")
    applied_version = apply_catalog(url)
    print(f"game_data_catalog 준비 완료: {GAME_ID}, {applied_version}")
