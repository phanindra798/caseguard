# Common commands. Run `make help` to list them.
.PHONY: help infra up down run test test-api test-worker test-agent agent-venv e2e chat harness harness-unit harness-tracing otel-agent retrieval-eval eval judge loadtest contract demo-worker demo-relay

help:            ## Show this list
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  make %-12s %s\n", $$1, $$2}'

infra:           ## Start PostgreSQL and Kafka in Docker (for tests and demos)
	docker compose up -d postgres kafka

up:              ## Build and start the whole stack in Docker
	docker compose up -d --build

down:            ## Stop the stack (data is kept)
	docker compose down

run:             ## Run the Case API locally on http://localhost:8081 (needs `make infra`)
	cd case-api && ./mvnw -q spring-boot:run

test: test-api test-worker test-agent   ## Run all tests (needs `make infra`)

test-api:        ## Case API tests
	cd case-api && ./mvnw -B test

test-worker:     ## Escalation worker tests
	cd escalation-worker && ./mvnw -B test

agent-venv:      ## Create the agent's Python virtualenv
	cd agent && python3 -m venv .venv && .venv/bin/pip install -q -e ".[dev]" && .venv/bin/pip install -q -e "../harness[dev]"

test-agent:      ## Agent tests against the fake Case API (fast, no services needed)
	cd agent && .venv/bin/python -m pytest -q

e2e:             ## Agent against the REAL Case API + Kafka + worker (needs `make infra`)
	scripts/e2e-agent.sh

chat:            ## Chat with the agent in the terminal (needs `make up` or a running Case API)
	cd agent && .venv/bin/python -m caseguard_agent.app

harness-unit:    ## Unit tests for the harness's checkers and fault proxy
	agent/.venv/bin/python -m pytest -q harness/tests

harness:         ## Run every scenario file against the real stack (needs `make infra`, built jars)
	agent/.venv/bin/python -m caseguard_harness --report harness-report.md

otel-agent:      ## Download the OpenTelemetry Java agent into tools/ (checksum verified)
	scripts/fetch-otel-agent.sh

harness-tracing: ## All scenarios with every service traced; S26 checks one trace across all services
	agent/.venv/bin/python -m caseguard_harness --tracing --report harness-report.md

retrieval-eval:  ## Measure policy retrieval on the labelled queries in evals/retrieval.yaml
	cd agent && .venv/bin/python -m caseguard_agent.retrieval_eval

eval:            ## Evaluate the agent on 105 labelled conversations (all configurations, 3 trials)
	cd agent && .venv/bin/python -m caseguard_agent.evaluation --trials 3 --report ../eval-report.md

judge:           ## Calibrate the (baseline) judge against the hand-labelled replies
	cd agent && .venv/bin/python -m caseguard_agent.evaluation.judge

loadtest:        ## Load test the real pipeline: 2,000 escalations, 20% same-key retries (needs `make infra`, jars)
	agent/.venv/bin/python -m caseguard_harness.loadtest --escalations 2000 --concurrency 50 --report load-report.md

contract:        ## Regenerate case-api/openapi.json after an intentional API change
	cd case-api && UPDATE_CONTRACT=true ./mvnw -B test -Dtest=ApiContractTest

demo-worker:     ## Real crash demo: worker dies after DB commit, before Kafka ack
	scripts/demo-crash-replay.sh worker

demo-relay:      ## Real crash demo: relay dies after publish, before marking the outbox row
	scripts/demo-crash-replay.sh relay
