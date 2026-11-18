# Common commands. Run `make help` to list them.
.PHONY: help db up down run test contract

help:            ## Show this list
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  make %-10s %s\n", $$1, $$2}'

db:              ## Start only PostgreSQL in Docker
	docker compose up -d postgres

up:              ## Build and start the whole stack in Docker
	docker compose up -d --build

down:            ## Stop the stack (data is kept)
	docker compose down

run:             ## Run the Case API locally on http://localhost:8081 (needs `make db`)
	cd case-api && ./mvnw -q spring-boot:run

test:            ## Run all Case API tests (needs the database)
	cd case-api && ./mvnw -B test

contract:        ## Regenerate case-api/openapi.json after an intentional API change
	cd case-api && UPDATE_CONTRACT=true ./mvnw -B test -Dtest=ApiContractTest
