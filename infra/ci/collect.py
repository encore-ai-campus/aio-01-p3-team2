"""Record each selected pytest parameter before execution for the evidence gate."""

import os
import subprocess
import sys

from infra.ci.gate import ROOT, read, write


def main():
    suite, *arguments = sys.argv[1:]
    collected = subprocess.check_output(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", *arguments], cwd=ROOT, text=True
    )
    selected = []
    for line in collected.splitlines():
        if "::" not in line or not line.split("::")[0].endswith(".py"):
            continue
        file, *parts = line.split("::")
        module = file.removesuffix(".py").replace("/", ".")
        selected.append([".".join([module, *parts[:-1]]), parts[-1]])
    if not selected:
        raise ValueError("Empty CI collection")
    target = ROOT / "test-results" / (suite + "-expected.json")
    previous = read(target) if target.exists() and os.getenv("CI_COLLECT_APPEND") == "true" else []
    write(target, previous + selected)


if __name__ == "__main__":
    main()
