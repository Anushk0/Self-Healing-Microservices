"""Generate every figure in the IEEE manuscript.

Figs. 1-3 are diagrams; Figs. 4-7 are read directly from docs/evaluation/, so no plotted
number is typed by hand.

Both are drawn from what the code does, not from a design sketch. Fig. 2 mirrors
ai_orchestration/graph/workflow.py:build_graph node for node and edge for edge, so if the graph
changes this script is the thing to update.

    python docs/paper/figures/make_figures.py

Writes PNG at 600 dpi and PDF (vector) for each figure. IEEE asks for 300 dpi minimum; the vector
PDF is what to upload if the venue accepts it.

Style: black and grey only, Times for prose, a monospace face for node names, sized for a 3.5 in
column so nothing is rescaled on insertion.
"""
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch  # noqa: E402

OUT = Path(__file__).resolve().parent
plt.rcParams.update({
    "font.family": "serif",
    "font.serif": ["Times New Roman", "Times", "DejaVu Serif"],
    "font.size": 7,
})
MONO = {"family": "monospace", "size": 6.3}

INK = "#000000"
GREY = "#6b6b6b"
FILL = "#ffffff"
FILL_GATE = "#e9e9e9"   # governance gates
FILL_TERM = "#d4d4d4"   # terminal states


def box(ax, x, y, w, h, text, *, fill=FILL, mono=True, bold=False, sub=None, lw=0.7, ls="-",
        msize=None):
    ax.add_patch(FancyBboxPatch((x - w / 2, y - h / 2), w, h,
                                boxstyle="round,pad=0.012,rounding_size=0.04",
                                fc=fill, ec=INK, lw=lw, ls=ls))
    kw = dict(MONO) if mono else {"size": 7}
    if msize:
        kw["size"] = msize
    if bold:
        kw["weight"] = "bold"
    if sub:
        ax.text(x, y + h * 0.16, text, ha="center", va="center", **kw)
        ax.text(x, y - h * 0.24, sub, ha="center", va="center", size=5.6, style="italic",
                color=GREY)
    else:
        ax.text(x, y, text, ha="center", va="center", **kw)


def arrow(ax, p, q, *, ls="-", color=INK, rad=0.0, label=None, lpos=0.5, loff=(0, 0),
          lw=0.7, lsize=5.6):
    ax.add_patch(FancyArrowPatch(p, q, arrowstyle="-|>", mutation_scale=6, lw=lw,
                                 color=color, ls=ls, shrinkA=1.5, shrinkB=1.5,
                                 connectionstyle=f"arc3,rad={rad}"))
    if label:
        lx = p[0] + (q[0] - p[0]) * lpos + loff[0]
        ly = p[1] + (q[1] - p[1]) * lpos + loff[1]
        ax.text(lx, ly, label, ha="center", va="center", size=lsize, style="italic",
                color=color, bbox=dict(fc="white", ec="none", pad=0.4))


def poly(ax, pts, *, ls="-", color=INK, lw=0.7):
    """Right-angled connector ending in an arrowhead."""
    xs, ys = zip(*pts)
    ax.plot(xs[:-1], ys[:-1], color=color, lw=lw, ls=ls, solid_capstyle="butt")
    arrow(ax, pts[-2], pts[-1], ls=ls, color=color, lw=lw)


