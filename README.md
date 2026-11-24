# Containerized Microservices Application

A learning project for independently deployable Python microservices, developed and tested in GitHub Codespaces.

## Services
- **API Gateway (8000):** single client entry point and HTTP reverse proxy
- **Auth Service (8001):** registration, PBKDF2 password hashing and JWT issuance
- **Product Service (8002):** authenticated product CRUD and atomic inventory reservation for the Order Service
- **Payment Service (8003):** PostgreSQL-backed simulated payment ledger; no real money movement
- **Order Service (8004):** idempotent order orchestration, inventory reservation, payment recovery, and reconciliation

## Architecture
The independently deployable FastAPI services use JWT authentication, synchronous service-to-service HTTP calls, Docker Compose, Kubernetes, and Helm. PostgreSQL and SQLAlchemy persist users, products, orders, idempotency transactions, inventory reservations, and payment outcomes. Idempotency keys are scoped to the authenticated user; retries replay the saved response, while a changed payload receives HTTP 409. Payment and inventory operations use separate durable idempotency keys. A bounded reconciler resumes stale transactions. Automated tests use isolated SQLite; Compose and Helm use PostgreSQL. Payment processing remains a simulation.

## Run in your existing GitHub Codespace

Pull the latest commits:

    git pull origin main

Install dependencies and run the complete local suite. Tests use an isolated temporary SQLite database:

    pip install -r requirements-dev.txt
    pytest -v

Run the PostgreSQL integration and real concurrency tests separately. This starts a uniquely named disposable Compose project with tmpfs PostgreSQL, uses a dedicated `test_microservices` database and isolated schema, and removes only that project on exit:

    sh scripts/run-postgres-tests.sh

Other useful test categories:

    pytest -m concurrency
    pytest -m recovery

Set development secrets (use strong values outside a disposable local environment):

    export JWT_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
    export POSTGRES_PASSWORD="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"

Start PostgreSQL and the application stack. Compose waits for PostgreSQL health, runs the one-shot Alembic migration service, then starts Auth, Product and Order only after migration succeeds:

    docker compose up --build -d

Check service status and health:

    docker compose ps
    docker compose ps --all
    docker compose logs migrations
    docker compose exec -T postgres psql -U microservices -d microservices -c "SELECT version_num FROM alembic_version;"
    curl http://localhost:8000/health

Open port 8000 in the Codespaces Ports tab. The gateway API documentation is available at /docs. Individual service docs are available on ports 8001–8004.

Follow logs:

    docker compose logs -f

Stop the stack without removing its PostgreSQL volume:

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

Create an order with a client-generated idempotency key. Reuse the same key and payload when retrying; a different payload with that key receives HTTP 409:

    curl -i -X POST http://localhost:8000/api/orders -H "Content-Type: application/json" -H "Authorization: Bearer $TOKEN" -H "Idempotency-Key: checkout-2026-001" -d '{"product_id":1,"quantity":2}'

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

All services that validate JWTs must use the same `JWT_SECRET`. The default values are for local development only. Before running the Compose stack with custom secrets, export them in the Codespace terminal:

    export JWT_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
    export POSTGRES_PASSWORD="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"

Then run `docker compose up --build -d` from that same terminal session. Do not commit real secrets to Git.

Passwords are hashed with PBKDF2-HMAC-SHA256. Auth users, products, orders, idempotency records, inventory reservations, and the payment ledger are stored in PostgreSQL for Compose and Helm deployments. The payment endpoint is a simulation and must not be used for real transactions. The default development credentials are not production-ready. For local decline-path tests only, set `PAYMENT_SIMULATED_OUTCOME=declined` in the Compose environment; callers cannot select an outcome in the request.

## Roadmap
- **Phase 1:** service boundaries, REST APIs, JWT, service-to-service calls, Docker and Compose
- **Phase 2:** PostgreSQL persistence for Auth, Product and Order; SQLAlchemy ORM and automated persistence tests
- **Phase 3:** Kubernetes Deployments, Services, Ingress, probes, ConfigMaps and Secrets
- **Phase 4:** Helm, PostgreSQL StatefulSet with PVC, CI/CD and image publishing
- **Phase 5:** Redis, autoscaling, Prometheus, Grafana and resilience testing


## Helm deployment (Phase 3)

