COMPOSE := docker compose
APP_URL := http://127.0.0.1:8000

.PHONY: up down logs check

# Build the image, start the app and wait until it is healthy (needs .env, see .env.example).
up:
	$(COMPOSE) up -d --build --wait

# Stop and remove the app container.
down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f

# The app is up (/healthz) and reaches the remote Elasticsearch (/readyz); fails otherwise.
check:
	@body=$$(curl -fsS $(APP_URL)/healthz) && echo "GET /healthz -> $$body"
	@body=$$(curl -sS $(APP_URL)/readyz); echo "GET /readyz -> $$body"; test "$$body" = '{"status":"ready"}' \
		|| { echo "Elasticsearch is not reachable from the app; see 'make logs' and the README troubleshooting table"; exit 1; }
