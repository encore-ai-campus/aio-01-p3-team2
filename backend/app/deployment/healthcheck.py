"""Container readiness with no provider calls and no credential output."""

import os
import sys
import urllib.request


def main():
    role = os.getenv("SERVICE_ROLE", "")
    mcp = role.endswith("-mcp")
    port = 8010 if mcp else 8000
    request = urllib.request.Request(f"http://127.0.0.1:{port}/health/ready")
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            if response.status != 200:
                sys.exit(1)
    except Exception:  # noqa: BLE001 - Docker health output must never show secret config.
        sys.exit(1)


if __name__ == "__main__":
    main()