# ================================================================ Fig. 2: LangGraph state graph
def figure_langgraph():
    """Mirrors graph/workflow.py:build_graph. The spine is the success path; refusal routes run in
    dedicated lanes on the left, the retry loop in a lane on the right, so no route crosses a box."""
    W, H = 3.45, 5.55
    fig, ax = plt.subplots(figsize=(W, H))
    ax.set_xlim(0, W)
    ax.set_ylim(0, H)
    ax.axis("off")

    sx, bw, bh = 1.62, 1.62, 0.25
    left, right = sx - bw / 2, sx + bw / 2          # 0.81, 2.43
    y = {
        "START": 5.36, "extract_features": 5.00, "detect_anomaly": 4.60, "interpret": 4.20,
        "retrieve_similar_incidents": 3.80, "root_cause_analysis": 3.36,
        "propose_remediation": 2.92, "safety_validator": 2.48, "human_approval": 2.04,
        "execute_action": 1.60, "verify_recovery": 1.16, "feedback": 0.72, "finalize": 0.36,
        "END": 0.08,
    }
    tall = {"root_cause_analysis": 0.04, "human_approval": 0.04}

    ax.text(sx, y["START"], "START", ha="center", va="center", size=6.3, weight="bold")
    ax.text(sx, y["END"], "END", ha="center", va="center", size=6.3, weight="bold")

    subs = {
        "detect_anomaly": "per-service Isolation Forest",
        "interpret": "LLM narration only",
        "retrieve_similar_incidents": "Memory partition only",
        "root_cause_analysis": "signature matching decides",
        "propose_remediation": "fixed lookup, no model call",
        "safety_validator": "deterministic, no AI",
        "human_approval": "interrupt(): waits for resume",
        "execute_action": "re-checks all gates",
        "verify_recovery": "re-observes the service",
        "feedback": "Evaluation set refused",
    }
    for name in ("extract_features", "detect_anomaly", "interpret", "retrieve_similar_incidents",
                 "root_cause_analysis", "propose_remediation", "safety_validator",
                 "human_approval", "execute_action", "verify_recovery", "feedback", "finalize"):
        fill = FILL_GATE if name in ("safety_validator", "human_approval") else (
            FILL_TERM if name == "finalize" else FILL)
        box(ax, sx, y[name], bw, bh + tall.get(name, 0), name, fill=fill, sub=subs.get(name),
            lw=1.3 if name == "human_approval" else 0.7)

    def top(n):
        return y[n] + (bh + tall.get(n, 0)) / 2

    def bot(n):
        return y[n] - (bh + tall.get(n, 0)) / 2

    spine = ["extract_features", "detect_anomaly", "interpret", "retrieve_similar_incidents",
             "root_cause_analysis", "propose_remediation", "safety_validator", "human_approval",
             "execute_action", "verify_recovery", "feedback", "finalize"]
    arrow(ax, (sx, y["START"] - 0.07), (sx, top("extract_features")))
    labels = {"safety_validator": "pass", "human_approval": "approved",
              "verify_recovery": "recovered"}
    for a, b in zip(spine, spine[1:]):
        if a == "retrieve_similar_incidents":
            arrow(ax, (sx, bot(a)), (sx, top(b)), ls=(0, (2.2, 1.6)))
            ax.text(right + 0.06, (bot(a) + top(b)) / 2, "advisory only:\nadjusts confidence,\n"
                    "cannot change the answer", ha="left", va="center", size=5.3, style="italic")
            continue
        arrow(ax, (sx, bot(a)), (sx, top(b)), label=labels.get(a),
              loff=(0.27 if a in labels else 0, 0))
    arrow(ax, (sx, bot("finalize")), (sx, y["END"] + 0.06))

    # ---- three refusal lanes on the left, each labelled along its own line
    def lane(x, y_from, y_to, text, y_text):
        poly(ax, [(left, y_from), (x, y_from), (x, y_to), (left, y_to)])
        ax.text(x + 0.05, y_text, text, rotation=90, ha="left", va="center", size=5.3,
                style="italic")

    lane(0.12, y["detect_anomaly"], y["finalize"], "not anomalous: abstain", 3.35)
    lane(0.34, y["safety_validator"] - 0.04, y["feedback"] + 0.06, "safety reject", 1.62)
    lane(0.56, y["human_approval"] - 0.07, y["feedback"] - 0.04, "human reject", 1.30)

    # ---- retry lane on the right: verify -> record_failed_action -> propose (not RCA)
    rx = 2.95
    ry = (y["verify_recovery"] + y["propose_remediation"]) / 2 + 0.10
    box(ax, rx, ry, 0.80, 0.27, "record_failed_\naction", msize=5.8)
    poly(ax, [(right, y["verify_recovery"]), (rx, y["verify_recovery"]), (rx, ry - 0.135)])
    ax.text(rx + 0.05, (y["verify_recovery"] + ry) / 2 - 0.12, "not\nrecovered", ha="left",
            va="center", size=5.3, style="italic")
    poly(ax, [(rx, ry + 0.135), (rx, y["propose_remediation"]), (right, y["propose_remediation"])])
    ax.text(rx + 0.05, (ry + y["propose_remediation"]) / 2 + 0.06, "retry:\nnew action,\n"
            "re-validated,\nre-approved", ha="left", va="center", size=5.3, style="italic")

    fig.subplots_adjust(0, 0, 1, 1)
    for ext in ("png", "pdf"):
        fig.savefig(OUT / f"fig2_langgraph.{ext}", dpi=600)
    plt.close(fig)


