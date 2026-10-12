#!/usr/bin/env bash
# Source in an ephemeral Linux job; the trap owns only this Compose project.
set -euo pipefail
mkdir -p test-results
export CI_DB_PASSWORD="$(python -c 'import secrets; print(secrets.token_hex(24))')"
export CI_DB_PORT="${CI_DB_PORT:-5434}"
export COMPOSE_PROJECT_NAME="ikonly-ci-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}-${CI_JOB_ROLE:?}"
cleanup_database() { docker compose -f infra/ci/compose.yml down --volumes; unset CI_DB_PASSWORD; }
trap cleanup_database EXIT
docker compose -f infra/ci/compose.yml up --detach --wait
export DATABASE_URL="postgresql+psycopg://eventquest_test:${CI_DB_PASSWORD}@127.0.0.1:${CI_DB_PORT}/eventquest_auto_test"
export POSTGRES_TEST_URL="$DATABASE_URL" MCP_DATABASE_URL="$DATABASE_URL" MCP_TEST_DATABASE_URL="$DATABASE_URL" MIGRATION_TEST_URL="$DATABASE_URL"
export WORKFLOW_ENGINE=mock CANONICAL_PLANNING_MODE=deterministic
export MCP_URL=' ' ENABLE_FAULT_INJECTION=true OPERATOR_ACTOR_ID=liveops01 APPROVED_ACTOR_IDS=liveops01
python -m backend.scripts.seed_game_runtime
python -c 'from backend.tests.conftest import align_isolated_test_policy; align_isolated_test_policy.__wrapped__()'
