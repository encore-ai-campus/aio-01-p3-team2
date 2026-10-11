"""Reviewed test inventory and deterministic execution profile selection."""

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INVENTORY = ROOT / "infra" / "test-suites.json"
PROFILES = {"core", "postgres", "compatibility", "external", "local"}


def load_inventory(path: Path = INVENTORY) -> dict[str, dict]:
    entries = json.loads(path.read_text(encoding="utf-8"))["python"]
    files = [entry["file"] for entry in entries]
    if len(files) != len(set(files)):
        raise ValueError("Duplicate test file in inventory")
    if any(entry["suite"] not in PROFILES - {"local"} for entry in entries):
        raise ValueError("Unknown test suite")
    return {entry["file"]: entry for entry in entries}


def included(suite: str, profile: str) -> bool:
    if profile not in PROFILES:
        raise ValueError("Unknown test profile")
    return suite in {"core", "postgres"} if profile == "local" else suite == profile


def validate_inventory(inventory: dict[str, dict], root: Path = ROOT) -> None:
    actual = {
        path.relative_to(root).as_posix()
        for directory in ("backend/tests", "mcp_server/tests", "demo-game/tests")
        for path in (root / directory).rglob("test_*.py")
    }
    if actual != set(inventory):
        raise ValueError(f"Unclassified/stale tests: {sorted(actual ^ set(inventory))}")
    if any((root / name).stat().st_size == 0 for name in actual):
        raise ValueError("Empty test placeholder")
    for name in actual:
        tree = ast.parse((root / name).read_text(encoding="utf-8-sig"))
        functions = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name.startswith("test_")
        }
        if functions != set(inventory[name]["test_functions"]):
            raise ValueError(f"Review new or changed test functions in {name}")
    javascript = json.loads((root / "infra/test-suites.json").read_text(encoding="utf-8"))[
        "javascript"
    ]
    registered = [entry["file"] for entry in javascript]
    actual_js = {
        path.relative_to(root).as_posix()
        for directory in ("frontend-web/e2e", "frontend-web/game-e2e", "frontend-web/tests")
        for pattern in ("*.spec.js", "*.test.js")
        for path in (root / directory).rglob(pattern)
    }
    if len(registered) != len(set(registered)) or actual_js != set(registered):
        raise ValueError("Unclassified/stale or duplicate JavaScript test file")
