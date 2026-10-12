"""Versioned non-secret deployment manifests and their JSON Schema exports."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

from infra.aws.deployment_impact import ROLES
from pydantic import BaseModel, ConfigDict, Field, model_validator

Sha = Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
Digest = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
Role = Literal["plan-web", "game-web", "main-api", "fault-api", "main-mcp", "fault-mcp"]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal[1] = 1


class Resource(Contract):
    id: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    owner_stack: str = Field(min_length=1)
    environment_id: str = Field(min_length=1)
    retain: bool


class EnvironmentManifest(Contract):
    account_id: str = Field(pattern=r"^[0-9]{12}$")
    region: Literal["ap-northeast-2"]
    environment_id: Literal["ikonly-demo"]
    roles: dict[Role, str]
    resources: list[Resource]
    parameter_names: dict[str, str]
    termination_mode: Literal["manual", "scheduled"] = "scheduled"
    stop_after: datetime | None = None

    @model_validator(mode="after")
    def check_scope(self):
        if set(self.roles) != set(ROLES) or len(set(self.roles.values())) != len(ROLES):
            raise ValueError("Every service needs its own instance ID")
        if any(not value.startswith("i-") for value in self.roles.values()):
            raise ValueError("Role values must be EC2 instance IDs")
        ids = [item.id for item in self.resources]
        keys = [(item.kind, item.id) for item in self.resources]
        if len(keys) != len(set(keys)) or not set(self.roles.values()).issubset(ids):
            raise ValueError("Resources must include each unique role instance")
        if any(item.environment_id != self.environment_id for item in self.resources):
            raise ValueError("Resources from another environment are forbidden")
        prefix = "/multi-agent-autohealing/ikonly-demo/"
        if any(not value.startswith(prefix) for value in self.parameter_names.values()):
            raise ValueError("Parameter names must use the environment prefix")
        if self.termination_mode == "manual" and self.stop_after is not None:
            raise ValueError("Manual termination must not imply a scheduled stop time")
        if self.termination_mode == "scheduled" and self.stop_after is None:
            raise ValueError("Scheduled termination needs a stop time")
        if self.stop_after is not None and self.stop_after.tzinfo is None:
            raise ValueError("The stop time needs an explicit timezone")
        return self


class ServiceRelease(Contract):
    image: Literal["web", "application"]
    digest: Digest
    source_sha: Sha
    deployment_id: Sha | None = None


class ReleaseManifest(Contract):
    environment_id: Literal["ikonly-demo"]
    source_sha: Sha
    release_id: Sha | None = None
    ci_run_id: str = Field(pattern=r"^[0-9]+$")
    ci_attempt: int = Field(ge=1)
    services: dict[Role, ServiceRelease]
    previous_release_id: str | None
    schema_checksum: Digest
    migration_checksum: Digest
    catalog_checksum: Digest

    @model_validator(mode="after")
    def check_services(self):
        if set(self.services) != set(ROLES):
            raise ValueError("Release must include changed and retained service digests")
        for role, service in self.services.items():
            expected = "web" if role.endswith("-web") else "application"
            if service.image != expected:
                raise ValueError("Image kind does not match the service role")
        for roles in (("main-api", "fault-api"), ("main-mcp", "fault-mcp")):
            if self.services[roles[0]].digest != self.services[roles[1]].digest:
                raise ValueError("Ordinary and fault pairs must use the same code image")
        return self


class CleanupPlan(Contract):
    account_id: str = Field(pattern=r"^[0-9]{12}$")
    region: Literal["ap-northeast-2"]
    environment_id: Literal["ikonly-demo"]
    mode: Literal["cleanup-keep-data", "purge-data"]
    delete: list[Resource]
    retain: list[Resource]
    environment_manifest_checksum: Digest

    @model_validator(mode="after")
    def check_resources(self):
        resources = self.delete + self.retain
        ids = [(item.kind, item.id) for item in resources]
        if len(ids) != len(set(ids)):
            raise ValueError("Delete and retain lists must be unique and disjoint")
        if any(item.environment_id != self.environment_id for item in resources):
            raise ValueError("Cleanup may only affect its own environment")
        if self.mode == "cleanup-keep-data" and any(item.retain for item in self.delete):
            raise ValueError("Protected data cannot be deleted by keep-data cleanup")
        return self


MODELS = {"environment": EnvironmentManifest, "release": ReleaseManifest, "cleanup": CleanupPlan}
SCHEMAS = Path(__file__).with_name("schemas")


def export(*, check: bool = False) -> list[str]:
    stale = []
    for name, model in MODELS.items():
        schema = model.model_json_schema()
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        schema["$id"] = f"urn:eventquest:deployment:{name}:v1"
        value = json.dumps(schema, indent=2, sort_keys=True) + "\n"
        target = SCHEMAS / f"{name}.schema.json"
        if not target.exists() or target.read_text(encoding="utf-8") != value:
            stale.append(name)
            if not check:
                SCHEMAS.mkdir(parents=True, exist_ok=True)
                target.write_text(value, encoding="utf-8")
    return stale


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--validate", choices=list(MODELS))
    parser.add_argument("--file", type=Path)
    args = parser.parse_args()
    if args.validate:
        if args.file is None:
            parser.error("--validate requires --file")
        MODELS[args.validate].model_validate_json(args.file.read_text(encoding="utf-8"))
        print("Manifest is valid")
    else:
        stale = export(check=args.check)
        if args.check and stale:
            raise SystemExit("Deployment schemas are stale: " + ", ".join(stale))
        print("Deployment schemas are current")
