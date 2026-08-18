.PHONY: help dev test lint types eval keys down

help:
	@echo "make dev    - bring the whole stack up locally"
	@echo "make test   - run the full test suite"
	@echo "make lint   - ruff + mypy"
	@echo "make keys   - generate an encryption key"
	@echo "make eval   - run the golden-set evaluation"

dev:
	docker compose -f infra/docker-compose.yml up --build

down:
	docker compose -f infra/docker-compose.yml down -v

test:
	pytest packages -q
	PYTHONPATH=.:apps/api pytest apps/api/tests -q

lint:
	ruff check packages apps
	mypy packages/schema packages/llm --ignore-missing-imports

keys:
	@PYTHONPATH=apps/api python3 -c "from app.security.crypto import generate_key; print('ENCRYPTION_KEY=' + generate_key())"

eval:
	python3 eval/run_eval.py
