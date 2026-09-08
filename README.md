# AIOps Self-Healing Microservices Platform

Academic capstone project. See `CLAUDE.md` for the full permanent project
rules and weekly roadmap.

## Status

**Weeks 15-16 complete — the roadmap is finished**, except for two items that need a person or a
cluster (supervisor feedback, and a live-fault re-run).

The paper is complete end to end: abstract, introduction, dataset construction, methodology,
results, discussion, related work, ablations, conclusion, five figures, a defence runbook and a
claim-by-claim novelty audit — in `docs/paper/`, with the evidence in `docs/evaluation/`.

**The ablations changed the paper's claims, in an unflattering direction that is now reported as
such.** Disabling retrieval entirely left all 48 multi-agent diagnoses *identical* — 100% when
answered, 100% cross-service, F1 1.00. Retrieval contributes zero diagnostic accuracy here; it moves
only the confidence label. That sharpens rather than weakens the headline: the advantage came
entirely from *refusing to defer* to retrieval, not from anything retrieval supplied. For the
single-agent baseline the same change collapsed same-service accuracy 100%→0% while *improving*
cross-service accuracy 0%→33% — retrieval was substituting for reasoning.

**The novelty audit weakened two claims and removed one.** Unsafe-action prevention was cut from the
contributions because the measured count is 0 — dry-run execution never failed, so the rule never
had occasion to fire, and reporting it as demonstrated would have been unearned.

Weeks 12-14 complete: the evaluation ran and the paper sections are drafted from it.

**The result.** On 48 held-out incidents, the multi-agent pipeline was correct on **100% of the
incidents it answered (21/21)** versus 57.1% for the single-agent baseline — and on the
cross-service faults this project was built around (where the service showing the symptom is not the
service at fault), **100% (9/9) versus 0% (0/9)**. The two arms were *identical* on same-service
faults, so the whole difference sits in exactly the cases the dataset was designed to isolate.

**Why.** On every cross-service incident, retrieval disagreed with the correct answer — it returned
incidents rooted at the symptom service. The baseline followed it and named the symptom service all
9 times; the multi-agent pipeline did not, because its architecture forbids retrieval from
overriding a signature match. The design rule "retrieved incidents are evidence, not commands" is
the entire measured difference between the arms.

**Stated as prominently as the result:** this run's corpus is fully synthetic (no cluster on the run
machine), and the diagnostic thresholds and the synthetic generator share constants — so the
absolute 100% is bounded by that circularity, though the comparison is not. Detection, not
diagnosis, was the real ceiling: both arms abstained on 56.2% of incidents. MTTR, Recovery Success
Rate and False Recovery Rate are **not measurable by replay** and are reported as unmeasurable
rather than as a fabricated 100%.

**A finding that changes the related-work section:** arXiv 2511.15755, named in the roadmap as the
closest competitor, has been **withdrawn by its author** after an audit found its multi-agent arm's
action list was a source constant rather than model output. Since that is the natural failure mode
of this architecture too, `docs/paper/05-related-work.md` §5.2 carries an empirical self-audit
proving this system's scored output varies with input telemetry, with a permanent regression test.

Artefacts: `docs/evaluation/` (results, metrics, per-incident CSV, charts) and `docs/paper/`
(dataset construction, methodology, results, discussion, related work, figures). 283 tests pass.

Weeks 10-11 complete: the pipeline now closes the loop and has a comparison arm.
**Week 10** executes an approved action against the running system (fault reset over HTTP, or a pod
restart via the Kubernetes API - three fail-closed gates, no generic command path), then re-observes
the service to check whether the fault actually cleared, escalates a failed or falsely-successful
fix through a deterministic ladder where *every retry needs its own fresh approval*, and writes the
finished incident back into retrieval memory with Evaluation-set incidents hard-refused (rule 22,
enforced twice independently). **Week 11** adds a separate single-agent baseline - one LLM call for
diagnosis and action - that shares every non-diagnostic node with the multi-agent arm, so the Week
12 comparison isolates agent decomposition rather than retrieval or downstream safety.

Also closed two gaps from earlier weeks: the Postman collection specified in Weeks 1-2
(`docs/postman/`, 31 requests) and the per-service Kubernetes READMEs from Week 3.

