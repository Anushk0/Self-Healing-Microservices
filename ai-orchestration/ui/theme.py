"""Design tokens and global styling for the AIOps console.

One source of truth for colour, type, spacing, radius and shadow. Every component in `ui/`
renders through these tokens rather than inline literals, so the interface stays consistent and
a palette change is a one-file edit.

Dark-first by design: this is an operations console that sits open on a second monitor, and a
deep neutral ground keeps severity colour (amber for pending, red for failure, teal for success)
legible without the interface shouting.
"""
from __future__ import annotations

import streamlit as st

# --- tokens -------------------------------------------------------------------------------------

TOKENS = {
    # ground and layered surfaces
    "bg": "#0B0D10",
    "surface1": "#111418",
    "surface2": "#161A20",
    "surface3": "#1C212A",
    "border": "#232932",
    "borderStrong": "#323A46",
    # type
    "fg": "#E7EAEF",
    "fgMuted": "#98A1AD",
    "fgFaint": "#6C757F",
    # accents
    "accent": "#6366F1",
    "accentHover": "#7F82F7",
    "accentSoft": "rgba(99,102,241,0.14)",
    "success": "#14B8A6",
    "successSoft": "rgba(20,184,166,0.14)",
    "warn": "#F59E0B",
    "warnSoft": "rgba(245,158,11,0.14)",
    "danger": "#EF4444",
    "dangerSoft": "rgba(239,68,68,0.14)",
    "info": "#3B82F6",
    "infoSoft": "rgba(59,130,246,0.14)",
    "neutralSoft": "rgba(152,161,173,0.12)",
}

# Semantic status vocabulary, used by pills, the stepper and service cards alike so one state
# never renders two different ways in two places.
STATUS = {
    "healthy":   ("success", "Healthy"),
    "degraded":  ("warn", "Degraded"),
    "down":      ("danger", "Unavailable"),
    "unknown":   ("neutral", "Unknown"),
    "complete":  ("success", "Complete"),
    "running":   ("info", "In progress"),
    "pending":   ("warn", "Awaiting approval"),
    "failed":    ("danger", "Failed"),
    "skipped":   ("neutral", "Skipped"),
    "idle":      ("neutral", "Not started"),
}

TONE_COLOUR = {
    "success": ("success", "successSoft"),
    "warn": ("warn", "warnSoft"),
    "danger": ("danger", "dangerSoft"),
    "info": ("info", "infoSoft"),
    "accent": ("accent", "accentSoft"),
    "neutral": ("fgMuted", "neutralSoft"),
}


def colour(tone: str) -> tuple[str, str]:
    """(foreground, background) for a semantic tone."""
    fg_key, bg_key = TONE_COLOUR.get(tone, TONE_COLOUR["neutral"])
    return TOKENS[fg_key], TOKENS[bg_key]


def inject() -> None:
    """Inject fonts and the global stylesheet. Call once, at the top of the app."""
    t = TOKENS
    st.html(
        f"""
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap">
<style>
  :root {{
    --bg: {t['bg']};            --s1: {t['surface1']};  --s2: {t['surface2']};
    --s3: {t['surface3']};      --bd: {t['border']};    --bds: {t['borderStrong']};
    --fg: {t['fg']};            --fgm: {t['fgMuted']};  --fgf: {t['fgFaint']};
    --accent: {t['accent']};    --accent-h: {t['accentHover']};  --accent-s: {t['accentSoft']};
    --ok: {t['success']};       --ok-s: {t['successSoft']};
    --warn: {t['warn']};        --warn-s: {t['warnSoft']};
    --bad: {t['danger']};       --bad-s: {t['dangerSoft']};
    --info: {t['info']};        --info-s: {t['infoSoft']};
    --neutral-s: {t['neutralSoft']};
    --r: 8px; --r-sm: 6px; --r-pill: 999px;
    --sp1: 4px; --sp2: 8px; --sp3: 12px; --sp4: 16px; --sp5: 24px; --sp6: 32px; --sp7: 48px;
    --shadow: 0 1px 2px rgba(0,0,0,.4), 0 4px 16px rgba(0,0,0,.24);
    --font: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    --mono: 'JetBrains Mono', ui-monospace, 'Cascadia Mono', Menlo, monospace;
  }}
"""
        + _STATIC_CSS
        + """</style>"""
    )


