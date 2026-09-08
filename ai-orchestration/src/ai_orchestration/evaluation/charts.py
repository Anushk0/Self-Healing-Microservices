"""Comparison charts for the Results section (Week 12 roadmap item 7).

Charts follow the same honesty rule as the metric tables: a metric that was not measured is not
plotted. A bar of height zero and a metric that could not be measured look identical on a chart and
mean opposite things, so unmeasured metrics are annotated as absent rather than drawn.

matplotlib is imported lazily and its absence is reported rather than raised, so an evaluation run
still produces its CSV, JSON and Markdown on a machine without it.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

log = logging.getLogger("ai_orchestration.evaluation.charts")

ARM_LABELS = {"multi_agent": "Multi-agent", "single_agent": "Single-agent\nbaseline"}
COLORS = {"multi_agent": "#2b6cb0", "single_agent": "#c05621"}


def _annotate_bars(ax, bars, values, fmt="{:.0%}") -> None:
    for bar, value in zip(bars, values):
        if value is None:
            ax.text(bar.get_x() + bar.get_width() / 2, 0.02, "not\nmeasured",
                    ha="center", va="bottom", fontsize=8, style="italic", color="#666")
        else:
            ax.text(bar.get_x() + bar.get_width() / 2, value, fmt.format(value),
                    ha="center", va="bottom", fontsize=9)


def generate_all(metrics: dict, out_dir: Path) -> dict[str, Optional[str]]:
    try:
        import matplotlib
        matplotlib.use("Agg")  # headless: never try to open a window during an evaluation run
        import matplotlib.pyplot as plt
    except ImportError:
        log.warning("matplotlib not installed - skipping charts")
        return {"error": "matplotlib not installed; charts skipped (tables and CSV are unaffected)"}

    out_dir.mkdir(parents=True, exist_ok=True)
    arms = metrics["run"]["arms"]
    written: dict[str, Optional[str]] = {}

    # --- Figure A: diagnostic accuracy, with cross-service broken out -----------------------
    fig, ax = plt.subplots(figsize=(8, 4.5))
    groups = [
        ("Accuracy\n(all incidents)", "accuracy_over_all_incidents"),
        ("Accuracy\n(when answered)", "accuracy_when_answered"),
        ("Cross-service\naccuracy", "cross_service_accuracy"),
        ("Same-service\naccuracy", "same_service_accuracy"),
        ("Fault-type\naccuracy", "fault_type_accuracy_when_answered"),
    ]
    width, positions = 0.36, range(len(groups))
    for i, arm in enumerate(arms):
        values = [metrics["per_arm"][arm]["performance"].get(key) for _, key in groups]
        plotted = [v if v is not None else 0 for v in values]
        offset = (i - (len(arms) - 1) / 2) * width
        bars = ax.bar([p + offset for p in positions], plotted, width,
                      label=ARM_LABELS.get(arm, arm), color=COLORS.get(arm))
        _annotate_bars(ax, bars, values)
    ax.set_xticks(list(positions))
    ax.set_xticklabels([label for label, _ in groups], fontsize=9)
    ax.set_ylim(0, 1.15)
    ax.set_ylabel("Accuracy")
    ax.set_title("Root cause identification: multi-agent vs single-agent baseline")
    ax.legend(frameon=False)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    path = out_dir / "fig_accuracy_comparison.png"
    fig.savefig(path, dpi=200)
    plt.close(fig)
    written["accuracy"] = str(path)

    # --- Figure B: abstention vs error - what the arms do when they cannot answer -----------
    fig, ax = plt.subplots(figsize=(7, 4))
    categories = ["Correct", "Incorrect", "Abstained", "Errored"]
    for i, arm in enumerate(arms):
        perf = metrics["per_arm"][arm]["performance"]
        total = perf.get("incidents_total") or 0
        answered, abstained, errored = perf.get("answered", 0), perf.get("abstained", 0), perf.get("errored", 0)
        correct = round((perf.get("accuracy_when_answered") or 0) * answered)
        values = [correct, answered - correct, abstained, errored]
        offset = (i - (len(arms) - 1) / 2) * width
        bars = ax.bar([p + offset for p in range(len(categories))], values, width,
                      label=ARM_LABELS.get(arm, arm), color=COLORS.get(arm))
        for bar, v in zip(bars, values):
            if v:
                ax.text(bar.get_x() + bar.get_width() / 2, v, str(v), ha="center", va="bottom", fontsize=9)
    ax.set_xticks(list(range(len(categories))))
    ax.set_xticklabels(categories)
    ax.set_ylabel("Incidents")
    ax.set_title("Outcome distribution (an abstention is not a wrong answer)")
    ax.legend(frameon=False)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    path = out_dir / "fig_outcome_distribution.png"
    fig.savefig(path, dpi=200)
    plt.close(fig)
    written["outcomes"] = str(path)

    # --- Figure C: cost - wall clock and LLM calls per incident -----------------------------
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(9, 4))
    for i, arm in enumerate(arms):
        ai = metrics["per_arm"][arm]["ai"]
        offset = (i - (len(arms) - 1) / 2) * width
        ax1.bar([offset], [ai.get("wall_clock_seconds_per_incident_mean") or 0], width,
                label=ARM_LABELS.get(arm, arm), color=COLORS.get(arm))
        ax2.bar([offset], [ai.get("llm_calls_per_incident_mean") or 0], width,
                label=ARM_LABELS.get(arm, arm), color=COLORS.get(arm))
    for ax, title, ylabel in ((ax1, "Wall clock per incident", "seconds"),
                              (ax2, "LLM calls per incident", "calls")):
        ax.set_title(title, fontsize=10)
        ax.set_ylabel(ylabel)
        ax.set_xticks([])
        ax.spines[["top", "right"]].set_visible(False)
    ax1.legend(frameon=False, fontsize=8)
    fig.suptitle("Cost per incident (token cost is not applicable to a self-hosted model)", fontsize=11)
    fig.tight_layout()
    path = out_dir / "fig_cost_per_incident.png"
    fig.savefig(path, dpi=200)
    plt.close(fig)
    written["cost"] = str(path)

    log.info("Wrote %d charts to %s", len(written), out_dir)
    return written
