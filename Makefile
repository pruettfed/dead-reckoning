.PHONY: help start-dr start-dr-frontend start-dr-backend start-dr-prod down-dr

help:
	@echo "make start-dr           - db + backend + frontend (docker, dev mode, default)"
	@echo "make start-dr-frontend  - frontend only (docker); needs backend already running separately"
	@echo "make start-dr-backend   - db + backend only (docker, dev mode)"
	@echo "make start-dr-prod      - single-origin image mimicking production (docker-compose.prod.yml)"
	@echo "make down-dr            - stop and remove whichever stack (dev or prod) is running"

start-dr:
	docker compose up --build -d

start-dr-frontend:
	docker compose up --build frontend -d

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
