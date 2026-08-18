.PHONY: help start-dr start-dr-frontend start-dr-backend start-dr-prod down-dr

# Set STUB to false to proxy a real backend instead of using stub data
STUB ?= true

help:
	@echo "make start-dr           - db + backend + frontend (docker, dev mode, default)"
	@echo "make start-dr-frontend  - frontend only (docker) with stub data; STUB=false proxies a real backend"
	@echo "make start-dr-backend   - db + backend only (docker, dev mode)"
	@echo "make start-dr-prod      - single-origin image mimicking production (docker-compose.prod.yml)"
	@echo "make down-dr            - stop and remove whichever stack (dev or prod) is running"

start-dr:
	docker compose up --build -d

start-dr-frontend:
	VITE_STUB=$(STUB) docker compose up --build frontend -d

start-dr-backend:
	docker compose up --build db backend -d

start-dr-prod:
	docker compose -f docker-compose.prod.yml up --build -d

down-dr:
	@if [ -n "$$(docker compose -f docker-compose.prod.yml ps -q)" ]; then \
		docker compose -f docker-compose.prod.yml down; \
	else \
		docker compose down; \
	fi
