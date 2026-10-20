"""Reusable presentation components.

Every one is pure presentation: it takes values and renders them. None of them fetch data, call
the pipeline, or decide anything. That keeps the console's business logic exactly where it was
and makes the visual language consistent across pages.
"""
from __future__ import annotations

import html
from typing import Iterable, Optional, Sequence

import streamlit as st

from . import theme

_ESC = lambda v: html.escape(str(v), quote=False)  # noqa: E731


# --- primitives ---------------------------------------------------------------------------------

def pill(label: str, tone: str = "neutral", dot: bool = True) -> str:
    """Inline status pill. Returns HTML so it can be embedded inside other markup."""
    fg, bg = theme.colour(tone)
    d = '<span class="dot"></span>' if dot else ""
    return (f'<span class="pill" style="color:{fg};background:{bg}">{d}{_ESC(label)}</span>')


def spacer(step: int = 4) -> None:
    """Vertical rhythm from the spacing scale rather than ad-hoc inline heights."""
    st.markdown(f'<div class="sp-{step}"></div>', unsafe_allow_html=True)


def page_header(title: str, subtitle: str = "", right: str = "") -> None:
    st.markdown(
        f'<div style="display:flex;align-items:flex-start;justify-content:space-between;'
        f'gap:16px;margin-bottom:20px">'
        f'<div><div class="page-title">{_ESC(title)}</div>'
        + (f'<div class="page-sub">{_ESC(subtitle)}</div>' if subtitle else "")
        + "</div>"
        + (f"<div>{right}</div>" if right else "")
        + "</div>",
        unsafe_allow_html=True,
    )


def tile(label: str, value: str, note: str = "", tone: Optional[str] = None,
         status: str = "") -> str:
    """A metric tile.

    `tone` colours a thin left rail, never the number. Colour on this interface means *state*;
    using it to decorate one figure and not another made the tiles look arbitrary. The value
    itself always renders in the primary text colour so figures read consistently page to page.
    `status` adds a small pill under the value when the tile genuinely has a state to report.
    """
    rail = ""
    if tone:
        fg, _ = theme.colour(tone)
        rail = f"border-left:2px solid {fg}"
    status_html = ""
    if status and tone:
        status_html = f'<div style="margin-top:7px">{pill(status, tone)}</div>'
    return (
        f'<div class="tile" style="{rail}"><div class="tile-label">{_ESC(label)}</div>'
        f'<div class="tile-value">{_ESC(value)}</div>'
        + status_html
        + (f'<div class="tile-note">{note}</div>' if note else "")
        + "</div>"
    )


def tile_row(tiles: Sequence[str]) -> None:
    """Render tiles in an evenly spaced row. Uses Streamlit columns so they stack on narrow
    viewports rather than overflowing."""
    if not tiles:
        return
    cols = st.columns(len(tiles), gap="small")
    for col, markup in zip(cols, tiles):
        col.markdown(markup, unsafe_allow_html=True)


_PANEL_STACK: list = []


def panel_open(title: str = "", right: str = "", tight: bool = False) -> None:
    """Open a panel.

    Streamlit widgets cannot be wrapped in a raw HTML `<div>` written across two markdown calls -
    each call is its own DOM container, so the opening tag closes immediately and the result is an
    empty box. A real bordered container is used instead, entered here and exited by
    `panel_close()`; `theme.py` styles `stVerticalBlockBorderWrapper` to match the design system.
    """
    container = st.container(border=True)
    container.__enter__()
    _PANEL_STACK.append(container)
    if title or right:
        st.markdown(
            f'<div class="panel-head"><h3>{_ESC(title)}</h3><div>{right}</div></div>',
            unsafe_allow_html=True,
        )


def panel_close() -> None:
    if _PANEL_STACK:
        _PANEL_STACK.pop().__exit__(None, None, None)


def kv_rows(rows: Iterable[tuple[str, str]]) -> None:
    body = "".join(
        f'<div class="kv"><div class="kv-k">{_ESC(k)}</div><div class="kv-v">{v}</div></div>'
        for k, v in rows
    )
    st.markdown(body, unsafe_allow_html=True)


def verdict(lead: str, main: str, sub: str = "", tone: str = "") -> None:
    cls = f"verdict {tone}".strip()
    st.markdown(
        f'<div class="{cls}"><div class="verdict-lead">{_ESC(lead)}</div>'
        f'<div class="verdict-main">{_ESC(main)}</div>'
        + (f'<div class="verdict-sub">{sub}</div>' if sub else "")
        + "</div>",
        unsafe_allow_html=True,
    )


def empty_state(title: str, body: str, compact: bool = False) -> None:
    cls = "empty compact" if compact else "empty"
    body_style = "" if compact else "margin:0 auto"
    st.markdown(
        f'<div class="{cls}"><div class="empty-title">{_ESC(title)}</div>'
        f'<div class="empty-body" style="{body_style}">{body}</div></div>',
        unsafe_allow_html=True,
    )


