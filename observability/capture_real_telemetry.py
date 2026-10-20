"""Capture representative per-cycle telemetry from the running estate.

Why this exists. The detector comparison in docs/evaluation/ was run against the synthetic
corpus, where each incident contributes a single *phase average* per metric - 40 training rows
per service in total. Measured against that corpus, half of all healthy evaluation records
contain at least one feature outside the range the detector saw in training (24 of 48 in both
the normal and recovery phases). That is not a small-sample problem so much as a representativeness
one: 40 averaged points do not cover the natural variability of normal operation.

It penalises rank-based detectors hardest. A method that scores by empirical tail probability
(ECOD) can only report "beyond anything I have seen", which is indistinguishable between a fault
and an ordinary healthy fluctuation once half the healthy data is also beyond that range.
Magnitude-aware methods (median/MAD, Gaussian mixtures) survive it because they grade the size of
the departure rather than only its rank.

This script runs a real NORMAL -> FAULT -> RECOVERY experiment for each of the four controlled
faults against the live Docker estate, at a short sampling interval, writing raw per-cycle
records rather than phase averages. The result is telemetry with genuine variation, which is what
a fair model comparison needs.

It changes nothing in the pipeline and writes nothing into the evaluation corpus: output lands in
its own directory, and the Memory/Evaluation partitions are untouched.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from telemetry.experiment import FAULTS, run_experiment  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("capture")

DEFAULT_OUT = REPO_ROOT / "telemetry" / "data" / "real_capture"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--faults", default=",".join(sorted(FAULTS)),
                        help="comma-separated fault names to capture")
    parser.add_argument("--baseline-seconds", type=float, default=150)
    parser.add_argument("--fault-seconds", type=float, default=150)
    parser.add_argument("--recovery-seconds", type=float, default=150)
    parser.add_argument("--interval", type=float, default=3.0,
                        help="seconds between samples; 3s over 150s gives ~50 points per phase")
    parser.add_argument("--settle-seconds", type=float, default=20,
                        help="pause between experiments so one fault's recovery does not bleed "
                             "into the next one's baseline")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    faults = [f.strip() for f in args.faults.split(",") if f.strip()]
    unknown = [f for f in faults if f not in FAULTS]
    if unknown:
        raise SystemExit(f"Unknown fault(s): {unknown}. Known: {sorted(FAULTS)}")

    per_experiment = args.baseline_seconds + args.fault_seconds + args.recovery_seconds
    total = len(faults) * (per_experiment + args.settle_seconds)
    log.info("Capturing %d experiment(s) into %s", len(faults), out_dir)
    log.info("Each: %.0fs baseline + %.0fs fault + %.0fs recovery at %.0fs intervals "
             "(~%d samples per phase per service)",
             args.baseline_seconds, args.fault_seconds, args.recovery_seconds, args.interval,
             int(args.baseline_seconds / args.interval))
    log.info("Estimated total: %.1f minutes", total / 60)

    results = []
    for i, fault in enumerate(faults, 1):
        log.info("[%d/%d] %s", i, len(faults), fault)
        summary = run_experiment(
            fault,
            baseline_seconds=args.baseline_seconds,
            fault_seconds=args.fault_seconds,
            recovery_seconds=args.recovery_seconds,
            interval=args.interval,
            data_dir=out_dir,
        )
        results.append((fault, summary))
        if i < len(faults):
            # The fault reset is synchronous, but a JVM takes a moment to settle; starting the
            # next baseline immediately would record the tail of this recovery as "normal".
            log.info("settling for %.0fs", args.settle_seconds)
            time.sleep(args.settle_seconds)

    log.info("Done. %d experiment(s) captured.", len(results))
    for fault, summary in results:
        eid = (summary or {}).get("experiment_id", "?") if isinstance(summary, dict) else "?"
        log.info("  %-22s experiment_id=%s", fault, eid)
    log.info("Raw per-cycle records are in %s", out_dir)


if __name__ == "__main__":
    main()
