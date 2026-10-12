#!/usr/bin/env bash
set -euo pipefail
mkdir -p test-results
case "${1:?core or postgres required}" in
  core)
    unset DATABASE_URL POSTGRES_TEST_URL MCP_DATABASE_URL MCP_TEST_DATABASE_URL MCP_TEST_URL MIGRATION_TEST_URL
    python -m ruff check .
    python -m ruff format --check .
    python -m backend.scripts.export_planning_contract_schemas --check
    python -m infra.aws.contracts --check
    python -m infra.ci.collect quality-core --test-profile core
    python -m pytest --test-profile core -q --junitxml=test-results/core.xml
    python -m infra.ci.gate evidence --suite quality-core --reports test-results/core.xml --output test-results/quality-core.json
    ;;
  postgres)
    export CLOUD_DATABASE_TESTS=true
    unset CLOUD_IMAGE_TESTS
    python -m infra.ci.collect postgres --test-profile postgres --ignore=backend/tests/integration/test_production_images.py
    CI_COLLECT_APPEND=true python -m infra.ci.collect postgres --test-profile local backend/tests/test_repository.py -k task_and_trace_share_atomic_rollback
    python -m pytest --test-profile postgres -q --ignore=backend/tests/integration/test_production_images.py --junitxml=test-results/postgres.xml
    python -m pytest --test-profile local backend/tests/test_repository.py -k task_and_trace_share_atomic_rollback -q --junitxml=test-results/core-pg.xml
    python -m infra.ci.gate evidence --suite postgres --reports test-results/postgres.xml test-results/core-pg.xml --output test-results/postgres.json
    ;;
  *) exit 2 ;;
esac
