"""Explicit first-time SecureString import; never rotate/overwrite existing keys."""

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path

from infra.aws.iac import PARAMETERS, PREFIX
from infra.aws.infrastructure import Aws, preflight, validate_config

OPERATIONS = {
    "operations/master-password",
    "operations/master-url",
    "operations/role-passwords",
    "operations/main-owner-url",
    "operations/fault-owner-url",
}
ALLOWED = set().union(*PARAMETERS.values(), OPERATIONS)


def validate_values(values):
    if not isinstance(values, dict) or not values or set(values) - ALLOWED:
        raise ValueError("Unknown or empty parameter input")
    if any(
        not isinstance(value, str)
        or not value
        or len(value.encode()) > 4096
        or any(char in value for char in "\r\n\0")
        for value in values.values()
    ):
        raise ValueError("Parameter values must be nonempty single-line Standard values")


def import_values(aws, config, values):
    preflight(aws, config)
    validate_values(values)
    # Check every selected name BEFORE importing anything. No WithDecryption needed.
    names = [PREFIX + name for name in values]
    for offset in range(0, len(names), 10):
        existing = aws(
            "ssm",
            "get-parameters",
            payload={"Names": names[offset : offset + 10], "WithDecryption": False},
        )
        if existing.get("Parameters"):
            raise ValueError("Existing parameters are preserved; select only missing names")
    report = []
    for name, value in sorted(values.items()):
        request = {
            "Name": PREFIX + name,
            "Value": value,
            "Type": "SecureString",
            "Tier": "Standard",
            "KeyId": "alias/aws/ssm",
            "Overwrite": False,
            "Tags": [
                {"Key": "Environment", "Value": "ikonly-demo"},
                {"Key": "ManagedBy", "Value": "ikonly-iac"},
            ],
        }
        # Avoid secret values in process argv. Owner-only local temporary file; unlink
        # on both success/failure. On Windows the user's TEMP ACL is inherited.
        fd, filename = tempfile.mkstemp(prefix="ikonly-parameter-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(json.dumps(request))
            result = subprocess.run(
                [
                    "aws",
                    "--region",
                    config["region"],
                    "--no-cli-pager",
                    "--output",
                    "json",
                    "ssm",
                    "put-parameter",
                    "--cli-input-json",
                    "file://" + filename,
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode:
                raise RuntimeError(
                    "Parameter import failed; preserve already-created parameters and inspect metadata"
                )
            report.append(
                {
                    "name": PREFIX + name,
                    "version": json.loads(result.stdout)["Version"],
                    "type": "SecureString",
                }
            )
        finally:
            os.unlink(filename)
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--values-file", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    config = validate_config(json.loads(args.config.read_text(encoding="utf-8")))
    source = args.values_file.resolve()
    ignored = subprocess.run(
        ["git", "check-ignore", "--quiet", str(source)], check=False, capture_output=True
    )
    if ignored.returncode:
        raise ValueError("Secret input must be a gitignored local file")
    values = json.loads(source.read_text(encoding="utf-8"))
    validate_values(values)
    if args.apply:
        print(json.dumps(import_values(Aws(config["region"]), config, values), indent=2))
    else:
        print(
            json.dumps(
                {"mode": "validation-only", "names": [PREFIX + name for name in sorted(values)]}
            )
        )


if __name__ == "__main__":
    main()
