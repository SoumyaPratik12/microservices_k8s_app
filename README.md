# Containerized Microservices Application

A learning project for independently deployable Python microservices, developed in GitHub Codespaces.

## Services
- API Gateway: single client entry point and HTTP reverse proxy
- Auth Service: registration, password hashing and JWT issuance
- Product Service: product CRUD
- Order Service: validates products and coordinates simulated payments
- Payment Service: deterministic payment simulation
- PostgreSQL and Redis: planned for Phase 2

## Phase 1 design
Phase 1 uses in-memory stores to validate service boundaries and API contracts. Data resets when a service restarts. This is not production persistence.

## Start in GitHub Codespaces
1. Open this repository on GitHub.
2. Select Code → Codespaces → Create codespace on main.
3. Wait for dev container setup.
4. Install dependencies if required: `pip install -r requirements-dev.txt`

5. Start each service in a separate terminal:

    uvicorn services.auth_service.app.main:app --host 0.0.0.0 --port 8001 --reload
    uvicorn services.product_service.app.main:app --host 0.0.0.0 --port 8002 --reload
    uvicorn services.payment_service.app.main:app --host 0.0.0.0 --port 8003 --reload
    uvicorn services.order_service.app.main:app --host 0.0.0.0 --port 8004 --reload
    uvicorn api_gateway.app.main:app --host 0.0.0.0 --port 8000 --reload

6. Open port 8000 from the Ports tab. API documentation is available at /docs.

## API walkthrough
1. Register: POST /api/auth/register
2. Login: POST /api/auth/login
3. Copy the returned access token.
4. Create a product: POST /api/products with Authorization: Bearer <token>
5. Create an order: POST /api/orders with the same bearer token.
6. Inspect the response for the simulated payment result.

Registration JSON: {"email":"dev@example.com","password":"StrongPass123"}
Product JSON: {"name":"Keyboard","description":"Mechanical keyboard","price":79.99,"stock":10}
Order JSON: {"product_id":1,"quantity":2}

## Service ports
| Service | Port | OpenAPI |
|---|---:|---|
| API Gateway | 8000 | /docs |
| Auth | 8001 | /docs |
| Product | 8002 | /docs |
| Payment | 8003 | /docs |
| Order | 8004 | /docs |

## Roadmap
- Phase 1: service boundaries, REST APIs, JWT and service-to-service calls
- Phase 2: PostgreSQL, Redis, migrations and persistence
- Phase 3: Dockerfiles and Docker Compose
- Phase 4: Kubernetes Deployments, Services, Ingress, probes and storage
- Phase 5: Helm, HPA and CI/CD
- Phase 6: Prometheus, Grafana and resilience testing

## Security
JWT_SECRET has a development-only fallback. Set a long random value before any shared deployment. Passwords are hashed with PBKDF2-HMAC-SHA256. Payment processing is simulated and does not contact a payment provider.