Live-verified by driving the console in a real browser: with no services and no cluster running,
every remediation genuinely failed, which exercised the whole escalation ladder under real failure
conditions - three real execution attempts, three separate approval pauses, terminal escalation
executing nothing, MTTR 53.4s, and the feedback loop demonstrably closing (a later run retrieved
incidents earlier runs had learned). That session also surfaced a measured finding: Isolation Forest
*saturates* outside its training range - a 2 GB heap scores identically to a 50 MB one - which is
documented as a limitation with a concrete mechanism rather than tuned away. 249 Python tests pass.
See `ai-orchestration/README.md` for full architecture and live-verification detail.

Week 9 complete: `ai-orchestration/` adds a deterministic Safety
Validator (zero AI/LLM calls) and a genuinely mandatory human-approval
gate via LangGraph's `interrupt()` - the graph cannot proceed past it
without an external approve/reject signal. A rejected-by-safety
proposal never reaches a human at all; a passed one pauses until `app.py`
(new Streamlit console) or a test resumes it. Live-verified this week by
driving the Streamlit console end-to-end in a real browser (no live
Kubernetes deployment on this machine): a full pause → Approve cycle and
a separate pause → Reject cycle, both with typed reasons that
round-tripped correctly into the final result. That session also caught
and fixed a real bug in the demo detector's synthetic training data
(a zero-variance feature that stopped Isolation Forest from ever
learning to split on it). See `ai-orchestration/README.md` for full
architecture and live-verification detail.

Week 8 complete: `ai-orchestration/` extends Week 7's LangGraph workflow
with an RCA agent, RAG retrieval against Week 6's ChromaDB Memory
collection, and a deterministic remediation-proposal generator - Week 8
never executes anything (`RemediationProposal.requires_human_approval`
is `True` at the type level, permanently). RCA determines root cause via
deterministic fault-signature matching first (correctly distinguishing
symptom-visible service from actual root-cause service for the two
cross-service faults), cross-checked against - but never overridden by -
retrieved historical incidents, with an optional LLM narrative that
cannot change the conclusion. Live-verified against brand-new
notification-latency and db-lock experiments this week; the
notification-latency run surfaced and honestly documents a real
limitation (a cumulative-counter artifact caused one RCA
misattribution), while the db-lock run produced a fully correct
end-to-end result. See `ai-orchestration/README.md` for full
architecture and live-verification detail.

Week 7 complete: `ai-orchestration/` (Python) adds the AI intelligence
foundation - one Isolation Forest per service trained on Memory-eligible
real baseline telemetry (never Evaluation-set data, enforced in code), a
local Ollama LLM integration for structured anomaly interpretation
(graceful degradation if unavailable, never executes actions), and a
real LangGraph workflow (`extract_features -> detect_anomaly -> normal |
anomaly -> interpret -> finalize`) that Week 8's RCA/RAG/remediation
agents will extend. See `ai-orchestration/README.md` for full
architecture, the feature schema, live verification results, and known
limitations (small real training set; cumulative-counter features).

Week 6 complete: `dataset-tools/` (Python) turns Week 5's raw telemetry
into a 68-incident labeled dataset (16 real, 52 deterministic synthetic —
both clearly labeled, never conflated), splits it deterministically
(seed 42, stratified by fault type) into a 20-incident Memory set and a
48-incident Evaluation set, and builds a local, persistent ChromaDB
collection from the Memory set only. The Evaluation/Memory separation is
enforced in code (ChromaDB's indexing path never opens the Evaluation
file, and independently double-checks for ID overlap before and after
indexing), not just by convention. See `dataset-tools/README.md` for the
full architecture, schema, and how to reproduce it.

Week 5 complete: an automated telemetry collector (`telemetry/`, Python)
continuously captures health, curated Prometheus metrics, fault state, and
synthetic end-to-end payment attempts from all four services into
structured JSONL files, and can orchestrate a full NORMAL → FAULT →
RECOVERY experiment against any of the four controlled faults. See
`telemetry/README.md` for the full design, schema, and how to run it.

