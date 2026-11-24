#!/bin/sh
set -eu

project="microservices-pg-tests-$(date +%s)-$$"
export POSTGRES_TEST_PASSWORD="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"

cleanup() {
    docker compose -f docker-compose.test.yml -p "$project" down --volumes --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

docker compose -f docker-compose.test.yml -p "$project" up \
    --build \
    --abort-on-container-exit \
    --exit-code-from integration-tests \
    integration-tests