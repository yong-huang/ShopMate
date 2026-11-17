# ShopMate development harness.
# Runs the FastAPI backend (uvicorn, reload) and the Next.js dev server.

BACKEND_PORT ?= 8000
FRONTEND_PORT ?= 3000

.PHONY: dev backend frontend smoke-gateway

dev: ## Run backend and frontend concurrently
	$(MAKE) -j 2 backend frontend

backend: ## Run the FastAPI backend (uvicorn --reload)
	cd backend && uv run uvicorn app.main:app --reload --port $(BACKEND_PORT)

frontend: ## Run the Next.js frontend (next dev)
	cd frontend && npm run dev -- --port $(FRONTEND_PORT)

smoke-gateway: ## Live model-gateway smoke test against the configured provider (needs credentials)
	cd backend && uv run python ../scripts/smoke_gateway.py
