#!/usr/bin/env bash
set -euo pipefail
python -m infra.ci.gate verify --directory test-results --load
export CLOUD_DATABASE_TESTS=true CLOUD_IMAGE_TESTS=true CLOUD_BROWSER_TESTS=true
export APPLICATION_TEST_IMAGE=ikonly-application:ci WEB_TEST_IMAGE=ikonly-web:ci
python -m infra.ci.collect verify-images backend/tests/integration/test_production_images.py --test-profile postgres
python -m pytest backend/tests/integration/test_production_images.py --test-profile postgres -q --junitxml=test-results/images.xml
python -m infra.ci.gate evidence --suite verify-images --reports test-results/images.xml test-results/production-browser.xml --output test-results/verify-images.json
