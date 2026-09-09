"""Negative control: how often does the detector fire on telemetry from a *healthy* service?

    python -m ai_orchestration.evaluation.negative_control --out ../docs/evaluation/negative_control

The main evaluation replays only fault-window telemetry, so it can measure missed detections but is
structurally incapable of measuring false alarms. That is a real blind spot: a detector that flags
everything would score identically on the main experiment. This module closes it, following the
negative-control design used by Souza and Colaco Junior (CSBC 2026), who evaluate a healthy-system
scenario purely to obtain a false-positive rate.

Each Evaluation incident contributes two healthy records for its symptom service - the NORMAL-phase
averages and the RECOVERY-phase averages. Both describe a service that is not faulty at that moment,
so *any* anomaly flag is a false positive.

The two phases are reported separately and deliberately. They measure different things: NORMAL is a
service that has not yet been disturbed, RECOVERY is one that has just been repaired. Reporting only
the pooled figure would hide that the second is far worse than the first, which is the operationally
important half of the result.

Note on interpretation, stated here so it cannot be lost: the detectors are trained on NORMAL and
RECOVERY phases of *Memory*-set incidents drawn from the same generator, so a low NORMAL-phase rate
is partly expected by construction. The RECOVERY-phase rate is the informative number, because it is
high despite that advantage.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Optional

from .. import config as ai_config
from ..anomaly import feature_extractor
from ..graph.workflow import load_all_detectors

ai_config.ensure_sibling_packages_importable()

from dataset_tools import config as dt_config  # noqa: E402
from dataset_tools.ingest import read_jsonl  # noqa: E402

# Phases that describe a service which is *not* faulty at that moment.
HEALTHY_PHASES: dict[str, str] = {
    "normal": "baseline_metrics",
    "recovery": "recovery_metrics",
}


def wilson_interval(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval. Used rather than the normal approximation because the counts here are
    small and the proportions near zero, where the normal approximation misbehaves badly."""
    if total == 0:
        return (0.0, 0.0)
    p = successes / total
    denom = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denom
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def _healthy_record(incident: dict, service: str, metrics: dict, phase: str) -> dict:
    return {
        "timestamp": (incident.get("phases") or {}).get(phase.upper(), {}).get("start")
        or incident.get("start_time"),
        "service": service,
        "correlation_id": f"negctl-{phase}-{incident.get('incident_id')}",
        "health": {"status": "UP", "components": {}},
        "metrics": dict(metrics),
        "fault": None,
        "collection_error": None,
    }


def run(out_dir: Optional[Path] = None) -> dict:
    detectors = load_all_detectors()
    if not detectors:
        raise RuntimeError(
            "No trained detectors found; a negative control over an untrained detector is "
            "meaningless. Train them first (see evaluation/prepare.py)."
        )

    incidents = read_jsonl(dt_config.EVALUATION_INCIDENTS_FILE)
    if not incidents:
        raise RuntimeError("No Evaluation incidents found.")

    per_phase: dict[str, dict] = {}
    for phase, key in HEALTHY_PHASES.items():
        scored = 0
        skipped = 0
        false_positives = []
        for incident in incidents:
            service = incident.get("symptom_service")
            metrics = (incident.get(key) or {}).get(service)
            if not service or not metrics:
                skipped += 1
                continue
            record = _healthy_record(incident, service, metrics, phase)
            features = feature_extractor.extract_features(record)
            detector = detectors.get(features.service)
            if detector is None or not detector.is_trained:
                skipped += 1
                continue
            result = detector.score(features)
            scored += 1
            if getattr(result, "is_anomaly", False):
                false_positives.append(
                    {
                        "incident_id": incident.get("incident_id"),
                        "service": features.service,
                        "anomaly_score": round(float(getattr(result, "anomaly_score", 0.0)), 6),
                        "true_fault_type": incident.get("fault_type"),
                    }
                )
        lo, hi = wilson_interval(len(false_positives), scored)
        per_phase[phase] = {
            "records_scored": scored,
            "records_skipped": skipped,
            "false_positives": len(false_positives),
            "false_positive_rate": round(len(false_positives) / scored, 4) if scored else None,
            "ci95_lower": round(lo, 4),
            "ci95_upper": round(hi, 4),
            "flagged": false_positives,
        }

    total_scored = sum(p["records_scored"] for p in per_phase.values())
    total_fp = sum(p["false_positives"] for p in per_phase.values())
    lo, hi = wilson_interval(total_fp, total_scored)

    report = {
        "description": (
            "False-positive rate of the shared anomaly detector on telemetry from services that "
            "are not faulty. Any anomaly flag on these records is a false positive."
        ),
        "detectors": sorted(detectors),
        "evaluation_incident_count": len(incidents),
        "per_phase": per_phase,
        "combined": {
            "records_scored": total_scored,
            "false_positives": total_fp,
            "false_positive_rate": round(total_fp / total_scored, 4) if total_scored else None,
            "ci95_lower": round(lo, 4),
            "ci95_upper": round(hi, 4),
        },
        "interpretation_note": (
            "Detectors are trained on NORMAL/RECOVERY phases of Memory-set incidents from the same "
            "generator, so a low NORMAL-phase rate is partly expected by construction. The "
            "RECOVERY-phase rate is the informative figure: it is high despite that advantage, "
            "meaning the system keeps alarming on a service that has just been repaired."
        ),
    }

    if out_dir is not None:
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "negative_control.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8"
        )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Negative control over healthy telemetry.")
    parser.add_argument("--out", default=None, help="directory to write negative_control.json into")
    args = parser.parse_args()

    report = run(Path(args.out) if args.out else None)
    for phase, block in report["per_phase"].items():
        rate = block["false_positive_rate"]
        print(
            f"{phase:9s} {block['false_positives']:3d}/{block['records_scored']:<3d} "
            f"= {rate * 100:6.2f}%  95% CI "
            f"[{block['ci95_lower'] * 100:.2f}%, {block['ci95_upper'] * 100:.2f}%]"
        )
    c = report["combined"]
    print(
        f"combined  {c['false_positives']:3d}/{c['records_scored']:<3d} "
        f"= {c['false_positive_rate'] * 100:6.2f}%  95% CI "
        f"[{c['ci95_lower'] * 100:.2f}%, {c['ci95_upper'] * 100:.2f}%]"
    )


if __name__ == "__main__":
    main()
