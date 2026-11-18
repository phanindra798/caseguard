# Common commands. Run `make help` to list them.
.PHONY: help infra up down run test test-api test-worker contract demo-worker demo-relay

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

test: test-api test-worker   ## Run all tests (needs `make infra`)

test-api:        ## Case API tests
	cd case-api && ./mvnw -B test

test-worker:     ## Escalation worker tests
	cd escalation-worker && ./mvnw -B test

contract:        ## Regenerate case-api/openapi.json after an intentional API change
	cd case-api && UPDATE_CONTRACT=true ./mvnw -B test -Dtest=ApiContractTest

demo-worker:     ## Real crash demo: worker dies after DB commit, before Kafka ack
	scripts/demo-crash-replay.sh worker

demo-relay:      ## Real crash demo: relay dies after publish, before marking the outbox row
	scripts/demo-crash-replay.sh relay
