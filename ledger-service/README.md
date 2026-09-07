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

```bash
mvn test   # requires the ledger_db_test database to exist
```