The Helm chart is located at `helm/microservices-app`. It manages the five application Deployments and Services, PostgreSQL StatefulSet and Service, migration hooks, a bounded order-reconciliation CronJob, ConfigMaps, probes and a persistent volume claim.

The Helm release is intentionally installed in a separate namespace (`microservices-helm`) so it does not take ownership of resources created earlier with `kubectl apply -k k8s`.

Install Helm in the Codespace if it is not already available, then verify:

    helm version

Pull the latest repository changes:

    git pull origin main

Confirm the Kind cluster is active and the five application images are loaded:

    kubectl get nodes
    docker images | grep microservices-k8s-app

If the images were rebuilt, load them into Kind again:

    kind load docker-image --name microservices \
            microservices-k8s-app-auth:0.5.0 \
            microservices-k8s-app-product:0.5.0 \
            microservices-k8s-app-payment:0.5.0 \
            microservices-k8s-app-order:0.5.0 \
            microservices-k8s-app-gateway:0.5.0

Create a separate namespace and both application credentials. Keep the secret values private and do not put them in values.yaml:

    kubectl create namespace microservices-helm --dry-run=client -o yaml | kubectl apply -f -
    export JWT_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
    export POSTGRES_PASSWORD="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
    kubectl create secret generic app-secrets \
      --namespace microservices-helm \
      --from-literal=jwt-secret="$JWT_SECRET" \
      --from-literal=postgres-password="$POSTGRES_PASSWORD" \
      --dry-run=client -o yaml | kubectl apply -f -

Validate and render the chart before installing:

    helm lint helm/microservices-app
    helm template microservices helm/microservices-app \
      --namespace microservices-helm

Fresh install: the chart creates PostgreSQL and its PVC, then a `post-install` migration Job waits for PostgreSQL and applies Alembic. Database-backed application pods wait for the migration revision before becoming Ready. Do not pass Helm's `--wait` on this fresh install: Helm waits for application readiness before running post-install hooks, while those pods intentionally wait for the hook's migration.

The verified GitHub deployment workflow detects whether the Helm release already exists: it omits `--wait` for a fresh install and enables it for upgrades, then explicitly waits for the workloads after the migration hook completes.

Install the chart:

    helm upgrade --install microservices helm/microservices-app \
      --namespace microservices-helm \
      --create-namespace

Check the release and workloads:

    helm status microservices -n microservices-helm
    kubectl get deployments,pods,services -n microservices-helm
    kubectl rollout status statefulset/postgres -n microservices-helm
    kubectl wait --for=condition=available deployment --all -n microservices-helm --timeout=300s
    kubectl exec -n microservices-helm postgres-0 -- psql -U microservices -d microservices -c "SELECT version_num FROM alembic_version;"

Forward the Helm-managed API Gateway on a different local port:

    kubectl port-forward -n microservices-helm service/api-gateway 8081:8000

Test it from another terminal:

    curl http://localhost:8081/health

On upgrades, a `pre-upgrade` Job applies migrations before Helm changes application Deployments. Successful migration Jobs are automatically deleted; failed Jobs remain for diagnosis:

    helm upgrade microservices helm/microservices-app \
      --namespace microservices-helm

Inspect failed migration Jobs and logs:

    kubectl get jobs -n microservices-helm -l app.kubernetes.io/component=database-migration
    kubectl logs -n microservices-helm job/<failed-job-name>

Rollback to the previous Helm revision:

    helm history microservices -n microservices-helm
    helm rollback microservices 1 -n microservices-helm

Uninstall only the Helm-managed release:

    helm uninstall microservices -n microservices-helm

The original `microservices` namespace deployment remains separate.

The chart currently uses one replica per application service and local Kind images with `IfNotPresent`. All data services use PostgreSQL. Database-backed application pods wait for the Alembic revision before becoming Ready; the reconciliation CronJob is serialized and runs a bounded pass.


## PostgreSQL persistence (Phase 2)

The Auth, Product, Order, and Payment services use SQLAlchemy and share a PostgreSQL instance in the local deployment. The schema includes:
- Auth: `auth_users`
- Product: `catalog_products`
- Order: `customer_orders`, `idempotency_records`
- Product inventory: `inventory_reservations`
- Payment: `payment_ledger`

The payment service is intentionally a simulation. Its response is stored as JSON on the order record.

