"""Fail-closed CI evidence and image promotion contracts; no AWS credentials."""

import argparse
import hashlib
import json
import os
import re
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
JOBS = {"changes", "quality-core", "postgres", "ui", "build-images", "verify-images"}
GROUPS = {
    "schema": ("backend/app/schemas", "backend/app/game/schemas.py"),
    "migration": ("backend/sql", "backend/app/deployment/sql"),
    "catalog": ("backend/data", "mcp_server/data"),
}


def digest(path):
    with Path(path).open("rb") as stream:
        result = hashlib.file_digest(stream, "sha256").hexdigest()
    return "sha256:" + result


def identity():
    sha, run, attempt = (
        os.environ[name] for name in ("GITHUB_SHA", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT")
    )
    if not re.fullmatch(r"[a-f0-9]{40}", sha) or not run.isdecimal() or int(attempt) < 1:
        raise ValueError("Invalid CI identity")
    return {"source_sha": sha, "ci_run_id": run, "ci_attempt": int(attempt)}


def fingerprints(root=ROOT):
    result = {}
    for group, locations in GROUPS.items():
        files = []
        for location in locations:
            path = root / location
            if path.is_file():
                files.append(path)
            elif path.is_dir():
                files.extend(
                    p
                    for p in path.rglob("*")
                    if p.is_file() and p.suffix in {".py", ".sql", ".json"}
                )
        if not files:
            raise ValueError("Empty source fingerprint group: " + group)
        entries = {p.relative_to(root).as_posix(): digest(p) for p in sorted(set(files))}
        result[group] = (
            "sha256:" + hashlib.sha256(json.dumps(entries, sort_keys=True).encode()).hexdigest()
        )
    return result


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def report_cases(path, *, core=False):
    cases = ET.parse(path).findall(".//testcase")
    if not cases:
        raise ValueError("Missing executed test cases: " + str(path))
    passed = set()
    for case in cases:
        key = (case.get("classname", ""), case.get("name", ""))
        if case.find("failure") is not None or case.find("error") is not None:
            raise ValueError("Failed test evidence")
        if case.find("skipped") is not None:
            if not (
                core
                and key
                == (
                    "backend.tests.test_repository",
                    "test_task_and_trace_share_atomic_rollback[<lambda>1]",
                )
            ):
                raise ValueError("Required test skipped: " + repr(key))
        else:
            passed.add(key)
    return passed


def evidence(suite, paths, output):
    passed = set()
    for path in paths:
        passed |= report_cases(path, core=suite == "quality-core")
    inventory = read(ROOT / "infra/test-suites.json")["python"]
    if suite in {"quality-core", "postgres", "verify-images"}:
        expected = {
            (entry["file"].removesuffix(".py").replace("/", "."), function)
            for entry in inventory
            if (entry["suite"] == ("core" if suite == "quality-core" else "postgres"))
            and (
                (entry["file"].endswith("test_production_images.py")) == (suite == "verify-images")
                or suite == "quality-core"
            )
            for function in entry["test_functions"]
        }
        actual = {
            (expected_module, name.split("[")[0])
            for module, name in passed
            for expected_module, _ in expected
            if module == expected_module or module.startswith(expected_module + ".")
        }
        if not expected <= actual:
            raise ValueError("Required test functions missing: " + repr(sorted(expected - actual)))
        if (
            suite == "postgres"
            and (
                "backend.tests.test_repository",
                "test_task_and_trace_share_atomic_rollback[<lambda>1]",
            )
            not in passed
        ):
            raise ValueError("Core PostgreSQL parameter was not executed")
    elif suite == "ui":
        # List-mode creates the expected set before execution, including loop-generated titles.
        expected = set(read(ROOT / "test-results/ui-expected.json"))
        if not expected or not expected <= {name for _, name in passed}:
            raise ValueError("UI test selection is incomplete")
    else:
        raise ValueError("Unknown evidence suite")
    extra = {}
    if suite != "ui":
        selected = {tuple(item) for item in read(Path(output).parent / (suite + "-expected.json"))}
        permitted = (
            {
                (
                    "backend.tests.test_repository",
                    "test_task_and_trace_share_atomic_rollback[<lambda>1]",
                )
            }
            if suite == "quality-core"
            else set()
        )
        if not selected or not selected <= passed | permitted:
            raise ValueError("Required parameterized cases missing")
    if suite == "verify-images":
        titles = {
            "production planning form reads actual catalog through authenticated HTTPS",
            "production policy and isolated recovery UI use separate real APIs",
            "production game renders Phaser and authoritative persisted state",
        }
        if not titles <= {name for _, name in passed}:
            raise ValueError("Production browser cases missing")
        extra["candidate_hash"] = digest(Path(output).parent / "candidate.json")
    write(
        output,
        {
            "version": 1,
            **identity(),
            "suite": suite,
            "reports": {Path(p).name: digest(p) for p in paths},
            "passed_cases": len(passed),
            **extra,
        },
    )


def bundle(directory):
    directory = Path(directory)
    images = {}
    for kind in ("application", "web"):
        tag = f"ikonly-{kind}:ci"
        metadata = json.loads(subprocess.check_output(["docker", "image", "inspect", tag]))[0]
        if (
            metadata["Config"]["Labels"]["org.opencontainers.image.revision"]
            != identity()["source_sha"]
        ):
            raise ValueError("Image source SHA mismatch")
        images[kind] = {
            "tag": tag,
            "image_id": metadata["Id"],
            "tar": kind + ".tar",
            "tar_hash": digest(directory / (kind + ".tar")),
        }
    write(
        directory / "candidate.json",
        {"version": 1, **identity(), "fingerprints": fingerprints(), "images": images},
    )


def verify_bundle(directory, *, load=False):
    directory = Path(directory)
    candidate = read(directory / "candidate.json")
    if any(candidate.get(k) != v for k, v in identity().items()) or candidate.get("version") != 1:
        raise ValueError("Candidate belongs to another SHA/run/attempt")
    if candidate.get("fingerprints") != fingerprints() or set(candidate.get("images", {})) != {
        "application",
        "web",
    }:
        raise ValueError("Candidate source contracts or images differ")
    for kind, image in candidate["images"].items():
        if image.get("tar") != kind + ".tar" or image.get("tag") != f"ikonly-{kind}:ci":
            raise ValueError("Unexpected artifact path or image tag")
        if digest(directory / image["tar"]) != image["tar_hash"]:
            raise ValueError("Image tar hash mismatch")
        if load:
            subprocess.run(["docker", "load", "--input", str(directory / image["tar"])], check=True)
            metadata = json.loads(
                subprocess.check_output(["docker", "image", "inspect", image["tag"]])
            )[0]
            if (
                metadata["Id"] != image["image_id"]
                or metadata["Config"]["Labels"]["org.opencontainers.image.revision"]
                != candidate["source_sha"]
            ):
                raise ValueError("Loaded image identity mismatch")
    return candidate


def approve(directory, results, output):
    if set(results) != JOBS or any(value.get("result") != "success" for value in results.values()):
        raise ValueError(
            "Every required CI job must succeed; missing/skip/failure forbids promotion"
        )
    candidate = verify_bundle(directory)
    directory = Path(directory)
    for suite in ("quality-core", "postgres", "ui", "verify-images"):
        receipt = read(directory / (suite + ".json"))
        if receipt.get("suite") != suite or any(receipt.get(k) != v for k, v in identity().items()):
            raise ValueError("Evidence identity mismatch")
        if not receipt.get("passed_cases") or not receipt.get("reports"):
            raise ValueError("Empty gate evidence")
        if suite == "verify-images" and receipt.get("candidate_hash") != digest(
            directory / "candidate.json"
        ):
            raise ValueError("Verified candidate changed after image tests")
        for name, expected in receipt["reports"].items():
            if Path(name).name != name or digest(directory / name) != expected:
                raise ValueError("Test report hash mismatch")
    impact = read(directory / "deployment-impact.json")
    if impact.get("head_sha") != candidate["source_sha"] or impact.get("ci_required") is not True:
        raise ValueError("Impact baseline identity mismatch")
    write(
        output,
        {
            **candidate,
            "ci_verified": True,
            "deployment_authorized": False,
            "manual_review_required": impact["manual_review_required"],
            "targets": impact["targets"],
            "evidence": {p.name: digest(p) for p in directory.glob("*.xml")},
        },
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["evidence", "bundle", "verify", "approve"])
    parser.add_argument("--directory", type=Path, default=Path("test-results"))
    parser.add_argument("--suite")
    parser.add_argument("--reports", nargs="+", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--load", action="store_true")
    args = parser.parse_args()
    if args.command == "evidence":
        evidence(args.suite, args.reports, args.output)
    elif args.command == "bundle":
        bundle(args.directory)
    elif args.command == "verify":
        verify_bundle(args.directory, load=args.load)
    else:
        approve(args.directory, json.loads(os.environ["NEEDS_JSON"]), args.output)


if __name__ == "__main__":
    main()
