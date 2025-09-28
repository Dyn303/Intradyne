
.PHONY: build run up down prod-up prod-down test ping lint type docker-up docker-logs docker-down api-up api-logs clean-artifacts monitoring-up monitoring-down
build:
	docker build -t intradyne-lite:1.9.0 .
build-api:
	docker build -f docker/Dockerfile.api -t intradyne-lite-api:1.9.0 .
run:
	docker run --rm -p 8080:8000 -e CONFIG=/app/config.yaml -v $$PWD/config.yaml.example:/app/config.yaml:ro -v $$PWD/profiles.yaml.example:/app/profiles.yaml:ro -v $$PWD/data:/app/data intradyne-lite:1.9.0
run-api:
	docker run --rm -p 8080:8000 intradyne-lite-api:1.9.0
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
	.venv/Scripts/python -m ruff check intradyne src app tests

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
