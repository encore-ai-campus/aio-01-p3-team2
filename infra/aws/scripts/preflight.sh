#!/bin/bash
set -euo pipefail
exec python3 -m infra.aws.infrastructure preflight "$@"
