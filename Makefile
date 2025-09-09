
.PHONY: build run up down prod-up prod-down test ping
build:
	docker build -t intradyne-lite:1.9.0 .
run:
	docker run --rm -p 8000:8000 -e CONFIG=/app/config.yaml -v $$PWD/config.yaml.example:/app/config.yaml:ro -v $$PWD/profiles.yaml.example:/app/profiles.yaml:ro -v $$PWD/data:/app/data intradyne-lite:1.9.0
up:
	docker compose -f deploy/docker-compose.yml up -d --build
down:
	docker compose -f deploy/docker-compose.yml down
prod-up:
	cd deploy && docker compose -f docker-compose.prod.yml --env-file ../.env up -d --build
prod-down:
	cd deploy && docker compose -f docker-compose.prod.yml --env-file ../.env down
ping:
	curl "http://localhost:8000/healthz"
