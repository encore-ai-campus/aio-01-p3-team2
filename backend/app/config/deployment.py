"""Deployment mode boundaries shared by repositories and migration commands."""

import os


def deployment_mode() -> str:
    mode = os.getenv("DEPLOYMENT_MODE", "local")
    if mode not in {"local", "cloud"}:
        raise RuntimeError("DEPLOYMENT_MODE must be local or cloud")
    return mode


def runtime_schema_creation_allowed() -> bool:
    """Cloud runtime never creates or repairs schema, even when explicitly requested."""
    return deployment_mode() == "local"
