.PHONY: help env venv install dev down test lint types eval keys logs clean templates notebooks

VENV := .venv
PY   := $(VENV)/bin/python
PIP  := $(VENV)/bin/pip

help:
	@echo "make env     - create .env with a generated encryption key (start here)"
	@echo "make install - create a virtualenv and install dev dependencies"
	@echo "make dev     - bring the stack up (no LibreOffice, fast)"
	@echo "make dev-full- same plus the converter (.doc and PDF support)"
	@echo "make reset-docker - clear stuck images after a failed build"
	@echo "make test    - run the full test suite (installs if needed)"
	@echo "make templates - regenerate the bundled Word templates"
	@echo "make notebooks - regenerate the Colab training notebooks"
	@echo "make keys    - generate an ENCRYPTION_KEY"
	@echo "make lint    - ruff + mypy"
	@echo "make logs    - tail the running stack"
	@echo "make down    - stop the stack and remove volumes"

# Every recipe line here is silenced with @. Without it make echoes the
# commands to stdout, and `make keys >> .env` then writes those echoed lines
# into the env file, which compose rejects with "key cannot contain a space".
$(VENV):
	@python3 -m venv $(VENV)
	@$(PIP) install --quiet --upgrade pip

install: $(VENV)
	@$(PIP) install --quiet -r requirements/dev.txt
	@echo "ready. tests: make test"

# Default stack: no LibreOffice. Builds in a fraction of the time.
dev:
	docker compose -f infra/docker-compose.yml up --build

# Adds the converter service, for .doc templates or PDF output.
dev-full:
	docker compose -f infra/docker-compose.yml --profile converter up --build

down:
	docker compose -f infra/docker-compose.yml down -v

logs:
	docker compose -f infra/docker-compose.yml logs -f --tail=100

# Depends on install so a fresh clone can run `make test` directly instead of
# failing with "pytest: No such file or directory".
#
# `tests/` runs first on purpose: it imports every FastAPI app, so a route that
# only breaks at import time fails here in two seconds rather than at
# `docker compose up` after a four-minute build.
test: install
	$(VENV)/bin/pytest tests -q
	$(VENV)/bin/pytest packages -q
	PYTHONPATH=.:apps/api $(VENV)/bin/pytest apps/api/tests -q

lint: install
	$(VENV)/bin/ruff check packages apps scripts tests
	$(VENV)/bin/mypy packages/schema packages/llm --ignore-missing-imports

# Regenerate the bundled Word templates and the Colab notebook. Both are
# generated artefacts: edit the scripts, not the .docx or the .ipynb.
templates: install
	$(PY) scripts/build_templates.py

notebooks: install
	$(PY) scripts/build_classifier_notebook.py

# Pure stdlib on purpose: an AES-256 key is 32 random bytes, so this must work
# on a fresh clone before anything is installed. Importing the app package here
# would make the very first setup step depend on a full dependency install.
keys:
	@python3 -c "import base64,os;print('ENCRYPTION_KEY='+base64.urlsafe_b64encode(os.urandom(32)).decode())"

# Creates .env and fills in generated secrets. Start here on a fresh clone.
env:
	@python3 scripts/setup_env.py

eval: install
	$(PY) eval/run_eval.py

# Docker's containerd image store sometimes leaves a half-written image behind
# after a cancelled build, and the next build fails with "image already
# exists". Removing the images is the documented way out.
reset-docker:
	-docker compose -f infra/docker-compose.yml down -v
	-docker rmi -f infra-api infra-ml-service infra-converter infra-web
	-docker builder prune -f
	@echo "docker state reset. run: make dev"

clean:
	rm -rf $(VENV) .pytest_cache .ruff_cache .mypy_cache
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