# ================================================================ Fig. 1: architecture
def figure_architecture():
    """Components and trust boundary. The operator sits outside the boundary and reaches the
    orchestrator only through the interrupt; actions reach the cluster only after approval."""
    W, H = 3.45, 3.25
    fig, ax = plt.subplots(figsize=(W, H))
    ax.set_xlim(0, W)
    ax.set_ylim(0, H)
    ax.axis("off")

    ax.add_patch(FancyBboxPatch((0.05, 0.40), 3.35, 2.80,
                                boxstyle="round,pad=0,rounding_size=0.05",
                                fc="none", ec=GREY, lw=0.6, ls=(0, (3, 2))))
    ax.text(3.35, 0.47, "system boundary", size=5.3, style="italic", color=GREY, ha="right",
            va="center")

    # Monitored application; payment-service calls the other three synchronously.
    ax.text(1.72, 3.08, "Monitored application (Spring Boot on Kubernetes)", ha="center",
            size=6.2, weight="bold")
    xs = {"auth": 0.56, "payment": 1.34, "ledger": 2.12, "notification": 2.90}
    for name, x in xs.items():
        box(ax, x, 2.62, 0.72, 0.30, f"{name}-\nservice", msize=5.6)
    for name in ("auth", "ledger", "notification"):
        ax.annotate("", xy=(xs[name], 2.78), xytext=(xs["payment"], 2.78),
                    arrowprops=dict(arrowstyle="-|>", lw=0.5, color=GREY, mutation_scale=5,
                                    connectionstyle="arc3,rad=%s" % ("0.35" if name == "auth"
                                                                     else "-0.22")))

    box(ax, 1.72, 2.10, 2.84, 0.26, "Telemetry collector + synthetic login-and-payment probe",
        mono=False)
    arrow(ax, (1.72, 2.47), (1.72, 2.23), label="metrics, health, fault status", lpos=0.5,
          loff=(0.66, 0))

    box(ax, 1.72, 1.50, 2.84, 0.58, "", fill=FILL_GATE)
    ax.text(1.72, 1.70, "Orchestration: LangGraph state graph (Fig. 2)", ha="center", size=6.4,
            weight="bold")
    ax.text(1.72, 1.52, "detect → diagnose → propose → validate → approve "
            "→ execute → verify", ha="center", size=5.6)
    ax.text(1.72, 1.35, "root cause decided by signature matching; LLM narrates only",
            ha="center", size=5.4, style="italic")
    arrow(ax, (1.72, 1.97), (1.72, 1.79))

    for x, name, sub, ls in ((0.80, "Isolation Forest", "one per service", "-"),
                             (1.72, "Retrieval memory", "ChromaDB, Memory set", (0, (2.2, 1.6))),
                             (2.64, "Local LLM", "Ollama, narration", "-")):
        box(ax, x, 0.80, 0.84, 0.34, name, mono=False, sub=sub)
        arrow(ax, (x, 1.21), (x, 0.97), ls=ls)

    # Human operator outside the boundary: left lane, interrupt and resume.
    box(ax, 1.72, 0.16, 1.55, 0.22, "Human operator (approve / reject)", mono=False, lw=1.2)
    lx = 0.17
    ax.plot([0.945, lx, lx], [0.16, 0.16, 1.50], color=INK, lw=0.8)
    arrow(ax, (lx, 1.50), (0.30, 1.50), lw=0.8)
    arrow(ax, (lx + 0.001, 0.16), (0.945, 0.16), lw=0.8)
    ax.text(lx + 0.05, 1.02, "interrupt / resume", rotation=90, ha="left", va="center",
            size=5.3, style="italic")

    # Approved actions only: right lane back up to the services.
    rx = 3.29
    ax.plot([3.14, rx, rx], [1.62, 1.62, 2.30], color=INK, lw=0.8)
    arrow(ax, (rx, 2.30), (rx, 2.46), lw=0.8)
    ax.text(rx + 0.04, 1.96, "approved action only", rotation=90, ha="left", va="center",
            size=5.3, style="italic")

    fig.subplots_adjust(0, 0, 1, 1)
    for ext in ("png", "pdf"):
        fig.savefig(OUT / f"fig1_architecture.{ext}", dpi=600)
    plt.close(fig)


