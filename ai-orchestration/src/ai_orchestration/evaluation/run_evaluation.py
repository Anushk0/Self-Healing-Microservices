"""CLI entry point for the Week 12 evaluation.

    python -m ai_orchestration.evaluation.run_evaluation --out results/

Forces the two settings a valid replay run requires (dry-run execution, feedback disabled) unless
explicitly overridden, so a run cannot silently be invalidated by an environment variable someone
forgot to set. The harness re-asserts both in `preflight()` regardless.
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from .. import config as ai_config
from . import harness, metrics as metrics_mod, report


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Week 12 evaluation over the Evaluation set.")
    parser.add_argument("--out", default="evaluation-results", help="output directory")
    parser.add_argument("--arms", default="multi_agent,single_agent")
    parser.add_argument("--operator-policy", default="permissive", choices=["permissive", "reject_all"])
    parser.add_argument("--limit", type=int, default=None, help="evaluate only the first N incidents")
    parser.add_argument("--mode", default="replay", choices=["replay", "live"])
    parser.add_argument("--prometheus-url", default=None,
                        help="e.g. http://localhost:9090 - omit to record infrastructure metrics as unavailable")
    parser.add_argument("--allow-live-execution", action="store_true",
                        help="do NOT force dry-run (only meaningful with --mode live)")
    parser.add_argument("--from-run", default=None, metavar="RAW_RUN_JSON",
                        help="recompute metrics and regenerate all artefacts from a previously "
                             "saved raw_run.json instead of running the pipeline again. Every "
                             "measurement is stored in that file, so a metric definition can be "
                             "improved and re-applied to an existing run without re-executing 96 "
                             "pipeline invocations.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")

    if args.from_run:
        from .models import EvaluationRun

        with open(args.from_run, encoding="utf-8") as f:
            run = EvaluationRun(**json.load(f))
        computed = metrics_mod.compute_all(run, prometheus_base_url=args.prometheus_url)
        paths = report.write_all(run, computed, Path(args.out))
        print(json.dumps({k: str(v) for k, v in paths.items()}, indent=2))
        for arm in run.arms:
            perf = computed["per_arm"][arm]["performance"]
            print(f"{arm}: accuracy(all)={perf['accuracy_over_all_incidents']} "
                  f"cross_service={perf['cross_service_accuracy']} abstention={perf['abstention_rate']}")
        return

    if not args.allow_live_execution:
        ai_config.EXECUTION_DRY_RUN = True
    ai_config.FEEDBACK_ENABLED = False  # never write evaluation runs into retrieval memory

    run = harness.run_evaluation(
        arms=[a.strip() for a in args.arms.split(",") if a.strip()],  # type: ignore[arg-type]
        operator_policy=args.operator_policy,
        limit=args.limit,
        mode=args.mode,
    )
    computed = metrics_mod.compute_all(run, prometheus_base_url=args.prometheus_url)
    paths = report.write_all(run, computed, Path(args.out))

    print(json.dumps({k: str(v) for k, v in paths.items()}, indent=2))
    for arm in run.arms:
        perf = computed["per_arm"][arm]["performance"]
        print(f"{arm}: accuracy(all)={perf['accuracy_over_all_incidents']} "
              f"cross_service={perf['cross_service_accuracy']} abstention={perf['abstention_rate']}")


if __name__ == "__main__":
    main()
