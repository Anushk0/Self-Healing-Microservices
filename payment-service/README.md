# payment-service (port 8082)

Orchestrates the full payment chain: validates the caller's JWT against auth-service, debits via
ledger-service, then calls notification-service. Root cause service for the **memory-leak** fault,
and the *symptom-visible* service for two others (**auth-key-error** and **notification-latency**) -
which is exactly what makes root-cause analysis non-trivial in this project.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/payments` | Orchestrate the full payment chain |
| GET | `/actuator/health` | Liveness/readiness |
| GET | `/actuator/prometheus` | Metrics scrape target |
| POST | `/inject-memory-leak` | Fault injection (off by default) |
| POST | `/reset-memory-leak` | Reset the fault |
| GET | `/fault-status` | Read-only fault flag |

See `docs/postman/aiops-capstone.postman_collection.json` for ready-to-send requests.

## Run

```bash
# Native
mvn spring-boot:run

# Docker Compose (from the repo root, brings up all four services + Postgres)
docker compose up -d --build

# Kubernetes
kubectl apply -f k8s/
```

Downstream base URLs come from environment variables (`AUTH_SERVICE_BASE_URL`,
`LEDGER_SERVICE_BASE_URL`, `NOTIFICATION_SERVICE_BASE_URL`) so the same image runs unchanged against
localhost, Compose service names, and Kubernetes Service DNS names.

## Kubernetes commands you will actually need

```bash
kubectl get pods -l app=payment-service
kubectl logs -f deployment/payment-service
kubectl port-forward svc/payment-service 8082:8082
kubectl describe pod -l app=payment-service
kubectl exec -it deployment/payment-service -- sh
kubectl rollout restart deployment/payment-service
kubectl delete pod -l app=payment-service          # what the Week 10 executor does for a restart
```

## Notification failures are non-fatal, by design

`NotificationServiceClient` catches its own failures and returns `NotificationResult.failed()`
rather than propagating - a payment is not rolled back because a receipt could not be sent. The
call is still made *synchronously*, which is what lets the notification-latency fault surface as a
payment-side timeout.

## Tests

```bash
mvn test
```
