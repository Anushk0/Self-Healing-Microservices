# ledger-service (port 8083)

Owns the financial state: accounts and transactions in PostgreSQL. Root cause **and** symptom
service for the **db-lock** fault.

**This is the service the Safety Validator protects most.** `LedgerService.debit()`/`credit()` are
`@Transactional` and lock the account row with `SELECT ... FOR UPDATE`, so a restart mid-transaction
is genuinely dangerous - which is why `safety/validator.py` refuses any `risk_level="high"` action
targeting this service, including the Week 10 escalation ladder's pod restart.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/accounts` | Create an account |
| GET | `/api/accounts/{accountId}` | Fetch balance |
| POST | `/api/ledger/debit` | Debit (transactional, row-locked) |
| POST | `/api/ledger/credit` | Credit (transactional, row-locked) |
| GET | `/actuator/health` | Liveness/readiness |
| GET | `/actuator/prometheus` | Metrics scrape target |
| POST | `/inject-db-lock` | Fault injection (off by default) |
| POST | `/reset-db-lock` | Reset the fault |
| GET | `/fault-status` | Read-only fault flag |

## Run

```bash
# Native (needs a local PostgreSQL)
mvn spring-boot:run

# Docker Compose (from the repo root - brings up Postgres too)
docker compose up -d --build

# Kubernetes
kubectl apply -f k8s/
```

HikariCP `maximum-pool-size` is deliberately small (`DB_POOL_MAX_SIZE`, default 10) so the db-lock
fault can exhaust it quickly and visibly.

## Kubernetes commands you will actually need

```bash
kubectl get pods -l app=ledger-service
kubectl logs -f deployment/ledger-service
kubectl port-forward svc/ledger-service 8083:8083
kubectl describe pod -l app=ledger-service
kubectl exec -it deployment/ledger-service -- sh
kubectl rollout restart deployment/ledger-service

# Postgres
kubectl get pvc                                    # postgres-pvc should be Bound
kubectl exec -it deployment/postgres -- psql -U aiops -d ledger_db -c "select * from ledger_transactions order by id desc limit 5;"
```

Note: on first deploy this pod may restart a couple of times while Postgres becomes ready. That is
normal Kubernetes self-healing, not a failure - it stabilises on its own.

## Tests

This is the only service whose tests need a real database — 18 tests across
`LedgerServiceTest` (7), `DbLockFaultServiceTest` (6) and `LedgerControllerTest` (5). They run
against PostgreSQL rather than an in-memory database on purpose: the behaviour under test is
`SELECT ... FOR UPDATE` row locking and HikariCP pool exhaustion, neither of which H2 reproduces
faithfully.

Requirements: **JDK 17** (see the root README — the build does not work on JDK 23), Maven, and a
PostgreSQL reachable with a `ledger_db_test` database. Schema is created automatically
(`ddl-auto: create-drop`).

### Option A — throwaway instance (recommended; leaves any existing PostgreSQL alone)

Creates a temporary cluster on port **5433**, so a PostgreSQL already running on 5432 is untouched
and no superuser password is needed — `initdb` makes you the superuser of the new cluster.

```bash
PGBIN="/c/Program Files/PostgreSQL/18/bin"     # adjust version
TMP=/tmp/pgdata-ledger-test

"$PGBIN/initdb" -D "$TMP" -U aiops --auth=trust -E UTF8
"$PGBIN/pg_ctl" -D "$TMP" -l /tmp/pg.log -o "-p 5433 -c listen_addresses=localhost" start
"$PGBIN/psql" -h localhost -p 5433 -U aiops -d postgres -c "CREATE DATABASE ledger_db_test;"

TEST_DB_URL=jdbc:postgresql://localhost:5433/ledger_db_test \
TEST_DB_USERNAME=aiops TEST_DB_PASSWORD= \
  mvn test

"$PGBIN/pg_ctl" -D "$TMP" stop -m fast && rm -rf "$TMP"
```

### Option B — an existing local PostgreSQL

Needs superuser access once, to create the role and database the tests expect:

```bash
psql -U postgres -c "CREATE ROLE aiops WITH LOGIN PASSWORD 'aiops-dev-password' CREATEDB;"
psql -U postgres -c "CREATE DATABASE ledger_db_test OWNER aiops;"
mvn test          # defaults already point at localhost:5432/ledger_db_test as user aiops
```

`TEST_DB_URL`, `TEST_DB_USERNAME` and `TEST_DB_PASSWORD` override the defaults in
`src/test/resources/application.yml` if your setup differs.

### The other three services

`auth-service`, `payment-service` and `notification-service` need no database — plain `mvn test`.
