NEURO_SAN_PM_HTTP_PORT ?= 8188
export NEURO_SAN_PM_HTTP_PORT
export NEURO_SAN_SERVER_HTTP_PORT := $(NEURO_SAN_PM_HTTP_PORT)
export NEURO_SAN_BASE_URL := http://localhost:$(NEURO_SAN_PM_HTTP_PORT)

.PHONY: setup check test agentic-test lint validate run trigger slack-bridge up down

setup:
	python -m venv .venv
	.venv/bin/python -m pip install -r requirements-dev.txt

check:
	.venv/bin/python scripts/check_config.py

test:
	.venv/bin/python -m pytest

agentic-test:
	.venv/bin/python -m pytest \
		tests/test_coder_dependency.py \
		tests/test_slack_coder_approval.py \
		tests/test_github_delivery.py \
		tests/test_fork_delivery.py \
		tests/test_agentic_delivery_contract.py

lint:
	.venv/bin/python -m ruff check .

validate: check test lint
	GITHUB_TOKEN=validation-only .venv/bin/python -m neuro_san.client.hocon_validator_cli \
		registries/product_colleague.hocon \
		--registry-dir .

run:
	.venv/bin/python -m scripts.start_server

trigger:
	.venv/bin/python scripts/trigger_event.py

slack-bridge:
	.venv/bin/python -m apps.slack_bridge

up:
	docker compose --profile slack up -d --build

down:
	.venv/bin/python -m scripts.slack_availability offline
	docker compose --profile slack down
