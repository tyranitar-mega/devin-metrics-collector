# Copyright 2026 Yunze Xu
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

TELESCOPE_IMAGE ?= ghcr.io/scopedb/telescope:v0.4.0
ENV_FILE ?= .env
PYTHON ?= python3
CATALOG_RESOURCE ?= /v1/catalog/databases/scopedb/schemas/devin_cli
CATALOG_DEFINITION ?= catalog/devin-cli.schema.json
CATALOG_PLAN ?= catalog-plan.json
COMPOSE = docker compose --env-file $(if $(wildcard $(ENV_FILE)),$(ENV_FILE),/dev/null)
CONFIG_MOUNT = -v $(CURDIR)/telescope.yaml:/etc/telescope/telescope.yaml:ro
SAMPLES_MOUNT = -v $(CURDIR)/samples:/samples:ro

.PHONY: help up down logs ps status verify query plan catalog-plan catalog-apply catalog-get validate preview inspect capture config-check

help:
	@echo "make up                 Start Telescope with Docker Compose"
	@echo "make down               Stop Telescope"
	@echo "make logs               Follow Telescope logs"
	@echo "make status             Show ScopeDB delivery status"
	@echo "make verify             Send a synthetic metrics probe"
	@echo "make query Q='...'      Run a ScopeQL query"
	@echo "make plan               Preview the Catalog API schema/table change"
	@echo "make catalog-apply      Apply the saved Catalog API plan"
	@echo "make catalog-get        Read the schema from the Catalog API"
	@echo "make validate           Validate mapping against the live ScopeDB catalog"
	@echo "make preview            Preview mapping with the bundled sample"
	@echo "make config-check       Validate Compose configuration without starting it"

up:
	$(COMPOSE) up -d

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f telescope

ps:
	$(COMPOSE) ps

status:
	$(COMPOSE) exec telescope telescope status

verify:
	$(COMPOSE) exec telescope telescope verify metrics

query:
	@test -n "$(Q)" || (echo 'Usage: make query Q="FROM scopedb.devin_cli.metrics SELECT metric_name, value LIMIT 5"' >&2; exit 2)
	$(COMPOSE) exec telescope telescope query "$(Q)"

plan: catalog-plan

catalog-plan:
	@test -f $(ENV_FILE) || (echo "Create $(ENV_FILE) from .env.example first" >&2; exit 2)
	@$(PYTHON) scripts/scopedb_catalog.py plan --env-file $(ENV_FILE) \
	  --resource $(CATALOG_RESOURCE) \
	  --file $(CATALOG_DEFINITION) \
	  --out $(CATALOG_PLAN)

catalog-apply:
	@test -f $(ENV_FILE) || (echo "Create $(ENV_FILE) from .env.example first" >&2; exit 2)
	@test -f $(CATALOG_PLAN) || (echo "Run make plan first" >&2; exit 2)
	@$(PYTHON) scripts/scopedb_catalog.py apply --env-file $(ENV_FILE) \
	  $(if $(ALLOW_DESTRUCTIVE),--allow-destructive) $(CATALOG_PLAN)

catalog-get:
	@test -f $(ENV_FILE) || (echo "Create $(ENV_FILE) from .env.example first" >&2; exit 2)
	@$(PYTHON) scripts/scopedb_catalog.py get --env-file $(ENV_FILE) --resource $(CATALOG_RESOURCE)

validate:
	@test -f $(ENV_FILE) || (echo "Create $(ENV_FILE) from .env.example first" >&2; exit 2)
	docker run --rm --env-file $(ENV_FILE) $(CONFIG_MOUNT) $(TELESCOPE_IMAGE) \
	  validate /etc/telescope/telescope.yaml

preview:
	docker run --rm $(CONFIG_MOUNT) $(SAMPLES_MOUNT) $(TELESCOPE_IMAGE) \
	  preview --offline --sample metrics=/samples/devin-metrics.otlp.json \
	  /etc/telescope/telescope.yaml

inspect:
	docker run --rm $(CONFIG_MOUNT) $(SAMPLES_MOUNT) $(TELESCOPE_IMAGE) \
	  inspect metrics --sample /samples/devin-metrics.otlp.json

capture:
	$(COMPOSE) exec telescope telescope capture --limit 100 metrics

config-check:
	TELESCOPE_SCOPEDB_ENDPOINT=$${TELESCOPE_SCOPEDB_ENDPOINT:-https://example.invalid} \
	TELESCOPE_SCOPEDB_API_KEY=$${TELESCOPE_SCOPEDB_API_KEY:-config-check} \
	$(COMPOSE) config --quiet
