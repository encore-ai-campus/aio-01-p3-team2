"""Publish only successful main CI tar artifacts; never rebuild during deployment."""

import argparse
import hashlib
import json
import os
import subprocess
import tarfile
import tempfile
from pathlib import Path

from infra.aws.contracts import ReleaseManifest
from infra.aws.host_release import run
from infra.aws.infrastructure import Aws, preflight, validate_config
from infra.ci.gate import JOBS, digest, fingerprints


def archive_identity(path, metadata, source_sha):
    """Docker engines expose config or OCI manifest IDs; bind both to the same tar."""
    with tarfile.open(path) as archive:
        entries = json.load(archive.extractfile("manifest.json"))
        if len(entries) != 1 or entries[0].get("RepoTags") != [metadata["tag"]]:
            raise ValueError("Ambiguous archived image")
        raw = archive.extractfile(entries[0]["Config"]).read()
        config = json.loads(raw)
        if (
            "sha256:" + hashlib.sha256(raw).hexdigest() != metadata["image_id"]
            or config["config"]["Labels"].get("org.opencontainers.image.revision") != source_sha
            or config.get("os") != "linux"
            or config.get("architecture") != "amd64"
        ):
            raise ValueError("Archived image config identity mismatch")
        identifiers = {metadata["image_id"]}
        if "index.json" in archive.getnames():
            index = json.load(archive.extractfile("index.json"))
            if len(index["manifests"]) != 1:
                raise ValueError("Ambiguous OCI archive")
            reference = index["manifests"][0]["digest"]
            raw_manifest = archive.extractfile("blobs/sha256/" + reference.split(":")[1]).read()
            if (
                "sha256:" + hashlib.sha256(raw_manifest).hexdigest() != reference
                or json.loads(raw_manifest)["config"]["digest"] != metadata["image_id"]
            ):
                raise ValueError("Archived OCI manifest identity mismatch")
            identifiers.add(reference)
        return identifiers


def verify(directory, run_metadata, jobs, *, allow_current_run=False):
    directory = Path(directory)
    candidate = json.loads((directory / "candidate.json").read_text())
    approved = json.loads((directory / "ci-verified.json").read_text())
    current = (
        allow_current_run
        and os.getenv("GITHUB_ACTIONS") == "true"
        and os.getenv("GITHUB_JOB") == "deploy"
        and os.getenv("GITHUB_REF") == "refs/heads/main"
        and os.getenv("GITHUB_REPOSITORY") == "ik-e/multi-agent-autohealing"
        and os.getenv("GITHUB_RUN_ID") == str(run_metadata.get("id"))
        and os.getenv("GITHUB_SHA") == run_metadata.get("head_sha")
        and run_metadata.get("status") == "in_progress"
    )
    completed = {j["name"]: j["conclusion"] for j in jobs}
    failed_deploy_only = (
        run_metadata.get("status") == "completed"
        and run_metadata.get("conclusion") == "failure"
        and all(completed.get(name) == "success" for name in JOBS | {"release-gate"})
        and {name for name, result in completed.items() if result == "failure"} == {"deploy"}
    )
    if (
        run_metadata.get("head_branch") != "main"
        or run_metadata.get("event") not in ("push", "workflow_dispatch")
        or run_metadata.get("path") != ".github/workflows/ikonly-ci.yml"
        or (
            not current
            and (
                run_metadata.get("status") != "completed"
                or (run_metadata.get("conclusion") != "success" and not failed_deploy_only)
            )
        )
        or run_metadata.get("head_repository", {}).get("full_name")
        != "ik-e/multi-agent-autohealing"
        or candidate.get("source_sha") != run_metadata.get("head_sha")
        or candidate.get("ci_run_id") != str(run_metadata.get("id"))
        or candidate.get("ci_attempt") != run_metadata.get("run_attempt")
        or approved.get("ci_verified") is not True
        or any(approved.get(k) != v for k, v in candidate.items())
    ):
        raise ValueError("Trusted CI provenance or approval mismatch")
    if any(completed.get(name) != "success" for name in JOBS | {"release-gate"}):
        raise ValueError("Required remote CI job is not successful")
    with tempfile.TemporaryDirectory(prefix="ikonly-source-") as temporary:
        archive = Path(temporary) / "source.tar"
        subprocess.run(
            [
                "git",
                "-c",
                "core.autocrlf=false",
                "-c",
                "core.eol=lf",
                "archive",
                "--format=tar",
                "--output",
                str(archive),
                candidate["source_sha"],
                "backend/app/schemas",
                "backend/app/game/schemas.py",
                "backend/sql",
                "backend/app/deployment/sql",
                "backend/data",
                "mcp_server/data",
            ],
            check=True,
            capture_output=True,
        )
        with tarfile.open(archive) as source:
            source.extractall(temporary, filter="data")
        if candidate.get("fingerprints") != fingerprints(Path(temporary)):
            raise ValueError("CI source contract checksums differ from the exact Git revision")
    if set(candidate.get("images", {})) != {"application", "web"}:
        raise ValueError("Missing CI images")
    for kind, metadata in candidate["images"].items():
        if (
            metadata.get("tar") != kind + ".tar"
            or metadata.get("tag") != f"ikonly-{kind}:ci"
            or digest(directory / metadata["tar"]) != metadata["tar_hash"]
        ):
            raise ValueError("Image artifact path/hash mismatch")
    return candidate


