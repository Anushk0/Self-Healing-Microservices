"""Configuration, paths, and the per-service feature schema for Week 7's AI orchestration
foundation.

Reuses Week 5/6 code (`telemetry`, `dataset_tools`) rather than duplicating JSONL-loading or
phase-reconstruction logic - this module makes both importable by inserting the repo root onto
`sys.path`, the same pattern `dataset-tools/src/dataset_tools/generate.py` already established for
importing `telemetry` from a sibling package.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def ensure_sibling_packages_importable() -> None:
    """Adds the repo root to sys.path so `import telemetry` and `import dataset_tools` resolve,
    without requiring either to be pip-installed. Idempotent - safe to call repeatedly."""
    repo_root_str = str(REPO_ROOT)
    if repo_root_str not in sys.path:
        sys.path.insert(0, repo_root_str)
    dataset_tools_src = str(REPO_ROOT / "dataset-tools" / "src")
    if dataset_tools_src not in sys.path:
        sys.path.insert(0, dataset_tools_src)


AI_ORCHESTRATION_DIR = Path(os.environ.get("AI_ORCHESTRATION_DIR", str(REPO_ROOT / "ai-orchestration")))
MODELS_DIR = Path(os.environ.get("AI_ORCHESTRATION_MODELS_DIR", str(AI_ORCHESTRATION_DIR / "models")))

# --- Feature schema -----------------------------------------------------------------------
#
# One ordered feature list per service, not one shared list across all four - some curated
# metrics are only meaningful for specific services (verified against real telemetry, same
# empirical standard dataset-tools/config.py's FAULT_SERVICE_MAP uses):
#   - hikaricp_connections_* / acquire_seconds_avg: only ledger-service reports these
#     (HikariCP is a database connection pool; auth/payment/notification-service don't hold one).
#   - http_client_requests_*: only payment-service makes outbound HTTP calls (to auth-service and
#     notification-service) - see telemetry/collector.py's parse_metrics comments.
# Training one Isolation Forest per service on only its own applicable features avoids treating
# "always None for this service" as a real signal, and keeps each service's model scoped to its
# own baseline distribution (JVM baseline ranges differ significantly by service - see
# dataset-tools/src/dataset_tools/synthetic_model.py's OBSERVED_BASELINE_RANGES).

_BASE_FEATURES = [
    "jvm_memory_used_bytes",
    "jvm_memory_committed_bytes",
    "jvm_memory_max_bytes",
    "http_server_requests_count",
    "http_server_requests_error_count",
    "http_server_requests_avg_duration_ms",
    "process_cpu_usage",
    "system_cpu_usage",
]

_HIKARICP_FEATURES = [
    "hikaricp_connections_active",
    "hikaricp_connections_idle",
    "hikaricp_connections_pending",
    "hikaricp_connections_acquire_seconds_avg",
]

_HTTP_CLIENT_FEATURES = [
    "http_client_requests_count",
    "http_client_requests_avg_duration_ms",
]

SERVICE_FEATURE_SCHEMAS: dict[str, list[str]] = {
    "auth-service": list(_BASE_FEATURES),
    "payment-service": list(_BASE_FEATURES) + _HTTP_CLIENT_FEATURES,
    "ledger-service": list(_BASE_FEATURES) + _HIKARICP_FEATURES,
    "notification-service": list(_BASE_FEATURES),
}

FEATURE_SCHEMA_VERSION = "v1"

# --- Isolation Forest hyperparameters (env-overridable, reproducible defaults) -------------

ISOLATION_FOREST_N_ESTIMATORS = int(os.environ.get("AI_ORCHESTRATION_IF_N_ESTIMATORS", "100"))
# sklearn's "auto" contamination uses a fixed -0.5 score offset, tuned for much larger datasets
# than this project currently has (only 4 real experiments / ~42 samples per service are
# Memory-eligible right now - see ai-orchestration/README.md "Known limitations"). Live testing
# against the real dataset showed "auto" flagged nearly everything as anomalous; an explicit,
# more conservative value is a more defensible default until more real experiments accumulate.
ISOLATION_FOREST_CONTAMINATION = os.environ.get("AI_ORCHESTRATION_IF_CONTAMINATION", "0.1")
ISOLATION_FOREST_RANDOM_STATE = int(os.environ.get("AI_ORCHESTRATION_IF_RANDOM_STATE", "42"))
ISOLATION_FOREST_MAX_SAMPLES = os.environ.get("AI_ORCHESTRATION_IF_MAX_SAMPLES", "auto")

# Anomaly score convention: score = -decision_function(x) (so *higher* = *more anomalous*,
# matching the intuitive meaning of "anomaly_score" in the structured output). sklearn's own
# inlier/outlier boundary is score == 0.0; ANOMALY_SCORE_THRESHOLD lets that boundary be tuned
# without retraining, per Week 7's "make anomaly thresholds configurable" requirement.
ANOMALY_SCORE_THRESHOLD = float(os.environ.get("AI_ORCHESTRATION_ANOMALY_THRESHOLD", "0.0"))

# --- Week 10: execution, recovery verification, feedback write-back ------------------------

# Hard cap on how many remediation actions may be executed for one incident before the pipeline
# escalates to a human and stops retrying (Week 8 roadmap: "Cap retries (e.g., 3) to avoid
# infinite loops"). Counts *executed* attempts, not proposals.
MAX_REMEDIATION_ATTEMPTS = int(os.environ.get("AI_ORCHESTRATION_MAX_REMEDIATION_ATTEMPTS", "3"))

# LangGraph's own default is 25 supersteps, which the Week 10 retry loop can come uncomfortably
# close to on a full three-attempt incident - see graph/workflow.py::default_run_config.
GRAPH_RECURSION_LIMIT = int(os.environ.get("AI_ORCHESTRATION_GRAPH_RECURSION_LIMIT", "60"))

# When true, the executor resolves and logs exactly what it *would* do and returns a normal
# ExecutionResult with `dry_run=True`, without issuing the HTTP reset call or touching Kubernetes.
# Default false: an approved action is supposed to actually execute (that is the entire point of
# Week 10), and execution is already gated behind the Safety Validator plus mandatory human
# approval. Set to "true" to rehearse the pipeline against a live cluster without changing it.
EXECUTION_DRY_RUN = os.environ.get("AI_ORCHESTRATION_EXECUTION_DRY_RUN", "false").lower() == "true"

EXECUTION_HTTP_TIMEOUT_SECONDS = float(os.environ.get("AI_ORCHESTRATION_EXECUTION_HTTP_TIMEOUT_SECONDS", "10"))

# Kubernetes namespace the four service Deployments live in (k8s/*.yaml apply to the current
# namespace, which is "default" on a stock Minikube).
KUBERNETES_NAMESPACE = os.environ.get("AI_ORCHESTRATION_KUBERNETES_NAMESPACE", "default")

# Post-execution recovery verification: re-check the target service's own /fault-status and
# /actuator/health after acting, rather than treating "the API call returned 200" as recovery.
# This is what makes Week 12's Recovery Success Rate and False Recovery Rate measurable at all.
RECOVERY_VERIFICATION_ATTEMPTS = int(os.environ.get("AI_ORCHESTRATION_RECOVERY_VERIFICATION_ATTEMPTS", "5"))
RECOVERY_VERIFICATION_INTERVAL_SECONDS = float(
    os.environ.get("AI_ORCHESTRATION_RECOVERY_VERIFICATION_INTERVAL_SECONDS", "3")
)

# Feedback write-back into ChromaDB. Disable during Week 12's evaluation runs so the retrieval
# memory stays frozen and identical for both the multi-agent pipeline and the single-agent
# baseline - otherwise earlier evaluation runs would change what later ones can retrieve, and the
# comparison would not be reproducible.
FEEDBACK_ENABLED = os.environ.get("AI_ORCHESTRATION_FEEDBACK_ENABLED", "true").lower() == "true"

# --- Ollama ---------------------------------------------------------------------------------

OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
# CLAUDE.md names llama3.1:8b as the project's initial candidate local model; the default here is
# a much smaller model chosen for this development machine's limited free disk space (see
# ai-orchestration/README.md "Ollama setup" for the exact tradeoff and how to switch to 8b).
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.2:1b")
OLLAMA_TIMEOUT_SECONDS = float(os.environ.get("OLLAMA_TIMEOUT_SECONDS", "30"))