# ================================================================ Figs. 3-7, read from the artefacts
import json  # noqa: E402
import math  # noqa: E402

EVAL = OUT.parents[1] / "evaluation"
DARK, HATCH = "#3a3a3a", "////"


def _json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def wilson(k, n, z=1.96):
    if n == 0:
        return 0.0, 0.0
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, c - h), min(1.0, c + h)


def _pct_label(v):
    """Match the manuscript: whole numbers for 0% and 100%, one decimal otherwise."""
    return f"{v:.0%}" if v in (0.0, 1.0) else f"{v * 100:.1f}%"


def _bars(ax, x, vals, cis, *, dark, labels_fmt=None):
    """Bars with Wilson intervals on every bar, value printed above the interval."""
    style = dict(color=DARK, edgecolor=INK) if dark else dict(color="white", edgecolor=INK,
                                                             hatch=HATCH)
    ax.bar(x, vals, width=0.36, lw=0.6, **style)
    # max(0, ...): at p = 0 or 1 the interval edge equals the value up to float rounding
    lo = [max(0.0, v - c[0]) for v, c in zip(vals, cis)]
    hi = [max(0.0, c[1] - v) for v, c in zip(vals, cis)]
    ax.errorbar(x, vals, yerr=[lo, hi], fmt="none", ecolor=INK, elinewidth=0.6, capsize=2,
                capthick=0.6)
    for xi, v, c in zip(x, vals, cis):
        text = labels_fmt.format(v) if labels_fmt else _pct_label(v)
        ax.text(xi, c[1] + 0.03, text, ha="center", va="bottom", size=5.6)


def _axes(ax, ymax=1.18, pct=True):
    ax.set_ylim(0, ymax)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_linewidth(0.6)
    ax.tick_params(width=0.6, length=2.5, labelsize=6)
    if pct:
        ax.set_yticks([0, 0.25, 0.5, 0.75, 1.0])
        ax.set_yticklabels(["0%", "25%", "50%", "75%", "100%"])


def _legend(ax, second="Single-agent baseline"):
    from matplotlib.patches import Patch
    ax.legend(handles=[Patch(fc=DARK, ec=INK, lw=0.6, label="Multi-agent"),
                       Patch(fc="white", ec=INK, lw=0.6, hatch=HATCH, label=second)],
              fontsize=5.8, frameon=False, loc="upper center", bbox_to_anchor=(0.5, 1.17),
              ncol=2, handlelength=1.6)


def _save(fig, name):
    for ext in ("png", "pdf"):
        fig.savefig(OUT / f"{name}.{ext}", dpi=600, bbox_inches="tight", pad_inches=0.02)
    plt.close(fig)


