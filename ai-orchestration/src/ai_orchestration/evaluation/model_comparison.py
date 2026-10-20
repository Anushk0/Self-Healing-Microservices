"""Compare unsupervised anomaly detectors on identical terms.

Every candidate sees the same training partition, the same per-service feature schema, the same
evaluation records and the same negative-control protocol. The only thing that varies is the
estimator, so differences are attributable to the model rather than to the setup.

**ROC AUC is the headline** because it is threshold-independent. Any detector can be made to flag
more by lowering its threshold; AUC asks whether its *ranking* carries information at all - the
probability that a randomly chosen fault window scores above a randomly chosen healthy record.
0.5 is a coin flip. This also makes detectors with incomparable score scales (an isolation depth
near zero, a deviation of 50 sigma, a negative log-likelihood in the hundreds of thousands)
directly comparable.

Two data sources are supported:

  ``--source corpus``  the synthetic Evaluation corpus: one phase-averaged record per incident
                       per phase. This is what the published evaluation used.
  ``--source capture`` raw per-cycle telemetry captured from the running estate by
                       ``observability/capture_real_telemetry.py``. Records inside an injected
                       fault window are the positives; records in the baseline and recovery
                       windows are the negatives.

The second source exists because the first is not representative: measured against the corpus,
half of all healthy evaluation records contain at least one feature outside the range the
detector saw in training. That penalises rank-based detectors (ECOD) hardest, since "beyond
anything I have seen" stops discriminating once half the healthy data is also beyond it, while
magnitude-aware detectors (MAD, Gaussian mixtures) still grade the size of the departure. A
comparison run only on the corpus therefore measures the corpus as much as the models.
"""
from __future__ import annotations

import argparse
import glob
import json
import warnings
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from .. import config as ai_config

ai_config.ensure_sibling_packages_importable()

from dataset_tools import config as dt_config  # noqa: E402

warnings.filterwarnings("ignore")

SERVICES = list(ai_config.SERVICE_FEATURE_SCHEMAS)
TRAINING_PHASES = ("baseline_metrics", "recovery_metrics")
ZERO_SPREAD_DEVIATION = 50.0

# Cumulative Micrometer counters, and the windowed quantity to use in their place when scoring
# raw per-cycle telemetry. The counters climb monotonically for the life of the JVM - over a
# 31-minute capture `http_server_requests_count` rose from 13,535 to 17,236 - so a detector
# trained on early samples reads every later sample as anomalous regardless of any fault. The
# lifetime averages have the same defect: they are dominated by history rather than by the
# current window. `telemetry/rates.py` derives the windowed equivalents; these are what a live
# detector must actually consume.
COUNTER_SUBSTITUTIONS = {
    "http_server_requests_count": "http_server_requests_rate_per_s",
    "http_server_requests_error_count": "http_server_requests_error_rate_per_s",
    "http_server_requests_avg_duration_ms": "http_server_requests_window_avg_ms",
    "http_client_requests_count": "http_client_requests_rate_per_s",
    "http_client_requests_avg_duration_ms": "http_client_requests_window_avg_ms",
}


# --- estimators ---------------------------------------------------------------------------------
# Each builder takes a training matrix and returns scorer(x) -> higher means more anomalous.

def _scaler(X):
    from sklearn.preprocessing import StandardScaler
    sc = StandardScaler().fit(X)
    sc.scale_ = np.where(sc.scale_ == 0, 1.0, sc.scale_)   # a constant feature must not divide by 0
    return sc


def build_iforest(X):
    from sklearn.ensemble import IsolationForest
    m = IsolationForest(n_estimators=ai_config.ISOLATION_FOREST_N_ESTIMATORS,
                        contamination=float(ai_config.ISOLATION_FOREST_CONTAMINATION),
                        random_state=ai_config.ISOLATION_FOREST_RANDOM_STATE).fit(X)
    return lambda x: -float(m.decision_function([x])[0])


def build_lof(X):
    from sklearn.neighbors import LocalOutlierFactor
    m = LocalOutlierFactor(n_neighbors=min(20, max(2, len(X) - 1)), novelty=True).fit(X)
    return lambda x: -float(m.decision_function([x])[0])


def build_ocsvm(X):
    from sklearn.svm import OneClassSVM
    sc = _scaler(X)
    m = OneClassSVM(kernel="rbf", nu=0.1, gamma="scale").fit(sc.transform(X))
    return lambda x: -float(m.decision_function(sc.transform([x]))[0])