Week 4 complete: all four services expose Spring Boot Actuator + Prometheus
metrics, Prometheus itself runs in-cluster scraping all four, Kubernetes
readiness/liveness probes are live on all four app Deployments, and all
four controlled faults (auth key error, payment memory leak, ledger DB pool
exhaustion, notification latency) are implemented, disabled by default, and
verified end-to-end (native, Docker Compose, and Kubernetes).

## Prerequisites

**PostgreSQL** (native, via Homebrew) — still used for `ledger-service`'s
local `mvn test` run outside of Docker/Kubernetes:

```bash
brew install postgresql@16
brew services start postgresql@16
```

One-time setup of the app role and databases:

```bash
PATH="/opt/homebrew/opt/postgresql@16/bin:$PATH"
psql -d postgres -c "CREATE ROLE aiops WITH LOGIN PASSWORD 'aiops-dev-password' CREATEDB;"
psql -d postgres -c "CREATE DATABASE ledger_db OWNER aiops;"
psql -d postgres -c "CREATE DATABASE ledger_db_test OWNER aiops;"  # used by ledger-service's test suite
```

**Docker** — via [Colima](https://github.com/abiosoft/colima) (not Docker
Desktop, to keep everything CLI-driven with no GUI/license click-through):

```bash
brew install colima docker docker-compose docker-buildx kubectl minikube
colima start --cpu 4 --memory 6 --disk 20
```

`docker compose`/`docker buildx` need Homebrew's plugin directory registered once in `~/.docker/config.json`:

```json
{ "cliPluginsExtraDirs": ["/opt/homebrew/lib/docker/cli-plugins"] }
```

## Services

### auth-service (port 8081)

Issues and validates JWTs against a hardcoded (in-memory) user store.
This is a placeholder user store, intentionally kept simple for Week 1;
it will be replaced with a persisted store in a later week.

Seeded users:

| username | password |
|---|---|
| alice | alice-pass |
| bob | bob-pass |

Endpoints:

- `POST /api/auth/login` — `{"username": "...", "password": "..."}` → `{"token": "...", "tokenType": "Bearer", "expiresInMs": ...}`
- `POST /api/auth/validate` — `{"token": "..."}` → `{"valid": bool, "username": "...", "roles": [...], "error": "..."}`

Configuration (env vars): `SERVER_PORT` (8081), `JWT_SECRET` (dev-only default — override outside local dev), `JWT_EXPIRATION_MS` (3600000).

**Fault injection** (disabled by default — see "Fault injection" below): `POST /inject-auth-key-error`, `POST /reset-auth-key`, `GET /fault-status` (read-only, added Week 5 for the telemetry collector).

### ledger-service (port 8083)

Manages accounts and performs debit/credit operations against PostgreSQL
inside real `@Transactional` boundaries (with pessimistic row locking, so
concurrent operations on the same account serialize correctly).

Seeds one demo account, `acct-42`, with a balance of 1000.00 USD on
startup (disable via `SEED_DEMO_ACCOUNT=false`) — a placeholder in the same
spirit as auth-service's hardcoded user store, since there's no account
provisioning flow yet.

Endpoints:

- `POST /api/accounts` — `{"accountId", "currency", "initialBalance"}` → `201` `{"accountId", "currency", "balance"}`
- `GET /api/accounts/{accountId}` → `{"accountId", "currency", "balance"}` or `404`
- `POST /api/ledger/debit` — `{"accountId", "currency", "amount"}` → `200` transaction record, `404` unknown account, `409` insufficient funds
- `POST /api/ledger/credit` — same shape, adds instead of subtracting

Configuration (env vars): `SERVER_PORT` (8083), `DB_URL`, `DB_USERNAME`, `DB_PASSWORD`, `DB_POOL_MAX_SIZE` (10 — matches the Week 4 HikariCP fault-injection spec), `SEED_DEMO_ACCOUNT`.

**Fault injection** (disabled by default): `POST /inject-db-lock`, `POST /reset-db-lock`, `GET /fault-status` — holds up to `DB_LOCK_CONNECTIONS_TO_HOLD` (default 9, always clamped below the pool max) raw connections from the pool.

### notification-service (port 8084)

Simulates sending a payment receipt — logs the activity and returns a
`SENT` status. No real email/SMS integration, no persistence.

