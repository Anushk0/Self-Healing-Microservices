# auth-service (port 8081)

Issues and validates JWTs. Root cause service for the **auth-key-error** fault - note that the
symptom of that fault is measured on payment-service, not here (see
`dataset_tools.config.FAULT_SERVICE_MAP` for why: this service's `/api/auth/validate` returns HTTP
200 even for an invalid token, reporting validity in the body, so its own error-rate metrics never
move during the fault).

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/auth/login` | Authenticate, issue a JWT |
| POST | `/api/auth/validate` | Validate a JWT, return claims |
| GET | `/actuator/health` | Liveness/readiness |
| GET | `/actuator/prometheus` | Metrics scrape target |
| POST | `/inject-auth-key-error` | Fault injection (off by default) |
| POST | `/reset-auth-key` | Reset the fault |
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

## Kubernetes commands you will actually need

```bash
kubectl get pods -l app=auth-service
kubectl logs -f deployment/auth-service
kubectl port-forward svc/auth-service 8081:8081
kubectl describe pod -l app=auth-service        # why a pod is not starting
kubectl exec -it deployment/auth-service -- sh  # shell inside the container
kubectl rollout restart deployment/auth-service # restart without deleting the Deployment
kubectl delete pod -l app=auth-service          # what the Week 10 executor does for a restart
```

The `aiops-secrets` Secret must exist before `kubectl apply` (it holds `JWT_SECRET`); it is created
imperatively and deliberately not committed - see the root `README.md`.

## Correlation IDs

`config/CorrelationIdFilter.java` reads `X-Correlation-Id` (generating a UUID if absent), puts it in
SLF4J MDC as `correlationId`, echoes it on the response, and clears it in a `finally` block. To
follow one request across all four services:

```bash
kubectl logs deployment/auth-service | grep <correlation-id>
```

## Tests

```bash
mvn test
```