def note(text: str) -> None:
    st.markdown(f'<div class="note">{text}</div>', unsafe_allow_html=True)


def eyebrow(text: str) -> None:
    st.markdown(f'<div class="eyebrow">{_ESC(text)}</div>', unsafe_allow_html=True)


# --- pipeline stepper ---------------------------------------------------------------------------

PIPELINE_STAGES = ["Telemetry", "Detect", "Diagnose", "Safety check",
                   "Human approval", "Execute", "Verify"]


def stepper(states: Sequence[str], captions: Optional[Sequence[str]] = None) -> None:
    """`states[i]` is one of done | now | fail | skip | idle for PIPELINE_STAGES[i].

    A stage is never drawn as complete unless the caller says it completed - the console's job
    is to report the pipeline's real position, and an optimistic stepper would misrepresent a
    run that short-circuited.
    """
    captions = captions or [""] * len(PIPELINE_STAGES)
    parts = ['<div class="stepper">']
    for i, (stage, state) in enumerate(zip(PIPELINE_STAGES, states)):
        cls = state if state in ("done", "now", "fail", "skip") else ""
        glyph = {"done": "✓", "fail": "✕", "skip": "–"}.get(state, str(i + 1))
        cap = captions[i] if i < len(captions) else ""
        parts.append(
            f'<div class="stp {cls}"><div class="stp-node">{glyph}</div>'
            f'<div class="stp-wrap"><div class="stp-txt">{_ESC(stage)}</div>'
            + (f'<div class="stp-sub">{_ESC(cap)}</div>' if cap else "")
            + "</div></div>"
        )
    parts.append("</div>")
    st.markdown("".join(parts), unsafe_allow_html=True)


# --- service presentation -------------------------------------------------------------------

def service_chip(name: str, role: str = "", kind: str = "") -> str:
    cls = f"svc {kind}".strip()
    return (f'<div class="{cls}">'
            + (f'<div class="svc-role">{_ESC(role)}</div>' if role else "")
            + f'<div class="svc-name">{_ESC(name)}</div></div>')


def causal_flow(symptom: str, cause: str) -> None:
    """The project's whole thesis in one graphic: where it hurt vs what was broken."""
    if symptom == cause:
        st.markdown(
            '<div style="display:grid;grid-template-columns:1fr;gap:8px">'
            + service_chip(cause, "Symptom and root cause", "cause")
            + "</div>",
            unsafe_allow_html=True,
        )
        return
    st.markdown(
        '<div style="display:grid;grid-template-columns:1fr 32px 1fr;gap:8px;align-items:stretch">'
        + service_chip(symptom, "Symptoms observed on", "symptom")
        + '<div class="flow-arrow">&#8594;</div>'
        + service_chip(cause, "Root cause", "cause")
        + "</div>",
        unsafe_allow_html=True,
    )


def evidence_item(metric: str, value: str, description: str) -> str:
    return (f'<div class="ev"><div class="ev-metric">{_ESC(metric)} = {_ESC(value)}</div>'
            f'<div class="ev-desc">{_ESC(description)}</div></div>')


# --- simple charts, drawn as HTML so they inherit the theme ----------------------------------

def bar(label: str, value: float, total: float, tone: str = "accent",
        caption: str = "", show_pct: bool = True) -> str:
    """A proportion bar.

    The caption states the proportion as a percentage *and* the raw fraction: a bare "12 / 48"
    leaves the reader computing the fill themselves, and a bare percentage hides the sample size.
    """
    ratio = 0.0 if not total else max(0.0, min(1.0, value / total))
    fg, _ = theme.colour(tone)
    if caption:
        right = caption
    elif show_pct:
        right = f"{ratio * 100:.0f}%  ({value:g}/{total:g})"
    else:
        right = f"{value:g} / {total:g}"
    return (
        f'<div style="margin-bottom:14px">'
        f'<div style="display:flex;justify-content:space-between;gap:12px;margin-bottom:6px">'
        f'<span style="font-size:.8125rem;color:var(--fg);min-width:0;overflow-wrap:anywhere">'
        f'{_ESC(label)}</span>'
        f'<span style="font-size:.75rem;color:var(--fgm);font-variant-numeric:tabular-nums;'
        f'white-space:nowrap">{_ESC(right)}</span></div>'
        f'<div class="bar-track"><div class="bar-fill" '
        f'style="width:{ratio * 100:.1f}%;background:{fg}"></div></div>'
        f"</div>"
    )


def comparison_bars(title: str, rows: Sequence[tuple[str, float, float, str]],
                    unit: str = "") -> None:
    """rows: (label, value, total, tone). `unit="%"` renders values as percentages."""
    if title:
        eyebrow(title)
    if unit == "%":
        markup = "".join(bar(l, v, t, tone, caption=f"{v:.0f}%") for l, v, t, tone in rows)
    else:
        markup = "".join(bar(l, v, t, tone) for l, v, t, tone in rows)
    st.markdown(markup, unsafe_allow_html=True)
