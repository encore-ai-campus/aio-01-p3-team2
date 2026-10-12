#!/usr/bin/env bash
set -euo pipefail
mkdir -p test-results
python -m infra.ci.ui
python -m infra.ci.gate evidence --suite ui --reports test-results/ui-connected.xml test-results/ui-restart.xml test-results/ui-game.xml test-results/ui-fault-display.xml test-results/ui-mcp503.xml --output test-results/ui.json
