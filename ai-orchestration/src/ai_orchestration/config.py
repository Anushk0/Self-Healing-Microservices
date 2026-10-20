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

# --- Week 16: ablation switches ---------------------------------------------------------------
#
# These exist so the two load-bearing design decisions can be *turned off and measured*, rather
# than defended in prose. Both default to the shipped behaviour; nothing changes unless an
# experiment sets them.
#
# RETRIEVAL_ENABLED=false answers "does RAG contribute anything?" by running the pipeline with an
# empty retrieval result - the roadmap's memory-ablation experiment.
RETRIEVAL_ENABLED = os.environ.get("AI_ORCHESTRATION_RETRIEVAL_ENABLED", "true").lower() == "true"

# RAG_MAY_OVERRIDE_SIGNATURE=true answers the sharper question: what would this system have scored
# if it had been built the conventional way, letting retrieved incidents override a deterministic
# signature match? The Week 12 results attribute the entire multi-agent/single-agent gap to the
# refusal to defer to retrieval, so this flag is what converts that attribution from an observation
# into a controlled result. Never enabled in the shipped configuration - retrieval overriding direct
# evidence is precisely the behaviour this project argues against.
RAG_MAY_OVERRIDE_SIGNATURE = os.environ.get(
    "AI_ORCHESTRATION_RAG_MAY_OVERRIDE_SIGNATURE", "false"
).lower() == "true"

# --- Detector training phases ------------------------------------------------------------------
#
# Which phases of a Memory-set incident count as "non-faulty" and may be used to train a detector.
#
# The shipped default is NORMAL + RECOVERY, and it is kept as the default so the published
# evaluation stays reproducible (rule 23). It is, however, known to be wrong, and the reason is
# worth stating precisely because it is the largest single cause of the reported 79.2% abstention
# rate:
#
#   `dataset_tools.synthetic_model` sets a memory-leak incident's RECOVERY heap to
#   `baseline + retained_final` while setting its FAULT heap to `baseline + retained_final / 2`
#   (the fault window is an average over a ramp; recovery is the final plateau). The recovery
#   phase is therefore *always* further from baseline than the fault window it follows - measured
#   at up to 7.2x baseline against roughly 4x during the fault.
#
#   Training on that teaches a detector that a leaked heap is normal. The evaluation's memory-leak
#   fault windows (45-139 MB) fall inside the range the model learned as healthy (up to 243 MB),
#   so they do not read as anomalous. Excluding RECOVERY from training raises detection from
#   10/48 to 19/48 and db-lock detection from 2/12 to 9/12 on the shipped corpus.
#
#   The same construction means the negative control's RECOVERY phase is not a healthy sample
#   either, so part of the reported 27.08% recovery-phase "false positive" rate is a detector
#   correctly flagging genuinely elevated heap.
#
# Set AI_ORCHESTRATION_TRAINING_PHASES=baseline_metrics to train on NORMAL only. Doing so changes
# the trained models and therefore every published number - regenerate the evaluation artefacts if
# you flip it.
TRAINING_PHASES: tuple[str, ...] = tuple(
    p.strip()
    for p in os.environ.get(
        "AI_ORCHESTRATION_TRAINING_PHASES", "baseline_metrics,recovery_metrics"
    ).split(",")
    if p.strip()
)