def build_pca(X):
    from sklearn.decomposition import PCA
    sc = _scaler(X); Z = sc.transform(X)
    k = max(1, min(Z.shape[1] - 1, max(1, int(np.linalg.matrix_rank(Z)) - 1)))
    p = PCA(n_components=k).fit(Z)
    def s(x):
        z = sc.transform([x])
        return float(np.sum((z - p.inverse_transform(p.transform(z))) ** 2))
    return s


def build_knn(X):
    from sklearn.neighbors import NearestNeighbors
    sc = _scaler(X); Z = sc.transform(X)
    nn = NearestNeighbors(n_neighbors=min(5, max(1, len(Z) - 1))).fit(Z)
    return lambda x: float(nn.kneighbors(sc.transform([x]))[0][0][-1])


def build_mahalanobis(X):
    from sklearn.covariance import LedoitWolf
    sc = _scaler(X)
    lw = LedoitWolf().fit(sc.transform(X))      # shrinkage keeps the covariance invertible
    return lambda x: float(lw.mahalanobis(sc.transform([x]))[0])


def build_gmm(X):
    from sklearn.mixture import GaussianMixture
    sc = _scaler(X); Z = sc.transform(X)
    n = max(1, min(ai_config.GMM_MAX_COMPONENTS, len(Z) // 15))
    g = GaussianMixture(n_components=n, covariance_type="diag",
                        reg_covar=ai_config.GMM_REG_COVAR,
                        random_state=ai_config.ISOLATION_FOREST_RANDOM_STATE,
                        max_iter=500).fit(Z)
    return lambda x: -float(g.score_samples(sc.transform([x]))[0])


def build_autoencoder(X):
    from sklearn.neural_network import MLPRegressor
    sc = _scaler(X); Z = sc.transform(X)
    ae = MLPRegressor(hidden_layer_sizes=(max(2, Z.shape[1] // 2),), activation="relu",
                      solver="lbfgs", max_iter=2000,
                      random_state=ai_config.ISOLATION_FOREST_RANDOM_STATE).fit(Z, Z)
    def s(x):
        z = sc.transform([x])
        return float(np.sum((z - ae.predict(z)) ** 2))
    return s


def build_zscore(X):
    mu, sd = X.mean(axis=0), X.std(axis=0)
    def s(x):
        dev = [abs(v - m) / d if d > 0 else (ZERO_SPREAD_DEVIATION if v != m else 0.0)
               for m, d, v in zip(mu, sd, x)]
        return float(max(dev))
    return s


def build_mad(X):
    centre = np.median(X, axis=0)
    spread = np.median(np.abs(X - centre), axis=0) * 1.4826
    def s(x):
        dev = [abs(v - c) / sp if sp > 0 else (ZERO_SPREAD_DEVIATION if v != c else 0.0)
               for c, sp, v in zip(centre, spread, x)]
        return float(max(dev))
    return s


def build_ecod(X):
    """ECOD (Li et al., IEEE TKDE 2022), implemented from the paper rather than via a dependency.

    Per dimension, score how far into a tail an observation sits as a log empirical tail
    probability; aggregate by summing across dimensions. Three aggregations - left tail, right
    tail, and one directed by the training skew - and the score is their maximum. Parameter-free.
    """
    from scipy.stats import skew
    Xa = np.asarray(X, dtype=float)
    n = len(Xa)
    sk = np.nan_to_num(skew(Xa, axis=0, bias=False), nan=0.0)
    floor = 1.0 / (n + 1)       # an empty tail must not give -log(0)
    def s(x):
        left = np.clip((Xa <= x).sum(axis=0) / n, floor, 1.0)
        right = np.clip((Xa >= x).sum(axis=0) / n, floor, 1.0)
        directed = np.where(sk < 0, np.log(left), np.log(right))
        return float(max(-np.log(left).sum(), -np.log(right).sum(), -directed.sum()))
    return s


MODELS: list[tuple[str, Callable]] = [
    ("IsolationForest", build_iforest),
    ("LocalOutlierFactor", build_lof),
    ("OneClassSVM", build_ocsvm),
    ("Mahalanobis+shrink", build_mahalanobis),
    ("kNN distance", build_knn),
    ("PCA reconstruction", build_pca),
    ("Autoencoder (MLP)", build_autoencoder),
    ("GMM likelihood", build_gmm),
    ("ECOD", build_ecod),
    ("Z-score (max)", build_zscore),
    ("Robust MAD (max)", build_mad),
]


# --- data sources --------------------------------------------------------------------------

def _read_jsonl(path) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def corpus_data() -> tuple[dict, dict]:
    """Phase-averaged records from the synthetic corpus, as the published evaluation used."""
    from . import replay
    memory = _read_jsonl(dt_config.MEMORY_INCIDENTS_FILE)
    evaluation = replay.load_evaluation_incidents()

    train: dict[str, list[list[float]]] = {s: [] for s in SERVICES}
    for inc in memory:
        for phase in TRAINING_PHASES:
            for svc in SERVICES:
                feats = ai_config.SERVICE_FEATURE_SCHEMAS[svc]
                m = (inc.get(phase) or {}).get(svc)
                if m and all(m.get(f) is not None for f in feats):
                    train[svc].append([float(m[f]) for f in feats])

    def rows(key):
        out = []
        for inc in evaluation:
            svc = inc["symptom_service"]
            feats = ai_config.SERVICE_FEATURE_SCHEMAS[svc]
            m = (inc.get(key) or {}).get(svc)
            if m and all(m.get(f) is not None for f in feats):
                out.append((svc, [float(m[f]) for f in feats],
                            inc["root_cause_service"] != inc["symptom_service"]))
        return out

    test = {"fault": rows("fault_metrics"),
            "healthy": rows("baseline_metrics") + rows("recovery_metrics")}
    return train, test


def capture_data(directory: Path) -> tuple[dict, dict]:
    """Raw per-cycle records captured from the live estate.

    Positives are samples taken while a fault was injected, negatives are samples from the
    baseline and recovery windows. Fault windows are read from the experiment's own event log,
    so nothing is inferred from the service's self-reported fault flag - which the replay
    protocol deliberately withholds elsewhere for the same reason.
    """
    events, telemetry = [], []
    for f in glob.glob(str(directory / "events_*.jsonl")):
        events += _read_jsonl(f)
    for f in glob.glob(str(directory / "telemetry_*.jsonl")):
        telemetry += _read_jsonl(f)
    if not telemetry:
        raise SystemExit(f"No telemetry records under {directory}. Run "
                         f"observability/capture_real_telemetry.py first.")

    # Each window carries the fault that was injected, because a fault is only observable on its
    # *symptom* service. During a db-lock injection, ledger-service degrades while auth-service
    # and notification-service remain genuinely healthy; labelling all four as faulty would make
    # three quarters of the positives mislabelled healthy records and cap any detector's
    # achievable AUC regardless of its quality.
    windows: list[tuple[str, str, str]] = []          # (start, end, symptom_service)
    open_at = open_fault = None
    for ev in sorted(events, key=lambda e: e.get("timestamp") or ""):
        kind = (ev.get("event") or ev.get("type") or "").lower()
        if "inject" in kind:
            open_at, open_fault = ev.get("timestamp"), ev.get("fault")
        elif "reset" in kind and open_at:
            mapping = dt_config.FAULT_SERVICE_MAP.get(open_fault or "", {})
            symptom = mapping.get("symptom_service")
            if symptom:
                windows.append((open_at, ev.get("timestamp"), symptom))
            open_at = open_fault = None

    def is_faulty(ts: Optional[str], service: str) -> bool:
        """A sample is a positive only inside a fault window *and* on that fault's symptom
        service - the same service the replay protocol feeds the pipeline."""
        if not ts:
            return False
        return any(a <= ts <= b and service == symptom for a, b, symptom in windows)

    def vector_for(rec: dict, svc: str) -> Optional[list[float]]:
        """Build a feature vector, substituting windowed rates for cumulative counters."""
        metrics = rec.get("metrics") or {}
        derived = rec.get("derived") or {}
        values = []
        for feat in ai_config.SERVICE_FEATURE_SCHEMAS[svc]:
            replacement = COUNTER_SUBSTITUTIONS.get(feat)
            value = derived.get(replacement) if replacement else metrics.get(feat)
            if value is None:
                return None          # first cycle has no window yet; skip rather than impute
            values.append(float(value))
        return values

    train: dict[str, list[list[float]]] = {s: [] for s in SERVICES}
    fault_rows, healthy_by_service = [], {s: [] for s in SERVICES}
    for rec in telemetry:
        svc = rec.get("service")
        if svc not in ai_config.SERVICE_FEATURE_SCHEMAS:
            continue
        vector = vector_for(rec, svc)
        if vector is None:
            continue
        if is_faulty(rec.get("timestamp"), svc):
            fault_rows.append((svc, vector, False))
        else:
            healthy_by_service[svc].append(vector)

    # Split healthy samples at random rather than by position. A positional split would put all
    # the early samples in training and all the late ones in evaluation, which confounds "is this
    # a fault" with "is this later in the capture" - exactly the artefact the counter
    # substitution above removes at the feature level.
    rng = np.random.default_rng(ai_config.ISOLATION_FOREST_RANDOM_STATE)
    held_out = []
    for svc, vecs in healthy_by_service.items():
        if not vecs:
            continue
        idx = rng.permutation(len(vecs))
        cut = len(vecs) // 2
        train[svc] = [vecs[i] for i in idx[:cut]]
        held_out += [(svc, vecs[i], False) for i in idx[cut:]]

    return train, {"fault": fault_rows, "healthy": held_out}


# --- scoring -----------------------------------------------------------------------------------

def auc(pos: list[float], neg: list[float]) -> float:
    if not pos or not neg:
        return float("nan")
    wins = ties = 0
    for p in pos:
        for n in neg:
            if p > n:
                wins += 1
            elif p == n:
                ties += 1
    return (wins + 0.5 * ties) / (len(pos) * len(neg))


def run(train: dict, test: dict) -> list[tuple[str, float, float, float]]:
    results = []
    for name, builder in MODELS:
        try:
            scorers = {svc: builder(np.array(rows, dtype=float))
                       for svc, rows in train.items() if rows}
        except Exception as exc:  # noqa: BLE001 - a model that cannot fit is a reportable outcome
            results.append((name, float("nan"), float("nan"), float("nan"),
                            f"{type(exc).__name__}"))
            continue

        def score_all(rows):
            out = []
            for svc, vec, cross in rows:
                fn = scorers.get(svc)
                if fn is None:
                    continue
                out.append((fn(np.array(vec, dtype=float)), cross))
            return out

        faults = score_all(test["fault"])
        healthy = [s for s, _ in score_all(test["healthy"])]
        cross = [s for s, c in faults if c]
        same = [s for s, c in faults if not c]
        results.append((name,
                        auc([s for s, _ in faults], healthy),
                        auc(cross, healthy) if cross else float("nan"),
                        auc(same, healthy) if same else float("nan"),
                        ""))
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=["corpus", "capture"], default="corpus")
    parser.add_argument("--capture-dir",
                        default=str(ai_config.REPO_ROOT / "telemetry" / "data" / "real_capture"))
    parser.add_argument("--out", default=None, help="write results as JSON")
    args = parser.parse_args()

    if args.source == "corpus":
        train, test = corpus_data()
        label = "synthetic corpus (phase averages)"
    else:
        train, test = capture_data(Path(args.capture_dir))
        label = f"live capture (per-cycle) from {args.capture_dir}"

    n_train = sum(len(v) for v in train.values())
    print(f"source     : {label}")
    print(f"training   : {n_train} records across {sum(1 for v in train.values() if v)} services")
    print(f"evaluation : {len(test['fault'])} fault, {len(test['healthy'])} healthy")
    print()
    hdr = f"{'model':24} {'AUC all':>9} {'AUC cross':>11} {'AUC same':>10}"
    print(hdr); print("-" * len(hdr))
    results = run(train, test)
    for name, a, x, s, note in sorted(results, key=lambda r: (np.isnan(r[1]), -(r[1] if not np.isnan(r[1]) else 0))):
        if note:
            print(f"{name:24} {'--':>9} {'--':>11} {'--':>10}  could not fit: {note}")
        else:
            xs = f"{x:>11.3f}" if not np.isnan(x) else f"{'n/a':>11}"
            ss = f"{s:>10.3f}" if not np.isnan(s) else f"{'n/a':>10}"
            print(f"{name:24} {a:>9.3f} {xs} {ss}")
    print()
    print("AUC 0.5 = no better than chance at ranking a fault above a healthy record.")

    if args.out:
        payload = {"source": label, "training_records": n_train,
                   "fault_records": len(test["fault"]), "healthy_records": len(test["healthy"]),
                   "results": [{"model": n, "auc_all": a, "auc_cross": x, "auc_same": s,
                                "error": note} for n, a, x, s, note in results]}
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"written: {args.out}")


if __name__ == "__main__":
    main()
