"""Fail-closed entry for the four application image roles."""

import os
import sys

from backend.app.config.cloud_database import verify_cloud_database
from backend.app.config.production import validate_production_environment


def main() -> None:
    try:
        role = validate_production_environment()
        verify_cloud_database()
        if role == "main-api":
            from backend.app.services.llm_record_service import validate_live_recording

            validate_live_recording()
    except Exception:  # noqa: BLE001 - startup diagnostics must not disclose secrets.
        sys.exit("Production service configuration or database verification failed")
    module = "mcp_server.server" if role.endswith("-mcp") else "backend.app.main"
    os.execv(sys.executable, [sys.executable, "-m", module])


if __name__ == "__main__":
    main()
