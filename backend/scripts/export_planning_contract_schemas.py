"""Export versioned JSON Schemas from the canonical Pydantic contracts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from backend.app.validation.planning_contracts import CONTRACT_MODELS, SCHEMA_DIR


def export_schemas(*, check: bool = False) -> list[str]:
    SCHEMA_DIR.mkdir(parents=True, exist_ok=True)
    changed: list[str] = []
    for version, model in CONTRACT_MODELS.items():
        schema = model.model_json_schema(mode="validation")
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        schema["$id"] = f"https://eventquest.local/schemas/{version}"
        rendered = json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        path = SCHEMA_DIR / f"{version.replace('.', '-')}.schema.json"
        if not path.exists() or path.read_text(encoding="utf-8") != rendered:
            changed.append(str(path.relative_to(Path.cwd())))
            if not check:
                path.write_text(rendered, encoding="utf-8")
    return changed


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    changed_paths = export_schemas(check=args.check)
    if args.check and changed_paths:
        raise SystemExit("Schema files are stale: " + ", ".join(changed_paths))
    print(
        "Contract schemas are current."
        if not changed_paths
        else "Exported: " + ", ".join(changed_paths)
    )
