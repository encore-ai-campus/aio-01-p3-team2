#!/bin/bash
set -euo pipefail
umask 077
exec python3 -m infra.aws.parameters "$@"
