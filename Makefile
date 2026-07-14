.PHONY: setup check test lint validate run trigger slack-bridge up down

setup:
	python -m venv .venv
	.venv/bin/python -m pip install -r requirements-dev.txt

check:
	.venv/bin/python scripts/check_config.py

test:
	.venv/bin/python -m pytest

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
