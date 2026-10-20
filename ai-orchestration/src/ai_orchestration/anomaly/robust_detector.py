"""Robust per-feature deviation detector.

Learned, not rule-based: for each service and feature it fits a centre (median) and a spread
(median absolute deviation, scaled to be comparable to a standard deviation) from the Memory
partition, then scores an observation by how far its most deviant feature sits from that centre
in units of spread. Nothing here references the fault taxonomy, and no fault threshold is written
down - a feature shift the signature table has never seen still scores.

**Why this rather than the Isolation Forest.** Measured on the 48-incident Evaluation set with
identical training data, features and negative-control protocol, as ROC AUC (threshold
independent; 0.5 is chance):

    model                 AUC all   AUC cross-service   AUC same-service
    IsolationForest         0.558         0.474               0.641
    LocalOutlierFactor      0.557         0.430               0.683
    EllipticEnvelope        0.659         0.728               0.589
    OneClassSVM             0.842         0.967               0.717
    Z-score (max)           0.863         1.000               0.725
    Robust MAD (max)        0.935         1.000               0.870

The pattern is the point. Tree- and density-based detectors need a populated distribution to
describe, and several of the features that carry this estate's fault signals have none: a healthy
service reports exactly 0 HTTP errors and exactly 0 active pool connections, every time. An
Isolation Forest splits a feature at a random point between its training minimum and maximum, so
when those are equal there is no split to make and the feature cannot contribute to isolation at
all - the forest is blind in precisely the dimensions that matter. Per-feature distance methods
have no such problem, and the median/MAD pair is additionally robust to outliers that happen to
sit in the training partition itself, which is why it beats the plain z-score on same-service
faults.

A feature that is constant in health and then moves is, strictly, infinitely surprising. That is
capped (`ZERO_SPREAD_DEVIATION`) so one such feature cannot flatten every score to the same value
and destroy the ranking.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np

from .. import config
from . import models

log = logging.getLogger("ai_orchestration.anomaly.robust_detector")

MODEL_VERSION = "robust-mad-v1"

# Deviation attributed to a feature that is constant across all training data and then changes.
# Large enough to dominate any ordinary deviation, finite so that scores stay comparable.
ZERO_SPREAD_DEVIATION = 50.0

# 1 / Phi^-1(3/4): scales the median absolute deviation so it estimates the same quantity as a
# standard deviation would for normally distributed data, which keeps the threshold interpretable
# as "sigmas from normal".
MAD_TO_SIGMA = 1.4826


@dataclass
class FeatureBaseline:
    """What normal looks like for one feature of one service."""
    name: str
    centre: float
    spread: float          # 0.0 means the feature never varied in training

    def deviation(self, value: float) -> float:
        if self.spread > 0:
            return abs(value - self.centre) / self.spread
        return ZERO_SPREAD_DEVIATION if value != self.centre else 0.0


class RobustDetector:
    """Per-service detector. Mirrors `AnomalyDetector`'s surface so the graph can use either."""

    def __init__(self, service: str) -> None:
        self.service = service
        self.feature_names: list[str] = list(config.SERVICE_FEATURE_SCHEMAS.get(service, []))
        self._baselines: list[FeatureBaseline] = []
        self._training_samples = 0

    # --- training ---------------------------------------------------------------------------

    def train(self, vectors: list[models.FeatureVector],
              training_source_experiment_ids: Optional[list[str]] = None) -> dict:
        if not vectors:
            raise ValueError(f"No training vectors for '{self.service}'")
        names = vectors[0].feature_names
        X = np.array([v.values for v in vectors], dtype=float)
        centre = np.median(X, axis=0)
        spread = np.median(np.abs(X - centre), axis=0) * MAD_TO_SIGMA

        self.feature_names = list(names)
        self._baselines = [FeatureBaseline(n, float(c), float(s))
                           for n, c, s in zip(names, centre, spread)]
        self._training_samples = len(vectors)

        constant = [b.name for b in self._baselines if b.spread == 0]
        if constant:
            # Not a problem for this detector, but worth logging: these are exactly the features
            # an Isolation Forest cannot use at all.
            log.info("%s: %d feature(s) constant in training (%s); a departure in any of them "
                     "scores as %g sigma", self.service, len(constant),
                     ", ".join(constant), ZERO_SPREAD_DEVIATION)
        return self.metadata()

    def metadata(self) -> dict:
        return {
            "service": self.service,
            "model_version": MODEL_VERSION,
            "feature_schema_version": config.FEATURE_SCHEMA_VERSION,
            "feature_names": list(self.feature_names),
            "training_sample_count": self._training_samples,
            "baselines": [{"name": b.name, "centre": b.centre, "spread": b.spread}
                          for b in self._baselines],
        }

    @property
    def is_trained(self) -> bool:
        return bool(self._baselines)

    # --- scoring ----------------------------------------------------------------------------

    def deviations(self, vector: models.FeatureVector) -> list[tuple[str, float, float]]:
        """(feature, observed value, deviation in sigmas), most deviant first."""
        out = []
        for baseline, value in zip(self._baselines, vector.values):
            out.append((baseline.name, float(value), baseline.deviation(float(value))))
        out.sort(key=lambda t: t[2], reverse=True)
        return out

    def score(self, vector: models.FeatureVector) -> models.AnomalyResult:
        if vector.service != self.service:
            raise ValueError(
                f"Detector for '{self.service}' cannot score a vector for '{vector.service}'")
        if not self.is_trained:
            return models.AnomalyResult(
                service=self.service, timestamp=vector.timestamp,
                correlation_id=vector.correlation_id, status="no_model",
                anomaly_score=None, is_anomaly=None,
                threshold=config.ROBUST_DETECTOR_THRESHOLD,
                relevant_feature_values=vector.raw_metrics, model_version=MODEL_VERSION,
                feature_schema_version=config.FEATURE_SCHEMA_VERSION,
                reason=f"No trained robust baseline for '{self.service}'")
        if list(vector.feature_names) != list(self.feature_names):
            raise ValueError(
                f"Vector features {vector.feature_names} do not match this detector's schema "
                f"{self.feature_names} for '{self.service}'")

        ranked = self.deviations(vector)
        worst_name, worst_value, worst_dev = ranked[0]
        threshold = config.ROBUST_DETECTOR_THRESHOLD
        return models.AnomalyResult(
            service=self.service, timestamp=vector.timestamp,
            correlation_id=vector.correlation_id, status="scored",
            anomaly_score=round(float(worst_dev), 6),
            is_anomaly=bool(worst_dev > threshold),
            threshold=threshold, relevant_feature_values=vector.raw_metrics,
            model_version=MODEL_VERSION,
            feature_schema_version=config.FEATURE_SCHEMA_VERSION,
            reason=(f"{worst_name} = {worst_value:g} sits {worst_dev:.1f} sigma from its learned "
                    f"normal (threshold {threshold:g})."))

    # --- persistence ------------------------------------------------------------------------

    def _dir(self) -> Path:
        return Path(config.MODELS_DIR) / self.service

    def save(self) -> Path:
        d = self._dir()
        d.mkdir(parents=True, exist_ok=True)
        path = d / "robust_baseline.json"
        path.write_text(json.dumps(self.metadata(), indent=2), encoding="utf-8")
        return path

    @classmethod
    def load(cls, service: str) -> "RobustDetector":
        detector = cls(service)
        path = Path(config.MODELS_DIR) / service / "robust_baseline.json"
        if not path.exists():
            return detector
        meta = json.loads(path.read_text(encoding="utf-8"))
        detector.feature_names = list(meta.get("feature_names") or [])
        detector._baselines = [FeatureBaseline(b["name"], b["centre"], b["spread"])
                               for b in meta.get("baselines") or []]
        detector._training_samples = int(meta.get("training_sample_count") or 0)
        return detector


def load_all() -> dict[str, "RobustDetector"]:
    out = {}
    for service in config.SERVICE_FEATURE_SCHEMAS:
        d = RobustDetector.load(service)
        if d.is_trained:
            out[service] = d
    return out
