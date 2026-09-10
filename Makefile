# CAT — one-command test entry points.
#
#   make test          both suites on the host venvs (backend + quant)
#   make test-backend  backend/tests only
#   make test-quant    quant/tests only
#   make test-docker   both suites inside the running compose stack
#   make venv          (re)create backend/.venv from the pinned lock
#   make lock          re-freeze backend/requirements.txt off the running image

ROOT       := $(patsubst %/,%,$(dir $(abspath $(lastword $(MAKEFILE_LIST)))))
PY312      ?= python3.12
BACKEND_PY := $(ROOT)/backend/.venv/bin/python
QUANT_PY   := $(ROOT)/quant/.venv/bin/python

# Host DB URLs come from repo .env — no credentials duplicated here. The
# backend suite reads them from the environment (it never loads .env), so they
# have to be exported; quant.data.registry prefers the environment too, which
# lets a one-off run point elsewhere without touching the secret file.
# The compose postgres is published on 5433 (a native PostgreSQL owns 5432), so
# these must carry port 5433 or every PG-backed test skips.
DATABASE_URL_SYNC ?= $(shell sed -n 's/^DATABASE_URL_SYNC=//p' $(ROOT)/.env 2>/dev/null)
DATABASE_URL      ?= $(shell sed -n 's/^DATABASE_URL=//p' $(ROOT)/.env 2>/dev/null)
PYTHONPATH        := $(ROOT)
export DATABASE_URL_SYNC
export DATABASE_URL
export PYTHONPATH

PYTEST_ARGS ?= -q

.PHONY: test test-backend test-quant test-docker venv lock

test: test-backend test-quant

test-backend: $(BACKEND_PY)
	cd $(ROOT)/backend && $(BACKEND_PY) -m pytest tests $(PYTEST_ARGS)

test-quant: $(QUANT_PY)
	cd $(ROOT) && $(QUANT_PY) -m pytest quant/tests $(PYTEST_ARGS)

test-docker:
	docker compose exec -T backend python -m pytest tests $(PYTEST_ARGS)
	docker compose exec -T backend python -m pytest quant/tests $(PYTEST_ARGS)

venv:
	$(PY312) -m venv $(ROOT)/backend/.venv
	$(BACKEND_PY) -m pip install --quiet --upgrade pip
	$(BACKEND_PY) -m pip install --quiet -r $(ROOT)/backend/requirements.txt

$(BACKEND_PY):
	$(MAKE) venv

$(QUANT_PY):
	@echo "quant/.venv missing: python3.11 -m venv quant/.venv && \
	quant/.venv/bin/pip install -r quant/requirements.txt" >&2; exit 1

# Pins are read off the BUILT image, so the lock records what a redeploy will
# run rather than what a macOS resolver would pick. It is a throwaway container
# (--no-deps, no ports) so this never disturbs the running stack — but it also
# means requirements.in has to reach the image first: `docker compose build
# backend`, then `make lock`.
# The freeze is its own recipe line on purpose: piping it into `sort` would hide
# a failed container behind sort's exit 0, and the lockfile would be silently
# replaced by a bare comment header. Alone, a non-zero pip freeze aborts make and
# requirements.txt is never touched.
lock:
	docker compose build backend
	docker compose run --rm --no-deps -T --entrypoint "" backend pip freeze \
	  > $(ROOT)/backend/requirements.freeze \
	  || { rm -f $(ROOT)/backend/requirements.freeze; exit 1; }
	@{ grep '^#' $(ROOT)/backend/requirements.txt; \
	   sort -f $(ROOT)/backend/requirements.freeze; } \
	  > $(ROOT)/backend/requirements.txt.new
	@mv $(ROOT)/backend/requirements.txt.new $(ROOT)/backend/requirements.txt
	@rm -f $(ROOT)/backend/requirements.freeze
	@echo "backend/requirements.txt re-frozen off the freshly built backend image"
