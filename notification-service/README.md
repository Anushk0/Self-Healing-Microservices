# notification-service (port 8084)

Simulates sending payment receipts - it logs and returns, with no real email/SMS integration and no
database. Root cause service for the **notification-latency** fault.

This is the project's headline demo scenario: injecting latency here makes the *symptom* appear on
payment-service (whose synchronous call to this service approaches its ~5s client timeout), while
the actual cause is here. Correctly naming notification-service rather than payment-service is the
case the RCA agent has to get right.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/notifications/receipt` | Simulate sending a receipt |
| GET | `/actuator/health` | Liveness/readiness |
| GET | `/actuator/prometheus` | Metrics scrape target |
| POST | `/inject-latency` | Fault injection (off by default; accepts `{"delayMs": 6000}`) |
| POST | `/reset-latency` | Reset the fault |
| GET | `/fault-status` | Read-only fault flag |

## Run

```bash
# Native
mvn spring-boot:run

# Docker Compose (from the repo root)
docker compose up -d --build

# Kubernetes
kubectl apply -f k8s/
```

## Kubernetes commands you will actually need

```bash
kubectl get pods -l app=notification-service
kubectl logs -f deployment/notification-service
kubectl port-forward svc/notification-service 8084:8084
kubectl describe pod -l app=notification-service
kubectl exec -it deployment/notification-service -- sh
kubectl rollout restart deployment/notification-service
kubectl delete pod -l app=notification-service     # what the Week 10 executor does for a restart
```

## Demonstrating the headline fault

```bash
curl -X POST localhost:8084/inject-latency -H 'Content-Type: application/json' -d '{"delayMs":6000}'
# ...now run a payment; it will take ~5s and time out on the notification call
curl -X POST localhost:8084/reset-latency
```

Always reset afterwards (CLAUDE.md rule 19). `GET /fault-status` confirms the flag is clear.

## Tests

```bash
mvn test
```