def figure_accuracy():
    m = _json(EVAL / "metrics.json")
    total = m["dataset"]["evaluation_incident_count"]
    groups = ["All incidents", "When answered", "Cross-service", "Same-service"]
    arms = {}
    for arm in ("multi_agent", "single_agent"):
        p = m["per_arm"][arm]["performance"]
        n, nc = p["answered"], p["cross_service_count"]
        ns = n - nc
        k = round(p["accuracy_when_answered"] * n)
        kc = round(p["cross_service_accuracy"] * nc)
        ks = round(p["same_service_accuracy"] * ns)
        arms[arm] = [(k, total), (k, n), (kc, nc), (ks, ns)]
    fig, ax = plt.subplots(figsize=(3.45, 2.05))
    xs = list(range(len(groups)))
    for arm, off, dark in (("multi_agent", -0.19, True), ("single_agent", 0.19, False)):
        vals = [k / n for k, n in arms[arm]]
        _bars(ax, [x + off for x in xs], vals, [wilson(k, n) for k, n in arms[arm]], dark=dark)
    _axes(ax)
    ax.set_xticks(xs)
    ax.set_xticklabels([f"{g}\n(n = {arms['multi_agent'][i][1]})" for i, g in enumerate(groups)],
                       size=5.8)
    ax.set_ylabel("Root-cause accuracy", size=6.2)
    _legend(ax)
    _save(fig, "fig4_accuracy")


def figure_negative_control():
    r = _json(EVAL / "negative_control" / "negative_control.json")
    rows = [("Normal phase", r["per_phase"]["normal"]), ("Recovery phase", r["per_phase"]["recovery"]),
            ("Combined", r["combined"])]
    fig, ax = plt.subplots(figsize=(3.45, 1.85))
    xs = list(range(len(rows)))
    vals = [b["false_positives"] / b["records_scored"] for _, b in rows]
    cis = [wilson(b["false_positives"], b["records_scored"]) for _, b in rows]
    _bars(ax, xs, vals, cis, dark=True, labels_fmt="{:.2%}")
    _axes(ax, ymax=0.5, pct=False)
    ax.set_yticks([0, 0.1, 0.2, 0.3, 0.4, 0.5])
    ax.set_yticklabels(["0%", "10%", "20%", "30%", "40%", "50%"])
    ax.set_xticks(xs)
    ax.set_xticklabels([f"{n}\n({b['false_positives']} / {b['records_scored']})" for n, b in rows],
                       size=5.8)
    ax.set_ylabel("False-positive rate", size=6.2)
    _save(fig, "fig5_negative_control")


def figure_ablations():
    main = _json(EVAL / "metrics.json")["per_arm"]
    a = _json(EVAL / "ablations" / "no_retrieval" / "metrics.json")["per_arm"]
    b = _json(EVAL / "ablations" / "rag_override" / "metrics.json")["per_arm"]

    def cross(per):
        p = per["performance"]
        n = p["cross_service_count"]
        return round(p["cross_service_accuracy"] * n), n

    fig, ax = plt.subplots(figsize=(3.45, 1.95))
    multi = [cross(main["multi_agent"]), cross(a["multi_agent"]), cross(b["multi_agent"])]
    single = [cross(main["single_agent"]), cross(a["single_agent"])]
    _bars(ax, [-0.19, 0.81, 2.0], [k / n for k, n in multi], [wilson(k, n) for k, n in multi],
          dark=True)
    _bars(ax, [0.19, 1.19], [k / n for k, n in single], [wilson(k, n) for k, n in single],
          dark=False)
    ax.text(2.0, 0.86, "run on the multi-agent\narm only; equals the\nbaseline's shipped 0%",
            ha="center", va="center", size=5.2, style="italic")
    _axes(ax)
    ax.set_xticks([0, 1, 2])
    ax.set_xticklabels(["Shipped", "No retrieval\n(Ablation A)", "Retrieval may override\n"
                        "(Ablation B)"], size=5.8)
    ax.set_ylabel("Cross-service accuracy\n(n = 4)", size=6.2)
    _legend(ax)
    _save(fig, "fig6_ablations")


