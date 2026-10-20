"""Gaussian Mixture Model detector.

A probabilistic generative model of normal behaviour, fitted by expectation-maximisation on the
Memory partition. An observation is scored by its negative log-likelihood under that model: the
less plausible the reading is as a sample from learned-normal, the more anomalous it is.

**Why this and not the Isolation Forest.** Eleven unsupervised detectors were compared on identical
terms - same training partition, same per-service features, same Evaluation records, same
negative-control protocol - and reported as threshold-independent ROC AUC, where 0.5 is chance:

    model                     AUC all   AUC cross-service
    LocalOutlierFactor          0.557         0.430
    IsolationForest             0.558         0.474
    EllipticEnvelope            0.659         0.728
    OneClassSVM                 0.842         0.967
    Mahalanobis + shrinkage     0.850         0.977
    kNN distance                0.852         0.950
    PCA reconstruction          0.860         0.994
    Autoencoder (MLP)           0.870         0.984
    GMM likelihood              0.907         1.000
    Robust MAD                  0.935         1.000

Tree- and density-based detectors fail here for a structural reason: several features that carry
this estate's fault signals are constant in healthy operation - exactly 0 HTTP errors, exactly 0
active pool connections - and an Isolation Forest splits a feature at a random point between its
training minimum and maximum, so a constant feature can never be split on. Likelihood- and
distance-based models have no such blind spot.

Two practical measures make a GMM workable on 40 training rows per service:

  * **Diagonal covariance.** A full covariance matrix cannot be estimated from 40 samples in up to
    12 dimensions, and would be singular in any case because of the constant features.
  * **Covariance regularisation** (`reg_covar`). A zero-variance feature would otherwise give
    infinite likelihood density; the floor keeps the model finite while still making a departure
    in such a feature extremely improbable, which is the behaviour wanted.

Features are standardised first, with the scale of a constant feature pinned to 1 so that
standardisation cannot divide by zero.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

import numpy as np
from sklearn.mixture import GaussianMixture
from sklearn.preprocessing import StandardScaler

from .. import config
from . import models

log = logging.getLogger("ai_orchestration.anomaly.gmm_detector")

MODEL_VERSION = "gmm-diag-v1"


class GMMDetector:
    """Per-service detector. Mirrors `AnomalyDetector`'s surface so the graph can use either."""

    def __init__(self, service: str) -> None:
        self.service = service
        self.feature_names: list[str] = list(config.SERVICE_FEATURE_SCHEMAS.get(service, []))
        self._model: Optional[GaussianMixture] = None
        self._mean: Optional[np.ndarray] = None
        self._scale: Optional[np.ndarray] = None
        self._training_samples = 0

    # --- training ---------------------------------------------------------------------------

    def train(self, vectors: list[models.FeatureVector],
              training_source_experiment_ids: Optional[list[str]] = None) -> dict:
        if not vectors:
            raise ValueError(f"No training vectors for '{self.service}'")
        X = np.array([v.values for v in vectors], dtype=float)
        scaler = StandardScaler().fit(X)
        scaler.scale_ = np.where(scaler.scale_ == 0, 1.0, scaler.scale_)
        Z = scaler.transform(X)

        components = max(1, min(config.GMM_MAX_COMPONENTS, len(Z) // 15))
        model = GaussianMixture(
            n_components=components,
            covariance_type="diag",
            reg_covar=config.GMM_REG_COVAR,
            random_state=config.ISOLATION_FOREST_RANDOM_STATE,
            max_iter=500,
        ).fit(Z)

        self.feature_names = list(vectors[0].feature_names)
        self._model, self._mean, self._scale = model, scaler.mean_, scaler.scale_
        self._training_samples = len(vectors)
        log.info("%s: GMM fitted, %d component(s) on %d samples, %d features",
                 self.service, components, len(Z), Z.shape[1])
        return self.metadata()

    def metadata(self) -> dict:
        return {
            "service": self.service,
            "model_version": MODEL_VERSION,
            "feature_schema_version": config.FEATURE_SCHEMA_VERSION,
            "feature_names": list(self.feature_names),
            "training_sample_count": self._training_samples,
            "n_components": int(self._model.n_components) if self._model is not None else 0,
            "covariance_type": "diag",
            "reg_covar": config.GMM_REG_COVAR,
        }

    @property
    def is_trained(self) -> bool:
        return self._model is not None

    # --- scoring ----------------------------------------------------------------------------

    def score(self, vector: models.FeatureVector) -> models.AnomalyResult:
        if vector.service != self.service:
            raise ValueError(
                f"Detector for '{self.service}' cannot score a vector for '{vector.service}'")
        if not self.is_trained:
            return models.AnomalyResult(
                service=self.service, timestamp=vector.timestamp,
                correlation_id=vector.correlation_id, status="no_model",
                anomaly_score=None, is_anomaly=None, threshold=config.GMM_THRESHOLD,
                relevant_feature_values=vector.raw_metrics, model_version=MODEL_VERSION,
                feature_schema_version=config.FEATURE_SCHEMA_VERSION,
                reason=f"No trained GMM for '{self.service}'")
        if list(vector.feature_names) != list(self.feature_names):
            raise ValueError(
                f"Vector features {vector.feature_names} do not match this detector's schema "
                f"{self.feature_names} for '{self.service}'")

        z = (np.array([vector.values], dtype=float) - self._mean) / self._scale
        nll = -float(self._model.score_samples(z)[0])   # higher = less plausible as normal
        threshold = config.GMM_THRESHOLD
        return models.AnomalyResult(
            service=self.service, timestamp=vector.timestamp,
            correlation_id=vector.correlation_id, status="scored",
            anomaly_score=round(nll, 6), is_anomaly=bool(nll > threshold),
            threshold=threshold, relevant_feature_values=vector.raw_metrics,
            model_version=MODEL_VERSION,
            feature_schema_version=config.FEATURE_SCHEMA_VERSION,
            reason=(f"Negative log-likelihood {nll:.2f} under the learned normal model "
                    f"(threshold {threshold:g}); higher means less plausible as normal."))

    # --- persistence ------------------------------------------------------------------------

    def save(self) -> Path:
        import joblib
        d = Path(config.MODELS_DIR) / self.service
        d.mkdir(parents=True, exist_ok=True)
        joblib.dump({"model": self._model, "mean": self._mean, "scale": self._scale},
                    d / "gmm.joblib")
        (d / "gmm_metadata.json").write_text(json.dumps(self.metadata(), indent=2),
                                             encoding="utf-8")
        return d / "gmm.joblib"

    @classmethod
    def load(cls, service: str) -> "GMMDetector":
        import joblib
        detector = cls(service)
        base = Path(config.MODELS_DIR) / service
        blob, meta = base / "gmm.joblib", base / "gmm_metadata.json"
        if not blob.exists() or not meta.exists():
            return detector
        payload = joblib.load(blob)
        info = json.loads(meta.read_text(encoding="utf-8"))
        detector._model = payload["model"]
        detector._mean = payload["mean"]
        detector._scale = payload["scale"]
        detector.feature_names = list(info.get("feature_names") or [])
        detector._training_samples = int(info.get("training_sample_count") or 0)
        return detector


def load_all() -> dict[str, "GMMDetector"]:
    out = {}
    for service in config.SERVICE_FEATURE_SCHEMAS:
        d = GMMDetector.load(service)
        if d.is_trained:
            out[service] = d
    return out