# Static rules: no interpolation, so CSS braces need no escaping here.
_STATIC_CSS = """
  /* app chrome */
  .stApp { background: var(--bg); }
  html, body, [class*="st-"], .stMarkdown { font-family: var(--font); color: var(--fg); }
  /* Streamlit renders icons as ligature text; the rule above must not reach them */
  span[data-testid="stIconMaterial"], .material-icons, .material-symbols-rounded {
    font-family: 'Material Symbols Rounded', 'Material Icons' !important;
    color: var(--fgm);
  }
  header[data-testid="stHeader"] { background: transparent; height: 0; }
  #MainMenu, footer { visibility: hidden; }
  .block-container { padding: var(--sp5) var(--sp6) var(--sp7); max-width: 1480px; }

  section[data-testid="stSidebar"] {
    background: var(--s1); border-right: 1px solid var(--bd); width: 248px !important;
  }
  section[data-testid="stSidebar"] > div { padding-top: var(--sp4); }

  /* typography */
  h1, h2, h3, h4 { font-family: var(--font); color: var(--fg); letter-spacing: -0.015em; }
  /* Type scale. Roles, not sizes: a page title must outrank every number on the page, and a
     section eyebrow must read as quieter than a metric label sitting next to a value. */
  .page-title { font-size: 1.75rem; font-weight: 680; margin: 0; letter-spacing: -0.028em;
                line-height: 1.2; }
  .page-sub { color: var(--fgm); font-size: .875rem; margin: 5px 0 0; line-height: 1.5; }
  .eyebrow {
    font-size: .6875rem; font-weight: 600; letter-spacing: .1em; text-transform: uppercase;
    color: var(--fgf); margin: 0 0 var(--sp3);
  }
  .mono { font-family: var(--mono); font-size: .8125rem; }
  code, .stCodeBlock { font-family: var(--mono) !important; }
  a { color: var(--accent); }

  /* surfaces */
  .panel {
    background: var(--s1); border: 1px solid var(--bd); border-radius: var(--r);
    padding: var(--sp5); margin-bottom: var(--sp4);
  }
  .panel-tight { padding: var(--sp4); }
  .panel-head {
    display: flex; align-items: center; justify-content: space-between;
    gap: var(--sp3); margin-bottom: var(--sp4);
  }
  .panel-head h3 { font-size: .9375rem; font-weight: 600; margin: 0; }

  /* metric tiles */
  .tile {
    background: var(--s1); border: 1px solid var(--bd); border-radius: var(--r);
    padding: var(--sp4) var(--sp4) var(--sp4); height: 100%;
    transition: border-color .15s ease, background .15s ease;
  }
  .tile:hover { border-color: var(--bds); }
  .tile-label {
    font-size: .75rem; font-weight: 500; letter-spacing: .01em; text-transform: none;
    color: var(--fgm);
  }
  .tile-value {
    font-size: 1.5rem; font-weight: 620; line-height: 1.2; margin-top: 7px;
    letter-spacing: -.022em; font-variant-numeric: tabular-nums; overflow-wrap: anywhere;
  }
  .tile-note { font-size: .75rem; color: var(--fgm); margin-top: 6px; line-height: 1.45; }
  .tile-accent { border-left: 2px solid var(--accent); }

  /* pills */
  .pill {
    display: inline-flex; align-items: center; gap: 5px;
    padding: 2px 9px; border-radius: var(--r-pill);
    font-size: .6875rem; font-weight: 600; letter-spacing: .01em; white-space: nowrap;
  }
  .pill .dot { width: 6px; height: 6px; border-radius: 50%; background: currentColor; }

  /* key/value rows */
  .kv { display: flex; gap: var(--sp3); padding: 7px 0; border-bottom: 1px solid var(--bd); }
  .kv:last-child { border-bottom: 0; }
  .kv-k { color: var(--fgm); font-size: .8125rem; min-width: 150px; flex-shrink: 0; }
  .kv-v { font-size: .8125rem; overflow-wrap: anywhere; }

  /* stepper */
  .stepper {
    display: flex; align-items: flex-start; gap: 0; margin: var(--sp2) 0 var(--sp5);
    flex-wrap: nowrap; overflow-x: auto; padding-bottom: 2px;
  }
  .stp {
    display: flex; align-items: flex-start; gap: var(--sp2); flex: 1 1 0; min-width: 0;
    position: relative; padding-right: var(--sp3);
  }
  /* the connector is drawn by the step itself, so the rail never detaches from its nodes */
  .stp:not(:last-child)::after {
    content: ""; position: absolute; left: 26px; right: 0; top: 13px; height: 1.5px;
    background: var(--bd); z-index: 0;
  }
  .stp.done:not(:last-child)::after { background: var(--ok); opacity: .5; }
  .stp > * { position: relative; z-index: 1; }
  .stp-node { margin-left: 0; }
  .stp-wrap { padding-right: var(--sp4); background: var(--bg); }
  .stp-node {
    width: 26px; height: 26px; border-radius: 50%; flex-shrink: 0;
    display: grid; place-items: center; font-size: .6875rem; font-weight: 700;
    border: 1.5px solid var(--bd); background: var(--s2); color: var(--fgf);
  }
  .stp-txt { font-size: .75rem; font-weight: 550; color: var(--fgf); line-height: 1.25; }
  .stp-sub { font-size: .6875rem; color: var(--fgf); opacity: .8; }
  .stp.done .stp-node { border-color: var(--ok); background: var(--ok-s); color: var(--ok); }
  .stp.done .stp-txt { color: var(--fg); }
  .stp.now  .stp-node { border-color: var(--warn); background: var(--warn-s); color: var(--warn);
                         box-shadow: 0 0 0 3px var(--warn-s); }
  .stp.now  .stp-txt { color: var(--warn); font-weight: 650; }
  .stp.fail .stp-node { border-color: var(--bad); background: var(--bad-s); color: var(--bad); }
  .stp.fail .stp-txt { color: var(--bad); }
  .stp.skip .stp-node { border-style: dashed; }
  .stp.skip .stp-txt { text-decoration: line-through; opacity: .65; }

  /* verdict banner */
  .verdict {
    border: 1px solid var(--bd); border-left: 3px solid var(--accent);
    background: linear-gradient(90deg, var(--accent-s) 0%, transparent 45%), var(--s1);
    border-radius: var(--r); padding: var(--sp5); margin-bottom: var(--sp4);
  }
  .verdict.ok   { border-left-color: var(--ok);   background: linear-gradient(90deg, var(--ok-s) 0%, transparent 45%), var(--s1); }
  .verdict.warn { border-left-color: var(--warn); background: linear-gradient(90deg, var(--warn-s) 0%, transparent 45%), var(--s1); }
  .verdict.bad  { border-left-color: var(--bad);  background: linear-gradient(90deg, var(--bad-s) 0%, transparent 45%), var(--s1); }
  .verdict-lead { font-size: .6875rem; font-weight: 600; letter-spacing: .09em;
                   text-transform: uppercase; color: var(--fgm); }
  .verdict-main { font-size: 1.6rem; font-weight: 680; letter-spacing: -.025em;
                   margin-top: 6px; line-height: 1.2; }
  .verdict-sub { color: var(--fgm); font-size: .875rem; margin-top: 8px; line-height: 1.55; }
  .verdict-sub b { color: var(--fg); font-weight: 600; }

  /* service flow */
  .svc {
    background: var(--s2); border: 1px solid var(--bd); border-radius: var(--r-sm);
    padding: var(--sp3) var(--sp4); display: flex; flex-direction: column; gap: 4px;
  }
  .svc.cause { border-color: var(--bad); background: var(--bad-s); }
  .svc.symptom { border-color: var(--warn); background: var(--warn-s); }
  .svc-name { font-family: var(--mono); font-size: .8125rem; font-weight: 500; }
  .svc-role { font-size: .6875rem; letter-spacing: .07em; text-transform: uppercase; color: var(--fgm); }
  .flow-arrow { color: var(--fgf); font-size: 1.1rem; display: grid; place-items: center; height: 100%; }

  /* evidence */
  .ev {
    background: var(--s2); border: 1px solid var(--bd); border-radius: var(--r-sm);
    padding: var(--sp3); margin-bottom: 6px;
  }
  .ev-metric { font-family: var(--mono); font-size: .8125rem; color: var(--fg); }
  .ev-desc { font-size: .75rem; color: var(--fgm); margin-top: 3px; line-height: 1.45; }

  /* streamlit control overrides */
  button[data-testid="stBaseButton-secondary"],
  button[data-testid="stBaseButton-primary"] {
    border-radius: var(--r-sm); font-weight: 550; font-size: .8125rem;
    padding: .45rem .9rem; transition: all .14s ease;
  }
  button[data-testid="stBaseButton-secondary"] {
    border: 1px solid var(--bd); background: var(--s2); color: var(--fg);
  }
  button[data-testid="stBaseButton-secondary"]:hover {
    border-color: var(--bds); background: var(--s3); color: var(--fg);
  }
  button[data-testid="stBaseButton-primary"] {
    background: var(--accent); border: 1px solid var(--accent); color: #fff;
  }
  button[data-testid="stBaseButton-primary"]:hover {
    background: var(--accent-h); border-color: var(--accent-h); color: #fff;
  }
  button[disabled] { opacity: .45; cursor: not-allowed; }

  /* bordered containers are the panel primitive: Streamlit widgets cannot be wrapped in a
     raw HTML div, so panels are real containers styled to match */
  div[data-testid="stVerticalBlockBorderWrapper"] {
    background: var(--s1); border: 1px solid var(--bd) !important;
    border-radius: var(--r) !important; padding: var(--sp4) var(--sp5) !important;
  }

  div[data-testid="stExpander"] {
    border: 1px solid var(--bd); border-radius: var(--r-sm); background: var(--s2);
  }
  div[data-testid="stExpander"] summary { font-size: .8125rem; color: var(--fgm); font-weight: 550; }
  div[data-testid="stExpander"] summary:hover { color: var(--fg); }

  div[data-baseweb="select"] > div, .stTextInput input, .stTextArea textarea, .stNumberInput input {
    background: var(--s2) !important; border-color: var(--bd) !important;
    color: var(--fg) !important; font-size: .8125rem !important; border-radius: var(--r-sm) !important;
  }
  div[data-baseweb="select"] > div:hover { border-color: var(--bds) !important; }
  div[data-baseweb="popover"] li { font-size: .8125rem; }
  div[data-baseweb="select"] * { color: var(--fg) !important; }
  div[data-baseweb="select"] svg { fill: var(--fgm) !important; }
  div[data-baseweb="popover"] ul { background: var(--s2) !important; border: 1px solid var(--bd); }
  div[data-baseweb="popover"] li:hover { background: var(--s3) !important; }
  div[data-baseweb="menu"] { background: var(--s2) !important; }
  input::placeholder, textarea::placeholder { color: var(--fgf) !important; }
  label, .stRadio label, .stCheckbox label { color: var(--fgm) !important; font-size: .8125rem; }

  /* this Streamlit build renders inputs with react-aria, not baseweb, so the widgets need
     their own rules; the baseweb selectors above are kept for older builds */
  .react-aria-ComboBox > div,
  .react-aria-TextField > div,
  .react-aria-NumberField > div,
  .react-aria-Group,
  .react-aria-Input,
  .react-aria-TextArea {
    background: var(--s2) !important; border-color: var(--bd) !important;
    color: var(--fg) !important; border-radius: var(--r-sm) !important;
  }
  .react-aria-ComboBox > div:hover,
  .react-aria-Group:hover { border-color: var(--bds) !important; }
  .react-aria-ComboBox *, .react-aria-Input, .react-aria-TextArea { color: var(--fg) !important; }
  .react-aria-ComboBox svg, .react-aria-Button svg { fill: var(--fgm) !important; color: var(--fgm) !important; }
  .react-aria-Popover, .react-aria-ListBox, .react-aria-Menu {
    background: var(--s2) !important; border: 1px solid var(--bd) !important;
    border-radius: var(--r-sm) !important; box-shadow: var(--shadow);
  }
  .react-aria-Option, .react-aria-ListBoxItem, .react-aria-MenuItem {
    color: var(--fg) !important; font-size: .8125rem;
  }
  .react-aria-Option[data-focused], .react-aria-ListBoxItem[data-focused],
  .react-aria-Option:hover, .react-aria-ListBoxItem:hover {
    background: var(--s3) !important;
  }
  .react-aria-Option[data-selected], .react-aria-ListBoxItem[data-selected] {
    background: var(--accent-s) !important;
  }

  .stDataFrame, div[data-testid="stTable"] { border: 1px solid var(--bd); border-radius: var(--r-sm); }
  hr { border-color: var(--bd); margin: var(--sp5) 0; }
  div[data-testid="stTabs"] button { font-size: .8125rem; }

  /* sidebar nav: full-width, left-aligned, active state carried by an accent rail */
  section[data-testid="stSidebar"] button[data-testid="stBaseButton-secondary"],
  section[data-testid="stSidebar"] button[data-testid="stBaseButton-primary"] {
    width: 100%; text-align: left; justify-content: flex-start;
    border-radius: 0; border: 0; border-left: 2px solid transparent;
    padding: .5rem .75rem; font-weight: 500;
  }
  section[data-testid="stSidebar"] button[data-testid="stBaseButton-secondary"] {
    background: transparent; color: var(--fgm);
  }
  section[data-testid="stSidebar"] button[data-testid="stBaseButton-secondary"]:hover {
    background: var(--s2); color: var(--fg); border-left-color: var(--bds);
  }
  section[data-testid="stSidebar"] button[data-testid="stBaseButton-primary"] {
    background: var(--accent-s); color: var(--fg);
    border-left: 2px solid var(--accent); font-weight: 600;
  }
  section[data-testid="stSidebar"] button[data-testid="stBaseButton-primary"]:hover {
    background: var(--accent-s); color: var(--fg);
  }
  section[data-testid="stSidebar"] div[data-testid="stVerticalBlockBorderWrapper"] {
    background: transparent; border: 0 !important; padding: 0 !important;
  }

  /* misc */
  .empty {
    border: 1px dashed var(--bd); border-radius: var(--r); padding: var(--sp5) var(--sp4);
    text-align: center; color: var(--fgm); background: transparent;
  }
  .empty.compact { padding: var(--sp4); text-align: left; }
  .empty.compact .empty-title { font-size: .875rem; }
  .empty-title { font-size: .9375rem; font-weight: 600; color: var(--fg); margin-bottom: 6px; }
  .empty-body { font-size: .8125rem; line-height: 1.6; max-width: 46ch; margin: 0 auto; }
  .bar-track { height: 6px; background: var(--s3); border-radius: var(--r-pill); overflow: hidden; }
  .bar-fill { height: 100%; border-radius: var(--r-pill); }
  .sp-2 { height: 8px; }  .sp-3 { height: 12px; }  .sp-4 { height: 16px; }
  .sp-5 { height: 24px; }  .sp-6 { height: 32px; }
  .note { font-size: .75rem; color: var(--fgf); line-height: 1.55; }
"""
