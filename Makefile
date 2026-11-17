# Common commands. Run `make help` to list them.
.PHONY: help db db-down run test

help:            ## Show this list
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  make %-10s %s\n", $$1, $$2}'

db:              ## Start PostgreSQL in Docker
	docker compose up -d postgres

db-down:         ## Stop PostgreSQL (data is kept)
	docker compose down

run:             ## Run the Case API on http://localhost:8081
	cd case-api && mvn -q spring-boot:run

test:            ## Run all Case API tests (needs the database)
	cd case-api && mvn -B test
