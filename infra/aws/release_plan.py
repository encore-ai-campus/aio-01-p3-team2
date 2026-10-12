"""Plan first or selective updates from each service's deployed Git baseline."""

import argparse
import json
from pathlib import Path

from infra.aws.contracts import ReleaseManifest
from infra.aws.deployment_impact import ROLES, plan_from_git


def build(candidate, previous=None, *, full=False):
    candidate = ReleaseManifest.model_validate(candidate)
    head = candidate.source_sha
    if any(service.source_sha != head for service in candidate.services.values()):
        raise ValueError("Candidate must contain verified images of its exact source revision")
    if previous is None:
        targets = list(ROLES)
        previous_id = None
        evidence = {}
        migration = True
        manual = False
    else:
        previous = ReleaseManifest.model_validate(previous)
        previous_id = previous.source_sha
        evidence = {
            role: plan_from_git(service.source_sha, head, baseline="deployed")
            for role, service in previous.services.items()
        }
        targets = [role for role in ROLES if full or role in evidence[role]["targets"]]
        migration = any(item["migration_required"] for item in evidence.values())
        manual = any(item["manual_review_required"] for item in evidence.values())
        # Shared DB/contracts must not leave incompatible retained services.
        changed = any(
            getattr(previous, key) != getattr(candidate, key)
            for key in ("schema_checksum", "migration_checksum", "catalog_checksum")
        )
        if changed or migration:
            targets = list(ROLES)
        for pair in (("main-api", "fault-api"), ("main-mcp", "fault-mcp")):
            if set(pair) & set(targets):
                targets = [role for role in ROLES if role in set(targets) | set(pair)]
    release = candidate.model_dump()
    release["previous_release_id"] = previous_id
    if previous is not None:
        for role in ROLES:
            if role not in targets:
                release["services"][role] = previous.services[role].model_dump()
    release = ReleaseManifest.model_validate(release).model_dump()
    return {
        "version": 1,
        "operation": "initial" if previous is None else "update" if targets else "no-op",
        "targets": targets,
        "retained_targets": [role for role in ROLES if role not in targets],
        "migration_required": migration,
        "manual_review_required": manual,
        "deployment_authorized": False,
        "evidence": evidence,
        "release": release,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--previous", type=Path)
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = build(
        json.loads(args.candidate.read_text(encoding="utf-8")),
        json.loads(args.previous.read_text(encoding="utf-8")) if args.previous else None,
        full=args.full,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                key: result[key]
                for key in ("operation", "targets", "retained_targets", "manual_review_required")
            }
        )
    )


if __name__ == "__main__":
    main()