def figure_cost():
    p = _json(EVAL / "metrics.json")["per_arm"]
    calls = [p[a]["ai"]["llm_calls_per_incident_mean"] for a in ("multi_agent", "single_agent")]
    wall = [p[a]["ai"]["wall_clock_seconds_per_incident_mean"] for a in ("multi_agent", "single_agent")]
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(3.45, 1.7))
    for ax, vals, fmt, ymax, ticks, lab in (
            (a1, calls, "{:.4f}", 0.5, [0, 0.1, 0.2, 0.3, 0.4, 0.5], "Model calls per incident"),
            (a2, wall, "{:.2f} s", 10, [0, 2, 4, 6, 8, 10], "Wall clock per incident (s)")):
        ax.bar([0], [vals[0]], width=0.55, color=DARK, edgecolor=INK, lw=0.6)
        ax.bar([1], [vals[1]], width=0.55, color="white", edgecolor=INK, lw=0.6, hatch=HATCH)
        for x, v in zip((0, 1), vals):
            ax.text(x, v + ymax * 0.02, fmt.format(v), ha="center", va="bottom", size=5.6)
        _axes(ax, ymax=ymax * 1.1, pct=False)
        ax.set_yticks(ticks)
        ax.set_xticks([0, 1])
        ax.set_xticklabels(["Multi-agent", "Single-agent"], size=5.8)
        ax.set_title(lab, size=6.2, pad=3)
    fig.tight_layout(w_pad=1.2)
    _save(fig, "fig7_cost")


def figure_fault_classes():
    """Same-service versus cross-service: where the fault is injected versus where it shows."""
    fig, ax = plt.subplots(figsize=(3.45, 2.35))
    ax.set_xlim(0, 3.45)
    ax.set_ylim(0, 2.35)
    ax.axis("off")
    panels = [
        (1.22, "Cross-service: naming the reporting service is wrong",
         [("notification-service", "notification-latency", "delays the synchronous call"),
          ("auth-service", "auth-key-error", "rejects the token")]),
        (0.02, "Same-service: naming the reporting service is right",
         [("payment-service", "memory-leak", None), ("ledger-service", "db-lock", None)]),
    ]
    for y0, title, cases in panels:
        ax.add_patch(FancyBboxPatch((0.03, y0 + 0.02), 3.39, 1.08,
                                    boxstyle="round,pad=0,rounding_size=0.04",
                                    fc="none", ec=GREY, lw=0.6))
        ax.text(1.725, y0 + 0.99, title, ha="center", va="center", size=6.2, weight="bold")
        for i, (svc, fault, how) in enumerate(cases):
            cx = 0.90 + i * 1.65
            if how:
                box(ax, cx, y0 + 0.72, 1.45, 0.26, svc, fill=FILL_GATE, msize=6.0,
                    sub=f"root cause: {fault}")
                arrow(ax, (cx, y0 + 0.59), (cx, y0 + 0.37), label=how, lsize=5.3)
                box(ax, cx, y0 + 0.24, 1.45, 0.26, "payment-service", msize=6.0,
                    sub="symptom observed here")
            else:
                box(ax, cx, y0 + 0.46, 1.45, 0.46, svc, fill=FILL_GATE, msize=6.0,
                    sub=f"fault: {fault}\ncause and symptom here")
    _save(fig, "fig3_fault_classes")


if __name__ == "__main__":
    figure_architecture()
    figure_langgraph()
    figure_fault_classes()
    figure_accuracy()
    figure_negative_control()
    figure_ablations()
    figure_cost()
    print("wrote", sorted(p.name for p in OUT.glob("fig*_*.png")))
