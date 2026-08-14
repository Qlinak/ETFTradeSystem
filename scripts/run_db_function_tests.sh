#!/usr/bin/env bash
set -euo pipefail

DB_CONTAINER="etf-postgres"
DB_USER="etf_user"
TEST_DB="etf_system_test"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if ! docker ps --format '{{.Names}}' | grep -q "^${DB_CONTAINER}$"; then
  echo "PostgreSQL container ${DB_CONTAINER} is not running. Start it first with:"
  echo "  docker compose up -d"
  exit 1
fi

echo "Recreating test database ${TEST_DB}..."
docker exec -i "${DB_CONTAINER}" psql -U "${DB_USER}" -d postgres -v ON_ERROR_STOP=1 -c "DROP DATABASE IF EXISTS ${TEST_DB} WITH (FORCE);"
docker exec -i "${DB_CONTAINER}" psql -U "${DB_USER}" -d postgres -v ON_ERROR_STOP=1 -c "CREATE DATABASE ${TEST_DB};"

echo "Applying schema.sql to ${TEST_DB}..."
docker exec -i "${DB_CONTAINER}" psql -U "${DB_USER}" -d "${TEST_DB}" -v ON_ERROR_STOP=1 < "${ROOT_DIR}/schema.sql"

echo "Running DB function test cases..."
docker exec -i "${DB_CONTAINER}" psql -U "${DB_USER}" -d "${TEST_DB}" -v ON_ERROR_STOP=1 < "${ROOT_DIR}/tests/integration/db_function_tests.sql"

echo "All DB function tests completed successfully."