Endpoint:

- `POST /api/notifications/receipt` — `{"accountId", "currency", "amount", "transactionId", "recipientUsername"}` → `{"notificationId", "status", "sentAt"}`

Configuration (env vars): `SERVER_PORT` (8084).

**Fault injection** (disabled by default): `POST /inject-latency` (optional body `{"delayMs": N}`, default `NOTIFICATION_LATENCY_DEFAULT_DELAY_MS`=6000), `POST /reset-latency`, `GET /fault-status`.

### payment-service (port 8082)

Orchestrates the full synchronous chain: validates the caller's JWT with
auth-service, debits the account via ledger-service, then sends a receipt
via notification-service.

A notification failure is **not** treated as a payment failure — the money
has already moved by that point, so it's logged and reported back as
`notificationStatus: "FAILED"` rather than rolling back or erroring the
whole request. This asymmetry (Payment Service can be slowed/blocked by a
downstream Notification problem without the transaction itself failing) is
intentional groundwork for the Week 4+ fault-injection scenario where a
Notification-service root cause manifests as a Payment-service symptom.

Endpoint:

- `POST /api/payments` — header `Authorization: Bearer <jwt>`, body `{"accountId", "currency", "amount"}` → `{"status", "message", "authenticatedUser", "ledgerTransactionId", "notificationStatus"}`

Configuration (env vars): `SERVER_PORT` (8082), `AUTH_SERVICE_URL`, `LEDGER_SERVICE_URL`, `NOTIFICATION_SERVICE_URL` (+ matching `*_TIMEOUT_MS` for each, default 5000).

**Fault injection** (disabled by default): `POST /inject-memory-leak`, `POST /reset-memory-leak`, `GET /fault-status` — retains `MEMORY_LEAK_CHUNK_SIZE_BYTES` (default 5MB) chunks every `MEMORY_LEAK_INTERVAL_MS` (default 1s), hard-capped at `MEMORY_LEAK_MAX_TOTAL_BYTES` (default 200MB).

### Observability (Actuator + Prometheus)

All four services expose Spring Boot Actuator: `/actuator/health`,
`/actuator/health/liveness`, `/actuator/health/readiness`,
`/actuator/info`, `/actuator/metrics`, `/actuator/prometheus`.
`management.endpoint.health.show-details: always` — fine for this
local/academic project, not multi-tenant production. Kubernetes
readiness/liveness probes on all four app Deployments use the
`/liveness`/`/readiness` sub-paths (15s/30s initial delay, tuned from
observed real startup times — no restart loops seen in testing).

A Prometheus instance runs inside the cluster (`k8s/prometheus-config.yaml`,
`k8s/prometheus.yaml`) scraping all four services' `/actuator/prometheus`.
It's ClusterIP-only, never exposed outside the cluster — reach it via:

```bash
kubectl port-forward svc/prometheus 9090:9090
```

Then `http://localhost:9090/targets` should show all four jobs `up`.

### Fault injection

