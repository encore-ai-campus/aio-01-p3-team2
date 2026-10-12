#!/bin/bash
set -euo pipefail
exec python3 -m infra.aws.export_environment "$@"