For Docker Compose, PostgreSQL is started with a named volume:

    export JWT_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
    export POSTGRES_PASSWORD="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
    docker compose up --build -d
    docker compose ps
    docker compose logs postgres

The PostgreSQL data volume is named `postgres_data`. To stop the application without deleting data:

    docker compose down

To intentionally delete the local database volume (destructive):

    docker compose down -v

Alembic owns schema creation and upgrades; the application does not create PostgreSQL tables at startup. Compose runs `alembic upgrade head` once after PostgreSQL is healthy. Kubernetes uses a post-install Job for fresh installations and a pre-upgrade Job for upgrades. All five application pods remain unready until `alembic_version` has a revision. The Order Service reconciler resumes stale transactions with bounded attempts and leases; uncertain payments are looked up by their stable key and are not treated as declines.

## Migration workflow

The shared SQLAlchemy configuration is in `shared/database.py`; Alembic loads all service models through `migrations/env.py`. For a local Compose database, generate and review a migration with:

    docker compose run --rm --volume "$PWD/migrations:/app/migrations" migrations alembic revision --autogenerate -m "describe_schema_change"

Review the generated revision and edit it as needed before applying. To apply, inspect the current revision, check model/schema drift, and upgrade:

    docker compose run --rm migrations alembic current
    docker compose run --rm migrations alembic check
    docker compose run --rm migrations alembic upgrade head
    docker compose exec -T postgres psql -U microservices -d microservices -c "SELECT version_num FROM alembic_version;"

The initial migration recognizes the matching tables created by previous releases, validates their expected columns, preserves existing rows, and creates only missing base tables/indexes. Incompatible legacy tables stop the migration with an explicit error.

Unit tests use isolated SQLite and do not connect to Compose or Kubernetes PostgreSQL:

    pytest -v

For Kubernetes, verify the revision with the PostgreSQL StatefulSet query above. Failed migration Jobs are deliberately retained and their logs are available with `kubectl logs`. Helm rollback only rolls application/chart resources back; it does not reverse database migrations. Use backward-compatible, forward-fixable migrations and take an appropriate database backup before operational upgrades.

## Backup and Restore

PostgreSQL is the authoritative persistent store. Backups must be copied to protected off-cluster storage with encryption, retention, and restricted access. Never put credentials in command arguments or shell history; supply `PGPASSWORD` through a protected environment or approved secret manager.

Create a compressed custom-format dump and checksum:

    export PGHOST=<database-host>
    export PGDATABASE=microservices
    export PGUSER=<backup-role>
    export PGPASSWORD=<secret-from-approved-secret-manager>
    sh scripts/backup-postgres.sh "backups/microservices-$(date -u +%Y%m%dT%H%M%SZ).dump"

Verify restoration in a disposable database before relying on a backup. The restore script checks the checksum and requires `RESTORE_CONFIRM_DATABASE` to exactly match the target database. It runs `pg_restore --clean`, which replaces objects in that target; never point it at a production database for a drill.

For an in-cluster recovery, first create a protected backup outside the PostgreSQL PVC. Restore into an approved new or recovered database, verify representative rows and `alembic_version`, and then deploy an application version compatible with that revision. Do not remove PVCs during normal upgrades or recovery. A backup is not considered verified until restoration and revision/data checks succeed.

## CI and Operations

Pull requests run Ruff lint and formatting, mypy, the isolated local suite, PostgreSQL integration/concurrency tests, Alembic upgrade/check, Helm lint/render, Docker builds, and Trivy scans. Release deployment is tag-triggered (`v*`) or manually started and runs the same verification jobs before publishing immutable Git-SHA and release-version images to GHCR. It requires the protected GitHub `production` environment, secrets `KUBECONFIG_B64`, `JWT_SECRET`, `POSTGRES_PASSWORD`, and variable `KUBERNETES_NAMESPACE`. Configure required reviewers and restrict environment secret access in GitHub settings; no credentials belong in source.

The chart provides optional HPA, PodDisruptionBudget, NetworkPolicy and TLS Ingress controls. Local Kind is a single-node functional test, not high availability. Production requires a multi-node managed Kubernetes cluster, independent PostgreSQL availability and storage, a tested backup/restore destination, metrics server, ingress controller/TLS, and platform-specific network and secret policies. Validate those options against the target cluster before enabling them.