# --- Detection strategy ---------------------------------------------------------------------------
#
# What decides that a telemetry record is anomalous and worth diagnosing.
#
#   "signature"        deterministic fault-signature matching (rca/signatures.py) decides.
#   "isolation_forest" the per-service Isolation Forest decides. Reproduces the published run.
#   "either"           anomalous if either fires.
#   "robust"           a learned per-feature median/MAD deviation detector
#                      (anomaly/robust_detector.py).
#   "gmm"              a Gaussian mixture model of normal behaviour, scored by negative
#                      log-likelihood (anomaly/gmm_detector.py).
#
# Measured on the 48-incident Evaluation set, symptom-service records, default threshold:
#
#   mode              detected   cross-service   FP normal   FP recovery
#   isolation_forest    10/48         4/24          2/48        13/48
#   signature           44/48        24/24          0/48        11/48
#   either              45/48        24/24          2/48        18/48
#
# "signature" is the default because it dominates: more than four times the coverage, every
# cross-service incident instead of one in six, and fewer false positives in both phases. "either"
# buys one extra detection for nearly double the false alarms, which no closed loop could use.
#
# Why the Isolation Forest does so badly here is worth recording, because it is not a tuning
# problem. The features that carry the fault signal have *zero variance* in the training data -
# a healthy service always reports 0 active HikariCP connections and 0 HTTP errors - and an
# Isolation Forest splits each feature at a random point between its training min and max. When
# those are equal there is no split to make, so the feature cannot contribute to isolation at all.
# A threshold handles "normally exactly zero, now 16" trivially; an outlier detector cannot.
# Sweeping the score threshold does not help: across the whole sweep cross-service coverage never
# exceeds 5 of 24, and the cross-service ranking is statistically indistinguishable from chance
# (AUC 0.474, 95% CI [0.346, 0.602]).
#
# A sixfold model comparison on identical terms (same training partition, features, records and
# negative control), reported as threshold-independent ROC AUC where 0.5 is chance:
#
#   model                 AUC all   AUC cross-service
#   IsolationForest         0.558         0.474
#   LocalOutlierFactor      0.557         0.430
#   EllipticEnvelope        0.659         0.728
#   OneClassSVM             0.842         0.967
#   Z-score (max)           0.863         1.000
#   Robust MAD (max)        0.935         1.000
#
# Both density- and tree-based detectors fail here for the same structural reason given above;
# every per-feature distance method succeeds. "robust" is therefore the better learned detector,
# and is preferred over "signature" where a detector that generalises beyond the known fault
# catalogue is wanted.
#
# Set AI_ORCHESTRATION_DETECTION_MODE=isolation_forest to reproduce the published evaluation.
# Threshold for the robust detector, in sigmas from the learned per-feature normal. 12 was chosen
# from the sweep in docs/evaluation/: at 12 the detector surfaces 41 of 48 incidents and all 24
# cross-service incidents with zero normal-phase false positives; tightening to 30 gives 36 of 48
# with zero false positives in either phase.
ROBUST_DETECTOR_THRESHOLD = float(os.environ.get("AI_ORCHESTRATION_ROBUST_THRESHOLD", "12.0"))

# Gaussian Mixture detector (anomaly/gmm_detector.py). Diagonal covariance and a covariance floor
# are what make a mixture model estimable from 40 samples per service; see that module for why.
GMM_MAX_COMPONENTS = int(os.environ.get("AI_ORCHESTRATION_GMM_MAX_COMPONENTS", "2"))
GMM_REG_COVAR = float(os.environ.get("AI_ORCHESTRATION_GMM_REG_COVAR", "1e-4"))
GMM_THRESHOLD = float(os.environ.get("AI_ORCHESTRATION_GMM_THRESHOLD", "40.0"))

DETECTION_MODE = os.environ.get("AI_ORCHESTRATION_DETECTION_MODE", "signature").strip().lower()
if DETECTION_MODE not in ("signature", "isolation_forest", "either", "robust", "gmm"):
    raise ValueError(
        f"AI_ORCHESTRATION_DETECTION_MODE must be one of 'signature', 'isolation_forest', "
        f"'either', 'robust' or 'gmm'; got {DETECTION_MODE!r}."
    )

# --- Recovery verification ----------------------------------------------------------------------
#
# Whether recovery is verified by re-reading the service's own /fault-status debug endpoint.
#
# `evaluation/replay.py` deliberately withholds /fault-status from the diagnosis path on the
# grounds that, for a same-service fault, it is effectively the answer key. Verifying recovery
# against that same endpoint reintroduces the dependency the replay protocol removed, and no
# production service exposes it. The metric-based check (see execution/verify.py) instead
# re-observes the metric whose threshold triggered detection and asks whether it has returned
# below that threshold, which is what a real deployment can actually do.
#
# Default True preserves the published behaviour; the metric check runs alongside it either way
# and is always recorded.
VERIFY_USING_FAULT_STATUS = os.environ.get(
    "AI_ORCHESTRATION_VERIFY_USING_FAULT_STATUS", "true"
).lower() == "true"

# --- Ollama ---------------------------------------------------------------------------------

OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
# CLAUDE.md names llama3.1:8b as the project's initial candidate local model; the default here is
# a much smaller model chosen for this development machine's limited free disk space (see
# ai-orchestration/README.md "Ollama setup" for the exact tradeoff and how to switch to 8b).
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.2:1b")
OLLAMA_TIMEOUT_SECONDS = float(os.environ.get("OLLAMA_TIMEOUT_SECONDS", "30"))