def publish(config, directory, run_id, output, *, allow_current_run=False):
    aws = Aws(config["region"])
    preflight(aws, config)
    metadata = json.loads(
        run(["gh", "api", f"repos/ik-e/multi-agent-autohealing/actions/runs/{run_id}"])
    )
    jobs = json.loads(
        run(
            [
                "gh",
                "api",
                f"repos/ik-e/multi-agent-autohealing/actions/runs/{run_id}/attempts/{metadata['run_attempt']}/jobs",
                "--paginate",
                "--slurp",
            ]
        )
    )
    candidate = verify(
        directory,
        metadata,
        [job for page in jobs for job in page["jobs"]],
        allow_current_run=allow_current_run,
    )
    registry = config["account_id"] + ".dkr.ecr.ap-northeast-2.amazonaws.com"
    token = run(["aws", "--region", config["region"], "ecr", "get-login-password"])
    digests = {}
    try:
        run(
            ["docker", "login", "--username", "AWS", "--password-stdin", registry], input_text=token
        )
        for kind, image in candidate["images"].items():
            identifiers = archive_identity(
                Path(directory) / image["tar"], image, candidate["source_sha"]
            )
            existing = aws(
                "ecr",
                "batch-get-image",
                "--repository-name",
                "ikonly-demo/" + kind,
                "--image-ids",
                "imageTag=" + candidate["source_sha"],
            )
            if existing.get("images"):
                if len(existing["images"]) != 1:
                    raise ValueError("Existing immutable image tag is ambiguous")
                entry = existing["images"][0]
                manifest = json.loads(entry["imageManifest"])
                if manifest.get("config", {}).get("digest") != image["image_id"]:
                    raise ValueError(
                        "Existing immutable tag differs from this exact CI archive; use a new commit"
                    )
                digests[kind] = entry["imageId"]["imageDigest"]
                run(["docker", "load", "--input", str(Path(directory) / image["tar"])])
                continue
            if any(f.get("failureCode") != "ImageNotFound" for f in existing.get("failures", [])):
                raise ValueError("Image registry lookup failed")
            run(["docker", "load", "--input", str(Path(directory) / image["tar"])])
            loaded = json.loads(run(["docker", "image", "inspect", image["tag"]]))[0]
            if (
                loaded["Id"] not in identifiers
                or loaded["Config"]["Labels"].get("org.opencontainers.image.revision")
                != candidate["source_sha"]
            ):
                raise ValueError("Loaded CI image identity mismatch")
            tag = registry + "/ikonly-demo/" + kind + ":" + candidate["source_sha"]
            run(["docker", "tag", image["tag"], tag])
            run(["docker", "push", tag])
            details = aws(
                "ecr",
                "describe-images",
                "--repository-name",
                "ikonly-demo/" + kind,
                "--image-ids",
                "imageTag=" + candidate["source_sha"],
            )["imageDetails"]
            if len(details) != 1:
                raise ValueError("Published image digest is ambiguous")
            digests[kind] = details[0]["imageDigest"]
    finally:
        run(["docker", "logout", registry])
    release = ReleaseManifest.model_validate(
        {
            "environment_id": "ikonly-demo",
            "source_sha": candidate["source_sha"],
            "ci_run_id": candidate["ci_run_id"],
            "ci_attempt": candidate["ci_attempt"],
            "services": {
                role: {
                    "image": kind,
                    "digest": digests[kind],
                    "source_sha": candidate["source_sha"],
                }
                for kind, roles in {
                    "web": ("plan-web", "game-web"),
                    "application": ("main-api", "fault-api", "main-mcp", "fault-mcp"),
                }.items()
                for role in roles
            },
            "previous_release_id": None,
            **{key + "_checksum": value for key, value in candidate["fingerprints"].items()},
        }
    )
    raw = json.dumps(release.model_dump(mode="json"), indent=2).encode()
    Path(output).write_bytes(raw)
    print(
        "Verified CI images published; release manifest SHA256: " + hashlib.sha256(raw).hexdigest()
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    publish(
        validate_config(json.loads(args.config.read_text())),
        args.directory,
        args.run_id,
        args.output,
    )


if __name__ == "__main__":
    main()
