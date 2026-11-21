# Containerized Microservices Application

A learning project for independently deployable Python microservices, developed and tested in GitHub Codespaces.

## Services
- **API Gateway (8000):** single client entry point and HTTP reverse proxy
- **Auth Service (8001):** registration, PBKDF2 password hashing and JWT issuance
- **Product Service (8002):** authenticated product CRUD
- **Payment Service (8003):** payment simulation only; no real money movement
- **Order Service (8004):** validates products and coordinates simulated payments

## Phase 1 architecture
Phase 1 validates service boundaries, REST contracts, JWT authentication, synchronous service-to-service HTTP calls, container builds and multi-container orchestration. Each service currently stores data in memory, so data resets when its container restarts. PostgreSQL and Redis are planned for Phase 2.

## Run in your existing GitHub Codespace

Pull the latest commits:

    git pull origin main

Install dependencies and run the test suite:

    pip install -r requirements-dev.txt
    pytest -q

Build and start all five containers:

    docker compose up --build -d

Check service status and health:

    docker compose ps
    curl http://localhost:8000/health

Open port 8000 in the Codespaces Ports tab. The gateway API documentation is available at /docs. Individual service docs are available on ports 8001–8004.

Follow logs:

    docker compose logs -f

Stop the stack:

    docker compose down

## API walkthrough

Run these commands in the Codespace terminal. The gateway is available at http://localhost:8000.

Register a user:

    curl -i -X POST http://localhost:8000/api/auth/register -H "Content-Type: application/json" -d '{"email":"dev@example.com","password":"StrongPass123"}'

Login and store the token:

    TOKEN=$(curl -s -X POST http://localhost:8000/api/auth/login -H "Content-Type: application/json" -d '{"email":"dev@example.com","password":"StrongPass123"}' | python -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')

Create a product:

    curl -i -X POST http://localhost:8000/api/products -H "Content-Type: application/json" -H "Authorization: Bearer $TOKEN" -d '{"name":"Keyboard","description":"Mechanical keyboard","price":79.99,"stock":10}'

List products:

    curl -i http://localhost:8000/api/products -H "Authorization: Bearer $TOKEN"

Create an order (the order service calls the product and payment services):

    curl -i -X POST http://localhost:8000/api/orders -H "Content-Type: application/json" -H "Authorization: Bearer $TOKEN" -d '{"product_id":1,"quantity":2}'

List the current user's orders:

    curl -i http://localhost:8000/api/orders -H "Authorization: Bearer $TOKEN"

## Service ports

| Service | Port | OpenAPI |
|---|---:|---|
| API Gateway | 8000 | /docs |
| Auth | 8001 | /docs |
| Product | 8002 | /docs |
| Payment | 8003 | /docs |
| Order | 8004 | /docs |

## Configuration and security

All services that validate JWTs must use the same `JWT_SECRET`. The default value is for local development only. Before running the Compose stack with a custom secret, export it in the Codespace terminal:

    export JWT_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"

Then run `docker compose up --build -d` from that same terminal session. Do not commit real secrets to Git.

Passwords are hashed with PBKDF2-HMAC-SHA256. Payment processing is a simulation and must not be used for real transactions. The current in-memory data stores, development JWT configuration and simulated payment flow are not production-ready.

## Roadmap
- **Phase 1:** service boundaries, REST APIs, JWT, service-to-service calls, Docker and Compose
- **Phase 2:** PostgreSQL persistence, Redis, database migrations and data ownership
- **Phase 3:** Kubernetes Deployments, Services, Ingress, probes, ConfigMaps and Secrets
- **Phase 4:** Helm, autoscaling, CI/CD and image publishing
- **Phase 5:** Prometheus, Grafana, centralized logs and resilience testing
