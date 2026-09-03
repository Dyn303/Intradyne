
.PHONY: build run up down prod-up prod-down test ping lint type docker-up docker-logs docker-down api-up api-logs clean-artifacts monitoring-up monitoring-down db-migrate db-migrate-check test-postgres
build:
	docker build -t intradyne-lite:1.9.0 .
build-api:
run:
	docker run --rm -p 8080:8000 -e CONFIG=/app/config.yaml -v $$PWD/config.yaml.example:/app/config.yaml:ro -v $$PWD/profiles.yaml.example:/app/profiles.yaml:ro -v $$PWD/data:/app/data intradyne-lite:1.9.0
run-api:
up:
	docker compose -f deploy/docker-compose.yml up -d --build
down:
	docker compose -f deploy/docker-compose.yml down
prod-up:
	cd deploy && docker compose -f docker-compose.prod.yml --env-file ../.env up -d --build
prod-down:
	cd deploy && docker compose -f docker-compose.prod.yml --env-file ../.env down
ping:
	curl "http://localhost:8080/healthz"

lint:
	.venv/Scripts/python -m ruff check src tests scripts

type:
	.venv/Scripts/python -m mypy --pretty

test:
	.venv/Scripts/python -m pytest -q

docker-up:
	docker compose -f deploy/docker-compose.yml up -d --build
docker-up-slim:
	docker compose -f deploy/docker-compose.yml --profile slim up -d --build

docker-logs:
	docker logs -f intradyne-engine

docker-down:
	docker compose -f deploy/docker-compose.yml down
docker-down-slim:
	docker compose -f deploy/docker-compose.yml --profile slim down

monitoring-up:
	docker compose -f deploy/docker-compose.prod.yml --profile monitoring up -d --build

monitoring-down:
	docker compose -f deploy/docker-compose.prod.yml --profile monitoring down

api-up:
	uvicorn intradyne.api.app:app --host 0.0.0.0 --port 8000

api-logs:
	curl -s http://localhost:8080/version && echo

clean-artifacts:
	powershell -NoProfile -Command "if (Test-Path artifacts) { Remove-Item -Recurse -Force artifacts }"

smoke:
	# Check a running instance actually serves what it claims to: the API, the
	# dashboard, /metrics as Prometheus text, and the research record.
	# Override API_BASE for the compose stack (it publishes on 8080).
	API_BASE=$${API_BASE:-http://localhost:8000} python scripts/e2e_smoke.py

stack-up:
	# One command from a clean checkout to a working system.
	docker compose -f deploy/docker-compose.yml up -d --build
	@echo "dashboard: http://localhost:8080"

stack-smoke:
	API_BASE=http://localhost:8080 python scripts/e2e_smoke.py

stack-down:
	docker compose -f deploy/docker-compose.yml down

db-migrate:
	# Copy equity history, order keys and traded notional from the old SQLite
	# database into the compose Postgres. Run BEFORE switching DB_URL: starting
	# against an empty equity table means dd_30d([]) == 0.0 and a drawdown halt
	# re-armed from zero. Refuses a non-empty target unless --replace is passed.
	# Runs inside the api container, where both databases are reachable and
	# Postgres does not have to be published to the host.
	docker compose -f deploy/docker-compose.yml exec api \
		python scripts/migrate_sqlite_to_postgres.py \
		--source sqlite:////app/state/trades.sqlite --target "$$DB_URL"

db-migrate-check:
	# Compare row counts on both sides. No writes.
	docker compose -f deploy/docker-compose.yml exec api \
		python scripts/migrate_sqlite_to_postgres.py \
		--source sqlite:////app/state/trades.sqlite --target "$$DB_URL" --check

test-postgres:
	# The Postgres half of tests/test_db_backends.py skips without a live
	# database, and it is the only proof the three stores behave identically on
	# both backends. This runs the suite inside the app image on a private
	# network rather than against a published port: on Docker Desktop for
	# Windows the host port proxy refuses new connections after a couple of
	# rapid connect/close cycles ("Address already in use"), which the tests do
	# constantly. Container to container is also how the stack actually runs.
	docker network create intradyne-test 2>/dev/null || true
	docker run -d --rm --name intradyne-pgtest --network intradyne-test \
		-e POSTGRES_PASSWORD=testpw -e POSTGRES_USER=intradyne \
		-e POSTGRES_DB=intradyne -e TZ=Asia/Kuching postgres:16-alpine
	until docker exec intradyne-pgtest pg_isready -U intradyne -d intradyne; do sleep 1; done
	-docker build -q -t intradyne-lite:test . && docker run --rm \
		--network intradyne-test -v "$$PWD/tests:/app/tests:ro" \
		-v "$$PWD/pytest.ini:/app/pytest.ini:ro" --user root -w /app \
		-e TEST_POSTGRES_URL=postgresql://intradyne:testpw@intradyne-pgtest:5432/intradyne \
		--entrypoint sh intradyne-lite:test -c \
		"pip install --quiet --no-cache-dir pytest==9.0.3 && python -m pytest tests/test_db_backends.py -q"
	docker rm -f intradyne-pgtest
	docker network rm intradyne-test