Four controlled faults exist, one per service, matching CLAUDE.md's "Fault
injection" section. **All are disabled by default, safe, and resettable.**
Each has an `/inject-*` and matching `/reset-*` endpoint (see each
service's section above for exact endpoints/config). A quick end-to-end
demo of any fault:

```bash
# Auth key error
curl -X POST http://localhost:8081/inject-auth-key-error
curl -X POST http://localhost:8081/api/auth/validate -H "Content-Type: application/json" -d '{"token":"<a token from before the fault>"}'  # now invalid
curl -X POST http://localhost:8081/reset-auth-key

# Memory leak (watch it rise)
curl -X POST http://localhost:8082/inject-memory-leak
curl http://localhost:8082/actuator/metrics/jvm.memory.used
curl -X POST http://localhost:8082/reset-memory-leak

# DB pool exhaustion
curl -X POST http://localhost:8083/inject-db-lock
curl http://localhost:8083/actuator/prometheus | grep hikaricp_connections
curl -X POST http://localhost:8083/reset-db-lock

# Notification latency (payment will time out around 5s, ledger still commits)
curl -X POST http://localhost:8084/inject-latency
curl -X POST http://localhost:8082/api/payments -H "Authorization: Bearer <token>" -H "Content-Type: application/json" -d '{"accountId":"acct-42","currency":"USD","amount":1.00}'
curl -X POST http://localhost:8084/reset-latency
```

The notification-latency fault needs no extra timeout config on the
Payment side — `app.notification-service.timeout-ms` (5000ms, from Week 2)
is already shorter than the fault's default 6000ms delay.

### Correlation IDs

All four services read `X-Correlation-Id` from the incoming request
(generating a UUID if absent), attach it to all log lines via SLF4J MDC,
return it on the response, and propagate it to every downstream call
(payment-service → auth-service / ledger-service / notification-service).
Verified end-to-end: the same correlation ID appears in all four services'
logs for a single payment request. See `CLAUDE.md` for the full convention.

## Running locally

Each service is an independent Maven project. Start PostgreSQL first (see
Prerequisites), then in four terminals:

```bash
cd auth-service && mvn spring-boot:run
cd ledger-service && DB_PASSWORD=aiops-dev-password mvn spring-boot:run
cd notification-service && mvn spring-boot:run
cd payment-service && mvn spring-boot:run
```

Try the full chain:

```bash
TOKEN=$(curl -s -X POST http://localhost:8081/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"alice","password":"alice-pass"}' | python3 -c "import sys,json;print(json.load(sys.stdin)['token'])")

curl -i -X POST http://localhost:8082/api/payments \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"accountId":"acct-42","currency":"USD","amount":150.25}'

curl http://localhost:8083/api/accounts/acct-42   # confirm the balance moved
```

## Running with Docker Compose

Builds and runs all four services plus a PostgreSQL container, fully wired
together on one Docker network:

```bash
docker compose up -d --build
```

Same endpoints as native (`localhost:8081`/`8082`/`8083`/`8084`). The
compose Postgres is exposed on host port **5433** (not 5432) so it doesn't
collide with the native Homebrew Postgres from the Prerequisites section —
containers still reach it internally via `postgres:5432`. `JWT_SECRET` and
`DB_PASSWORD` can be overridden via a `.env` file; they default to the same
dev-only placeholder values used elsewhere in this repo.

```bash
docker compose down          # stop and remove containers (keeps the postgres-data volume)
docker compose logs -f       # follow all services' logs
```

## Running on Kubernetes (Minikube)

```bash
minikube start --driver=docker --cpus=2 --memory=3500mb
```

Build the images and load them into Minikube's internal image store (it
doesn't share the host/Colima Docker daemon):

```bash
docker compose build
for svc in auth-service ledger-service notification-service payment-service; do
  docker tag capstone_2026-$svc:latest aiops/$svc:0.1.0
done
```

**Important**: `minikube image load` can silently keep a stale image under
an already-existing tag — confirmed by comparing `docker inspect
aiops/<svc>:0.1.0 --format '{{.Id}}'` between the host daemon and `minikube
ssh -- docker inspect ...`. After the very first load this doesn't matter,
but on every *rebuild*, force-remove the old tag from Minikube's daemon
first, or the running pods can end up serving old code with no error at
apply time:

```bash
kubectl scale deployment auth-service ledger-service notification-service payment-service --replicas=0
for svc in auth-service ledger-service notification-service payment-service; do
  minikube ssh -- "docker rmi -f aiops/$svc:0.1.0" || true
  minikube image load aiops/$svc:0.1.0
done
kubectl scale deployment auth-service ledger-service notification-service payment-service --replicas=1
```

Create the secret the manifests reference (not committed to git — dev-only
placeholder values, same ones already used elsewhere in this repo):

```bash
kubectl create secret generic aiops-secrets \
  --from-literal=jwt-secret='dev-only-insecure-secret-key-change-me-1234567890' \
  --from-literal=db-password='aiops-dev-password'
```

Deploy everything and check status:

```bash
kubectl apply -f k8s/
kubectl get pods
```

`ledger-service` may restart once or twice on first boot if it starts
before postgres's readiness probe passes — Kubernetes' default restart
policy handles this without an explicit init-container wait, and it
settles once postgres is ready.

Reach the services from your host (each in its own terminal, or backgrounded):

```bash
kubectl port-forward svc/auth-service 8081:8081
kubectl port-forward svc/ledger-service 8083:8083
kubectl port-forward svc/notification-service 8084:8084
kubectl port-forward svc/payment-service 8082:8082
kubectl port-forward svc/prometheus 9090:9090
```

Then the same curl flow as above works against `localhost`. Postgres in
this setup uses a `PersistentVolumeClaim`, and all four app Deployments/
Services use the exact same env-var contract (`AUTH_SERVICE_URL`, `DB_URL`,
etc.) as native and Compose — just pointed at Kubernetes Service DNS names
instead of `localhost`/compose service names.

Tear down: `kubectl delete -f k8s/` (the PVC and its data persist unless
you also `kubectl delete pvc postgres-pvc`). `minikube stop` pauses the
cluster; `minikube delete` removes it entirely.

## Telemetry collection (Week 5)

A standalone Python collector (`telemetry/`) continuously captures health,
curated Prometheus metrics, fault state, and synthetic payment attempts
from all four services into JSONL files, and can run a full NORMAL →
FAULT → RECOVERY experiment against any of the four faults. It works
unmodified against native, Docker Compose, or Kubernetes (via
`kubectl port-forward` + env vars). Full details, data schema, and
commands: see [`telemetry/README.md`](telemetry/README.md).

```bash
cd telemetry
python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
python -m telemetry.experiment notification-latency --baseline-seconds 30 --fault-seconds 30 --recovery-seconds 30
```

## Dataset construction and ChromaDB retrieval memory (Week 6)

`dataset-tools/` builds the incident dataset used for RAG-based root-cause retrieval, from Week
5's raw telemetry. Full details: [`dataset-tools/README.md`](dataset-tools/README.md).

```bash
cd dataset-tools
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt && pip install -e .

python -m dataset_tools.build_dataset   # reconstruct real + generate synthetic incidents, split
python -m dataset_tools.chroma_store    # build the ChromaDB collection (Memory set only)
python -m dataset_tools.chroma_store query "payment service returning errors after auth failure"
```

## AI orchestration: anomaly detection, RCA, RAG, remediation proposals (Weeks 7-8)

`ai-orchestration/` trains per-service anomaly detectors, interprets anomalies via a local Ollama
model, then (Week 8) runs a root-cause analysis against the anomaly evidence, retrieves similar
historical incidents from Week 6's ChromaDB Memory collection, and generates a structured
remediation *proposal* - never executes it. All of this runs through one LangGraph workflow. Full
details: [`ai-orchestration/README.md`](ai-orchestration/README.md).

```bash
cd ai-orchestration
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt && pip install -e .

python -m ai_orchestration.anomaly.train    # train + persist all 4 services' models

brew install ollama && ollama serve &        # if not already installed/running
ollama pull llama3.2:1b                       # small model - see README for why, and how to switch
```

## Testing

```bash
cd auth-service && mvn test
cd ledger-service && mvn test          # needs Postgres running; uses the ledger_db_test database
cd notification-service && mvn test
cd payment-service && mvn test
```

`payment-service`'s tests use OkHttp MockWebServer to stand in for all
three downstream services, so they don't require any other service to be
running. `ledger-service`'s tests run against a real PostgreSQL database
(`ledger_db_test`), each wrapped in a rolled-back transaction so the schema
stays clean between runs.

```bash
cd telemetry && source .venv/bin/activate && python -m pytest tests/ -v
```

The telemetry collector's tests (19) mock all HTTP calls (via `responses`)
and don't require any service to be running.

```bash
cd dataset-tools && source .venv/bin/activate && python -m pytest tests/ -v
```

`dataset-tools`' tests (42) use a real local ChromaDB `PersistentClient`
against an isolated `tmp_path` per test - nothing is mocked - and don't
require any of the four microservices to be running.

```bash
cd ai-orchestration && source .venv/bin/activate && python -m pytest tests/ -v
```

`ai-orchestration`'s tests (90 - 44 Week 7 + 46 Week 8) mock Ollama and
use a real, isolated local ChromaDB (never the project's real Memory
collection) for RAG tests. None require a running Kubernetes cluster,
payment system, or Ollama server.
