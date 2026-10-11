"""Compute conservative deployment candidates; never authorize a cloud deployment."""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
import subprocess
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[2]
RULES = Path(__file__).with_name("change_rules.json")
ROLES = ("plan-web", "game-web", "main-api", "fault-api", "main-mcp", "fault-mcp")
SHA = re.compile(r"[0-9a-fA-F]{40}\Z")


def load_rules(path: Path = RULES) -> dict:
    rules = json.loads(path.read_text(encoding="utf-8"))
    if rules["version"] != 1 or rules["roles"] != list(ROLES):
        raise ValueError("Unsupported impact rules or role order")
    seen = set()
    for rule in rules["rules"]:
        if rule["id"] in seen or not rule["patterns"]:
            raise ValueError("Duplicate or empty change rule")
        if set(rule["targets"]) - set(ROLES):
            raise ValueError("Unknown deployment role")
        if not isinstance(rule["ci"], bool):
            raise TypeError("Invalid CI flag")
        seen.add(rule["id"])
    return rules


def classify(paths: list[str], *, full: bool = False, rules: dict | None = None) -> dict:
    """The first matching rule owns a path; unknown code requires manual review."""
    rules = rules or load_rules()
    targets = set(ROLES) if full else set()
    ci, migration, manual = full, full, False
    evidence = []
    for raw in sorted(set(paths)):
        path = raw.replace("\\", "/")
        parsed = PurePosixPath(path)
        if path.startswith("/") or ".." in parsed.parts or ":" in path or not path:
            raise ValueError("Invalid repository-relative change path")
        if any(p == ".env" or p.startswith(".env.") for p in parsed.parts) and not path.endswith(
            (".example", ".example.json")
        ):
            raise ValueError("Private environment files cannot enter a deployment plan")
        rule = next(
            (
                entry
                for entry in rules["rules"]
                if any(fnmatch.fnmatchcase(path, pattern) for pattern in entry["patterns"])
            ),
            None,
        )
        if rule is None:
            targets.update(ROLES)
            ci, manual = True, True
            evidence.append({"path": path, "rule": "unknown"})
            continue
        targets.update(rule["targets"])
        ci |= rule["ci"]
        migration |= rule.get("migration", False)
        manual |= rule.get("manual", False)
        evidence.append({"path": path, "rule": rule["id"]})
    selected = [role for role in ROLES if role in targets]
    return {
        "version": 1,
        "targets": selected,
        "images": [
            image
            for image, affected in (
                ("web", {"plan-web", "game-web"}),
                ("application", set(ROLES[2:])),
            )
            if targets & affected
        ],
        "web_sites": [site for site in ("plan", "game") if f"{site}-web" in targets],
        "ci_required": ci,
        "migration_required": migration,
        "manual_review_required": manual,
        "full_deployment": full,
        "evidence": evidence,
    }


def _git(*args: str, root: Path = ROOT) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, encoding="utf-8"
    ).stdout


def plan_from_git(base: str, head: str, *, baseline: str = "push", root: Path = ROOT) -> dict:
    """A deployed baseline is required later under the target deployment lock."""
    if not SHA.fullmatch(head) or (base and not SHA.fullmatch(base)):
        raise ValueError("Git revisions must be full hexadecimal commit IDs")
    if baseline not in {"push", "deployed"}:
        raise ValueError("Unknown baseline kind")
    _git("cat-file", "-e", f"{head}^{{commit}}", root=root)
    initial = not base or base == "0" * 40
    if initial:
        result = classify([], full=True)
    else:
        _git("cat-file", "-e", f"{base}^{{commit}}", root=root)
        ancestor = subprocess.run(
            ["git", "merge-base", "--is-ancestor", base, head],
            cwd=root,
            check=False,
            capture_output=True,
        )
        if ancestor.returncode:
            raise ValueError("Baseline is not an ancestor; manual reconciliation required")
        # --no-renames exposes both old/deleted and new paths for a moved file.
        changed = _git("diff", "--name-only", "--no-renames", "-z", base, head, root=root)
        result = classify([value for value in changed.split("\0") if value])
    result.update(
        base_sha=base or None,
        head_sha=head,
        baseline_kind=baseline,
        # CI candidates alone do not prove readiness, permissions, or image verification.
        deployment_authorized=False,
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="")
    parser.add_argument("--head", required=True)
    parser.add_argument("--baseline", choices=["push", "deployed"], default="push")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args()
    result = plan_from_git(args.base, args.head, baseline=args.baseline)
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    if args.github_output:
        with args.github_output.open("a", encoding="utf-8") as stream:
            for key in ("ci_required", "targets", "images", "web_sites", "manual_review_required"):
                stream.write(f"{key}={json.dumps(result[key], separators=(',', ':'))}\n")
    print(rendered, end="")


if __name__ == "__main__":
    main()
