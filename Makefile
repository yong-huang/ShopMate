# ShopMate development harness.
# Runs the FastAPI backend (uvicorn, reload) and the Next.js dev server.

BACKEND_PORT ?= 8000
FRONTEND_PORT ?= 3000

# --- Evaluation (Eval Plan, docs/eval-plan.md) -----------------------------
# PROVIDER/MODEL select the run's chat provider (env passthrough); judge
# provider/model come from JUDGE_PROVIDER/JUDGE_MODEL (default: run provider).

PROVIDER ?=
MODEL ?=

eval: ## Run the 32-scenario golden set on the default provider (override: make eval PROVIDER=ollama MODEL=qwen3:8b)
	cd backend && env $(if $(PROVIDER),LLM_PROVIDER=$(PROVIDER)) $(if $(MODEL),LLM_MODEL=$(MODEL)) uv run python -m evals.runner

eval-compare: ## Merge latest per-provider reports into one comparison matrix
	cd backend && uv run python -m evals.runner --eval-compare

.PHONY: dev backend frontend catalog catalog-check eval eval-compare docker-up docker-down docker-logs smoke-gateway

dev: ## Run backend and frontend concurrently
	$(MAKE) -j 2 backend frontend

backend: ## Run the FastAPI backend (uvicorn --reload)
	cd backend && uv run uvicorn app.main:app --reload --port $(BACKEND_PORT)

frontend: ## Run the Next.js frontend (next dev)
	cd frontend && npm run dev -- --port $(FRONTEND_PORT)

catalog: ## Regenerate backend/data/catalog (products.json + SVG images)
	cd backend && uv run python -m app.catalog.generate

catalog-check: ## Build check: total count + 30/40/40/40 category split
	cd backend && uv run python -m app.catalog.generate --check

docker-up: ## Build and start the demo stack (api + web) detached
	docker compose up --build -d

docker-down: ## Stop the demo stack
	docker compose down

docker-logs: ## Tail the structured JSON logs of the demo stack
	docker compose logs -f

smoke-gateway: ## Live model-gateway smoke test against the configured provider (needs credentials)
	cd backend && uv run python ../scripts/smoke_gateway.py
