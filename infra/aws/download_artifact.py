"""Download CI artifacts in bounded, resumable ranges; verify GitHub ZIP digest."""

import argparse
import hashlib
import json
import time
import urllib.error
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from infra.aws.host_release import run
from infra.aws.operation_state import write

REPO = "ik-e/multi-agent-autohealing"
CHUNK = 8 * 1024 * 1024


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def signed_url(identifier):
    token = run(["gh", "auth", "token"])
    req = urllib.request.Request(
        f"https://api.github.com/repos/{REPO}/actions/artifacts/{identifier}/zip",
        headers={"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json"},
    )
    try:
        urllib.request.build_opener(NoRedirect()).open(req, timeout=30)
    except urllib.error.HTTPError as exc:
        if exc.code == 302 and exc.headers.get("Location", "").startswith("https://"):
            return exc.headers["Location"]
        raise RuntimeError("Artifact redirect request failed") from None
    raise ValueError("Expected artifact redirect")


def download(identifier, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    metadata = json.loads(run(["gh", "api", f"repos/{REPO}/actions/artifacts/{identifier}"]))
    if metadata["expired"] or not metadata.get("digest", "").startswith("sha256:"):
        raise ValueError("Artifact expired or lacks authoritative digest")
    size = metadata["size_in_bytes"]
    pieces = output / ("artifact-" + str(identifier) + "-parts")
    pieces.mkdir(exist_ok=True)
    jobs = list(range((size + CHUNK - 1) // CHUNK))
    url = signed_url(identifier)

    def fetch(number):
        start, end = number * CHUNK, min(size, (number + 1) * CHUNK) - 1
        target = pieces / str(number)
        if target.exists() and target.stat().st_size == end - start + 1:
            return
        for attempt in range(5):
            try:
                req = urllib.request.Request(
                    url if attempt == 0 else signed_url(identifier),
                    headers={"Range": f"bytes={start}-{end}"},
                )
                with urllib.request.urlopen(req, timeout=30) as response:
                    if (
                        response.status != 206
                        or response.headers.get("Content-Range") != f"bytes {start}-{end}/{size}"
                    ):
                        raise ValueError("Artifact range response mismatch")
                    data = response.read(CHUNK + 1)
                if len(data) != end - start + 1:
                    raise ValueError("Artifact range truncated")
                temporary = target.with_suffix(".tmp")
                temporary.write_bytes(data)
                temporary.replace(target)
                return
            except (OSError, ValueError):
                if attempt == 4:
                    raise RuntimeError(
                        "Artifact range download exhausted; rerun to resume"
                    ) from None
                time.sleep(2)

    with ThreadPoolExecutor(max_workers=4) as pool:
        for number, _ in enumerate(pool.map(fetch, jobs), 1):
            print(f"Artifact {identifier}: {number}/{len(jobs)} ranges verified", flush=True)
    archive = output / ("artifact-" + str(identifier) + ".zip")
    digest = hashlib.sha256()
    with archive.open("wb") as handle:
        for number in jobs:
            data = (pieces / str(number)).read_bytes()
            digest.update(data)
            handle.write(data)
    if "sha256:" + digest.hexdigest() != metadata["digest"]:
        raise ValueError("Artifact ZIP digest mismatch; remove recorded pieces before retry")
    with zipfile.ZipFile(archive) as source:
        for name in source.namelist():
            if not (output / name).resolve().is_relative_to(output.resolve()):
                raise ValueError("Artifact archive path escapes output")
        source.extractall(output)
    write(
        output / ("artifact-" + str(identifier) + "-verified.json"),
        {
            "id": identifier,
            "name": metadata["name"],
            "bytes": size,
            "digest": metadata["digest"],
        },
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-id", required=True, type=int)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    download(args.artifact_id, args.output)


if __name__ == "__main__":
    main()
