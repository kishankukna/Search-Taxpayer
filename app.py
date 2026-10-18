"""GST Taxpayer Search - Streamlit dashboard.

Run locally:      streamlit run app.py
Deploy:           Streamlit Community Cloud -> New app -> this repo, main file: app.py
"""

import datetime as dt
import html
import logging
import os
import tempfile

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components

try:
    try:  # project layout: gst_search/ package next to app.py (local runs, git push)
        from gst_analytics import COMPLIANCE_ORDER, build_frame, kpis
        from gst_auth import (access_table, auth_configured, current_user, is_admin, is_approved,
                                     record_request, secrets_snippet, set_access, has_signed_up, save_feedback, load_feedback, contact_details)
        from gst_captcha import build_solver
        from gst_gstin import extract_entries, is_valid_gstin
        from gst_io_utils import read_gstins_from_file, write_excel
        from gst_parser import additional_places, flatten_details, records as detail_records
        from gst_runner import RunOptions, load_history, run_batch
        from gst_summary import core_activity
    except ModuleNotFoundError as exc:  # flat layout: gst_*.py files next to app.py (GitHub web upload)
        if exc.name not in ("gst_search", "gst_search.analytics"):
            raise
        from gst_analytics import COMPLIANCE_ORDER, build_frame, kpis
        from gst_auth import (access_table, auth_configured, current_user, is_admin, is_approved,
                              record_request, secrets_snippet, set_access, has_signed_up, save_feedback, load_feedback, contact_details)
        from gst_captcha import build_solver
        from gst_gstin import extract_entries, is_valid_gstin
        from gst_io_utils import read_gstins_from_file, write_excel
        from gst_parser import additional_places, flatten_details, records as detail_records
        from gst_runner import RunOptions, load_history, run_batch
        from gst_summary import core_activity
except ImportError as exc:
    # Typical on GitHub web uploads: app.py was updated but another file is still the old version.
    culprit = f"`{exc.name}.py`" if getattr(exc, "name", None) else "one of the gst_*.py files"
    st.set_page_config(page_title="GST Taxpayer Search", page_icon="🧾")
    st.error(f"**The code files on the server don't match.** {culprit} is missing or is an older version "
             f"than `app.py`.\n\nFix: upload **all** files from the `github_upload` folder to GitHub again "
             f"(Add file → Upload files → select all → Commit), then in Streamlit use **Manage app → ⋮ → "
             f"Reboot app**.\n\nDetails: `{exc}`")
    st.stop()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
for noisy in ("urllib3", "PIL"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

# Validated palette (dataviz reference instance): one series hue + reserved status colours.
SERIES = "#2a78d6"
STATUS = {"good": "#0ca30c", "warning": "#fab219", "serious": "#ec835a", "critical": "#d03b3b",
          "neutral": "#898781"}
STATUS_COLORS = {"Active": STATUS["good"], "Suspended": STATUS["warning"], "Cancelled": STATUS["critical"],
                 "Other": STATUS["neutral"], "Unknown": STATUS["neutral"], "Not fetched": STATUS["neutral"]}
COMPLIANCE_COLORS = {"Up to date": STATUS["good"], "1–2 months behind": STATUS["warning"],
                     "3+ months behind": STATUS["critical"], "No GSTR-3B found": STATUS["neutral"]}


# ------------------------------------------------------------------ look & feel
CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
html, body, .stApp, [data-testid="stSidebar"] { font-family: 'Inter', system-ui, -apple-system, 'Segoe UI', sans-serif; }
footer, [data-testid="stDeployButton"], .stAppDeployButton { display: none !important; }
/* no sidebar: everything lives on the page */
[data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"], [data-testid="collapsedControl"],
[data-testid="stExpandSidebarButton"] { display: none !important; }
@import url('https://fonts.googleapis.com/css2?family=Material+Symbols+Rounded:opsz,wght,FILL,GRAD@24,500,0,0');
.material-symbols-rounded { font-family: 'Material Symbols Rounded'; font-weight: normal; font-style: normal;
                            line-height: 1; letter-spacing: normal; text-transform: none; white-space: nowrap;
                            -webkit-font-feature-settings: 'liga'; font-feature-settings: 'liga'; }
.block-container { padding: 3.6rem 2.6rem 3rem 2.6rem; max-width: 100%; }
/* search input panel */
.panel-title { font-size: 1.45rem; font-weight: 700; margin: 4px 0 2px 0; }
.panel-sub { opacity: .7; font-size: .95rem; margin-bottom: 12px; }
[data-testid="stTextArea"] textarea { border-radius: 14px !important; font-family: 'JetBrains Mono', Consolas, monospace;
                                      font-size: .92rem; }
[data-testid="stTextArea"] > div, [data-testid="stFileUploaderDropzone"] { border-radius: 14px !important; }
[data-testid="stFileUploaderDropzone"] { min-height: 150px; border: 2px dashed rgba(42,120,214,.40) !important;
                                         background: rgba(42,120,214,.04) !important; align-items: center; }
iframe[title="st.iframe"], [data-testid="stIFrame"] { display: none; }
/* hide Streamlit's Share / star / edit / menu bar */
/* (the admin's top navigation also lives in stToolbar, so only the action items are hidden) */
[data-testid="stToolbarActions"], [data-testid="stMainMenu"], [data-testid="stAppDeployButton"],
[data-testid="stDecoration"], #MainMenu { display: none !important; }
/* green GSTIN count under the box */
.detected-txt { color: #0a7d0a; font-weight: 600; font-size: .95rem; margin: -6px 0 0 2px; }
/* what you get */
.about { margin-top: 36px; border-radius: 22px; overflow: hidden; border: 1px solid rgba(137,135,129,.22); }
.about-head { background: linear-gradient(135deg, #104281 0%, #1c5cab 45%, #2a78d6 100%); color: #fff;
              padding: 30px 34px 28px 34px; }
.about-head h2 { color: #fff; font-size: 1.55rem; font-weight: 700; margin: 0 0 6px 0; padding: 0; }
.about-head p { color: rgba(255,255,255,.85); margin: 0; }
.feats { display: grid; grid-template-columns: repeat(5, 1fr); gap: 22px 18px; padding: 28px 30px 30px 30px; }
.feat { display: flex; gap: 12px; align-items: flex-start; }
.feat .material-symbols-rounded { font-size: 22px; color: #2a78d6; background: rgba(42,120,214,.12);
                                  border-radius: 10px; padding: 8px; }
.feat b { display: block; font-size: .96rem; margin-bottom: 3px; }
.feat p { margin: 0; opacity: .72; font-size: .87rem; line-height: 1.45; }
@media (max-width: 1000px) { .feats { grid-template-columns: repeat(2, 1fr); } }
@media (max-width: 600px) { .feats { grid-template-columns: 1fr; } }
/* feedback & contact */
.section-gap { height: 34px; border-bottom: 1px solid rgba(137,135,129,.22); margin-bottom: 26px; }
.section-title { font-size: 1.2rem; font-weight: 700; display: flex; align-items: center; gap: 8px; }
.section-title .material-symbols-rounded { color: #2a78d6; font-size: 24px; }
.contact-card { border: 1px solid rgba(42,120,214,.25); border-radius: 16px; padding: 18px 20px;
                background: linear-gradient(135deg, rgba(42,120,214,.07), rgba(42,120,214,.02)); }
.contact-row { display: flex; align-items: center; gap: 12px; padding: 6px 0; font-size: .92rem; white-space: nowrap; }
.contact-row .material-symbols-rounded { color: #2a78d6; background: rgba(42,120,214,.12); border-radius: 10px;
                                         padding: 7px; font-size: 20px; }
.contact-row a { color: inherit; text-decoration: none; font-weight: 600; }
.footer-note { text-align: center; opacity: .55; font-size: .8rem; margin-top: 30px; }
.brand { font-weight: 700; font-size: 1.05rem; display: flex; align-items: center; gap: 8px; padding-top: 6px; }
.brand .dot { width: 30px; height: 30px; border-radius: 9px; display: inline-flex; align-items: center;
              justify-content: center; background: linear-gradient(135deg, #104281, #2a78d6); color: #fff;
              font-size: .95rem; }
/* brand accent, also when the .streamlit/config.toml theme file is missing (GitHub web upload) */
[data-testid="stBaseButton-primary"] { background: #2a78d6 !important; border-color: #2a78d6 !important;
                                       color: #fff !important; }
[data-testid="stBaseButton-primary"]:hover { background: #1c5cab !important; border-color: #1c5cab !important; }
[data-testid="stBaseButton-primary"]:disabled { background: rgba(42,120,214,.35) !important;
                                                border-color: transparent !important; }
[data-baseweb="tab-highlight"] { background-color: #2a78d6 !important; }
button[role="tab"][aria-selected="true"] p { color: #2a78d6 !important; }
[data-baseweb="slider"] [role="slider"] { background-color: #2a78d6 !important; }
[data-testid="stSliderThumbValue"] { color: #2a78d6 !important; }
[data-baseweb="checkbox"] input:checked + div, [data-baseweb="checkbox"] [aria-checked="true"] {
    background-color: #2a78d6 !important; border-color: #2a78d6 !important; }
[data-baseweb="radio"] input:checked + div { background-color: #2a78d6 !important; border-color: #2a78d6 !important; }
.hero { background: linear-gradient(135deg, #104281 0%, #1c5cab 45%, #2a78d6 100%); color: #fff;
        border-radius: 18px; padding: 26px 30px; margin-bottom: 22px; box-shadow: 0 12px 32px rgba(16,66,129,.28); }
.hero h1 { color: #fff; font-size: 1.8rem; font-weight: 700; margin: 0 0 6px 0; padding: 0; }
.hero p { color: rgba(255,255,255,.86); margin: 0; font-size: .98rem; }
.badge { display: inline-block; padding: 3px 11px; border-radius: 999px; background: rgba(255,255,255,.18);
         color: #fff; font-size: .72rem; font-weight: 600; letter-spacing: .06em; text-transform: uppercase;
         margin-bottom: 10px; }
[data-testid="stMetric"] { background: rgba(42,120,214,.06); border: 1px solid rgba(42,120,214,.20);
                           border-radius: 14px; padding: 14px 16px; }
[data-testid="stMetricLabel"] p { font-weight: 600; font-size: .82rem; opacity: .72; }
[data-testid="stMetricValue"] { font-weight: 700; }
.stButton > button, .stDownloadButton > button, [data-testid="stBaseButton-secondary"] { border-radius: 10px; font-weight: 600; }
[data-testid="stSidebar"] { border-right: 1px solid rgba(137,135,129,.18); }
.card { border: 1px solid rgba(137,135,129,.28); border-radius: 18px; padding: 26px 28px;
        background: rgba(137,135,129,.05); }
.card h3 { margin-top: 0; }
.feature { display: flex; gap: 12px; align-items: flex-start; margin: 14px 0; }
.feature .ic { font-size: 1.3rem; line-height: 1.4rem; }
.feature b { display: block; margin-bottom: 2px; }
.feature span { opacity: .75; font-size: .92rem; }
.profile { display: flex; align-items: center; gap: 10px; padding: 10px 12px; border-radius: 12px;
           background: rgba(42,120,214,.09); margin: 4px 0 10px 0; }
.profile img, .avatar { width: 38px; height: 38px; border-radius: 50%; flex: none; }
.avatar { background: #2a78d6; color: #fff; display: flex; align-items: center; justify-content: center;
          font-weight: 700; }
.profile .n { font-weight: 600; font-size: .9rem; line-height: 1.25; }
.profile .e { font-size: .74rem; opacity: .7; word-break: break-all; }
.pill { display: inline-block; padding: 1px 8px; border-radius: 999px; font-size: .68rem; font-weight: 700;
        background: #2a78d6; color: #fff; margin-left: 4px; vertical-align: middle; }
.muted { opacity: .7; font-size: .9rem; }
</style>
"""


def inject_css():
    st.markdown(CSS, unsafe_allow_html=True)


def hero(title, subtitle, badge="GST Taxpayer Intelligence"):
    st.markdown(f'<div class="hero"><div class="badge">{html.escape(badge)}</div><h1>{html.escape(title)}</h1>'
                f'<p>{html.escape(subtitle)}</p></div>', unsafe_allow_html=True)


# ------------------------------------------------------------------ sign-in & access
def login_page():
    left, right = st.columns([1.25, 1], gap="large")
    with left:
        hero("Verify any GSTIN in seconds",
             "Bulk taxpayer search on the GST portal, with captchas solved automatically.",
             badge="GST Taxpayer Search")
        for icon, title, text in [
            ("⚡", "Bulk & fast", "Paste a list or upload Excel – about 0.25 s per GSTIN with parallel workers."),
            ("🧾", "Your Excel format", "Status with effective dates, offices, HSN, last GSTR-1 / 3B filed."),
            ("📊", "Compliance dashboard", "Active / cancelled / suspended, filing delays, captcha accuracy."),
            ("🔒", "Private access", "Google sign-in; new users are approved by the admin."),
        ]:
            st.markdown(f'<div class="feature"><div class="ic">{icon}</div><div><b>{title}</b>'
                        f'<span>{text}</span></div></div>', unsafe_allow_html=True)
    with right:
        login_tab, signup_tab = st.tabs(["Log in", "Sign up"])
        with login_tab:
            st.markdown('<div class="card"><h3>Welcome back</h3><p class="muted">Log in with the Google account '
                        'you signed up with.</p></div>', unsafe_allow_html=True)
            st.write("")
            if st.button("Log in with Google", type="primary", width="stretch", icon=":material/login:",
                         key="login_btn"):
                st.login()
        with signup_tab:
            st.markdown('<div class="card"><h3>Create your account</h3><p class="muted">1. Continue with Google. '
                        '2. Tell us your name, company and why you need access. 3. The admin approves your '
                        'request and you can start searching.</p></div>', unsafe_allow_html=True)
            st.write("")
            if st.button("Sign up with Google", type="primary", width="stretch", icon=":material/person_add:",
                         key="signup_btn"):
                st.login()
        st.caption("We only receive your name, email and profile photo from Google.")


def signup_form(user):
    """Shown once, after a new user's first Google sign-in."""
    hero("Complete your sign-up", "A few details for the admin who approves your access.", badge="Sign up")
    with st.form("signup"):
        a, b = st.columns(2)
        full_name = a.text_input("Full name", value=user["name"] or "")
        company = b.text_input("Company / firm")
        c, d = st.columns(2)
        phone = c.text_input("Phone (optional)")
        d.text_input("Email", value=user["email"], disabled=True)
        purpose = st.text_area("Why do you need access?", placeholder="e.g. vendor GSTIN verification for our "
                                                                      "accounts team", height=90)
        if st.form_submit_button("Send access request", type="primary", icon=":material/send:"):
            if not full_name.strip() or not company.strip():
                st.error("Please fill in your name and company.")
            else:
                record_request(user, {"full_name": full_name, "company": company, "phone": phone,
                                      "purpose": purpose})
                st.rerun()
    if st.button("Sign out", icon=":material/logout:"):
        st.logout()


def pending_page(user):
    if not has_signed_up(user):
        signup_form(user)
        return
    hero("Access requested", "Your account is waiting for the admin's approval.", badge="Almost there")
    st.markdown(f'<div class="card"><h3>Thanks, {html.escape(user["name"] or "")} 👋</h3>'
                f'<p>Your access request for <b>{html.escape(user["email"])}</b> has been sent. '
                f'Once the admin approves it, refresh this page to start searching.</p></div>',
                unsafe_allow_html=True)
    st.write("")
    a, b, _ = st.columns([1, 1, 3])
    if a.button("Check again", icon=":material/refresh:", width="stretch"):
        st.rerun()
    if b.button("Sign out", icon=":material/logout:", width="stretch"):
        st.logout()


def top_bar(user, admin):
    """Brand on the left, profile menu (name, email, sign out) on the right."""
    left, right = st.columns([5, 1.3], vertical_alignment="center")
    left.markdown('<div class="brand"><span class="dot">🧾</span>GST Taxpayer Search</div>', unsafe_allow_html=True)
    first = (user["name"] or user["email"] or "?").split()[0]
    with right.popover(first, icon=":material/account_circle:", width="stretch"):
        name = html.escape(user["name"] or user["email"])
        email = html.escape(user["email"] if user["mode"] != "local" else "Local mode – no sign-in")
        pic = (f'<img src="{html.escape(user["picture"])}" referrerpolicy="no-referrer">' if user.get("picture")
               else f'<div class="avatar">{html.escape(first[:1].upper())}</div>')
        pill = '<span class="pill">ADMIN</span>' if admin and user["mode"] != "local" else ""
        st.markdown(f'<div class="profile">{pic}<div><div class="n">{name}{pill}</div>'
                    f'<div class="e">{email}</div></div></div>', unsafe_allow_html=True)
        if user["mode"] == "google" and st.button("Sign out", icon=":material/logout:", width="stretch"):
            st.logout()


@st.cache_resource(show_spinner="Loading captcha OCR models...")
def get_solver():
    return build_solver("ddddocr")


# ------------------------------------------------------------------ charts
def bar(labels, values, colors=None, title="", horizontal=True, fmt="{:,}", height=None):
    """Thin bars, 4px rounded ends, value labels in ink colour, hover per bar."""
    colors = colors or [SERIES] * len(labels)
    text = [fmt.format(v) for v in values]
    if horizontal:
        trace = go.Bar(y=labels, x=values, orientation="h", marker=dict(color=colors, cornerradius=4),
                       text=text, textposition="outside", cliponaxis=False,
                       hovertemplate="%{y}: %{x}<extra></extra>")
    else:
        trace = go.Bar(x=labels, y=values, marker=dict(color=colors, cornerradius=4), text=text,
                       textposition="outside", cliponaxis=False, hovertemplate="%{x}: %{y}<extra></extra>")
    fig = go.Figure(trace)
    fig.update_layout(
        title=dict(text=title, font=dict(size=15)), bargap=0.45, showlegend=False,
        height=height or max(220, 46 * len(labels) + 90), margin=dict(l=10, r=40, t=50, b=10),
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)")
    grid = dict(showgrid=True, gridcolor="rgba(137,135,129,0.25)", zeroline=False)
    if horizontal:
        fig.update_xaxes(**grid, showticklabels=False)
        fig.update_yaxes(autorange="reversed", showgrid=False)
    else:
        fig.update_yaxes(**grid, showticklabels=False)
        fig.update_xaxes(showgrid=False)
    return fig


def line(x, y, title, y_fmt):
    fig = go.Figure(go.Scatter(x=x, y=y, mode="lines+markers", line=dict(color=SERIES, width=2),
                               marker=dict(size=8), hovertemplate="%{x}<br>" + y_fmt + "<extra></extra>"))
    fig.update_layout(title=dict(text=title, font=dict(size=15)), height=300, showlegend=False,
                      margin=dict(l=10, r=10, t=50, b=10), hovermode="x unified",
                      plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)")
    fig.update_yaxes(gridcolor="rgba(137,135,129,0.25)", zeroline=False)
    fig.update_xaxes(showgrid=False)
    return fig


def counts(series, order=None):
    vc = series.value_counts()
    if order:
        vc = vc.reindex([o for o in order if o in vc.index])
    return list(vc.index), [int(v) for v in vc.values]


# ------------------------------------------------------------------ input
RUN_OPTIONS = RunOptions(workers=8, goods=True, returns=True)  # fixed: 8 measured fastest; more gets blocked


PASTE_LABEL = "GST numbers"
SEARCH_LABEL = "Search"

# Keyboard behaviour that Streamlit doesn't offer by itself:
#  * paste  -> send the box's text at once, so the GSTIN count appears straight away
#  * Enter  -> start the search (Shift+Enter still adds a new line); also works after an upload
# Uses components.html (deprecated in newer Streamlit, works in the pinned 1.66.0 - re-test on upgrade).
KEYBOARD_JS = """
<script>
const doc = window.parent.document;
function searchButton() {
  return doc.querySelector('.st-key-search_btn button');   // the Search button, by its widget key
}
function startSearch(ta) {
  if (ta) ta.blur();                       // send the latest text first
  setTimeout(() => { const b = searchButton(); if (b && !b.disabled) b.click(); }, 350);
}
function hook() {
  doc.querySelectorAll('textarea[aria-label="%(label)s"]').forEach(ta => {
    if (ta.dataset.gstHook) return;
    ta.dataset.gstHook = "1";
    ta.addEventListener("paste", () => setTimeout(() => { ta.blur(); ta.focus(); }, 80));
    ta.addEventListener("keydown", e => {
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); startSearch(ta); }
    });
  });
}
if (!doc.gstKeyHook) {
  doc.gstKeyHook = true;
  doc.addEventListener("keydown", e => {
    const tag = (e.target.tagName || "").toLowerCase();
    if (e.key === "Enter" && tag !== "textarea" && tag !== "input") startSearch(null);
  });
}
hook();
new MutationObserver(hook).observe(doc.body, {childList: true, subtree: true});
</script>
""" % {"label": PASTE_LABEL}


def _file_entries():
    """GSTINs in the uploaded file (read once per file and remembered)."""
    up = st.session_state.get(f"gst_file_{st.session_state.get('upload_round', 0)}")
    if up is None:
        return []
    cache = st.session_state.setdefault("file_cache", {})
    if up.file_id not in cache:
        suffix = os.path.splitext(up.name)[1]
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(up.getvalue())
        try:
            cache[up.file_id] = read_gstins_from_file(tmp.name)
        except Exception as exc:
            st.session_state.input_error = f"Could not read {up.name}: {exc}"
            cache[up.file_id] = []
        finally:
            os.unlink(tmp.name)
    return cache[up.file_id]


def current_entries():
    """Pasted GSTINs followed by the uploaded file's, in order, duplicates kept."""
    return extract_entries(st.session_state.get("gst_text", "")) + _file_entries()


def _start_search():
    entries = current_entries()
    if not entries:
        st.session_state.input_error = "Paste GST numbers or upload a file first."
        return
    st.session_state.pending = entries
    st.session_state.gst_text = ""                                                     # ready for the next list
    st.session_state.upload_round = st.session_state.get("upload_round", 0) + 1      # and a fresh uploader


def detected_text(entries):
    """'✓ 11 GSTINs detected (1 repeated) — press Enter to search' as plain green text."""
    unique = len(set(entries))
    invalid = sum(1 for e in set(entries) if not is_valid_gstin(e))
    extra = ([f"{len(entries) - unique} repeated"] if len(entries) > unique else []) + (
        [f"{invalid} invalid"] if invalid else [])
    detail = f" ({', '.join(extra)})" if extra else ""
    return (f'<div class="detected-txt">✓ {len(entries)} GSTIN{"s" if len(entries) != 1 else ""} detected{detail}'
            f' — press Enter to search</div>')


def input_panel():
    """Paste box and file upload; the GSTIN count shows in green, Enter (or Search) starts the run."""
    st.markdown('<div class="panel-title">Search GSTINs</div><div class="panel-sub">Paste GST numbers or upload '
                'an Excel / CSV / TXT file, then press <b>Enter</b>. Results keep the same order as your input.</div>',
                unsafe_allow_html=True)
    left, right = st.columns([3, 2], gap="medium")
    left.text_area(PASTE_LABEL, key="gst_text", height=150, label_visibility="collapsed",
                   placeholder="Paste GST numbers here – one per line, or separated by spaces / commas\n\n"
                               "03AAFPW4345P1Z7\n03BKQPG7275L1ZN")
    right.file_uploader("Excel, CSV or TXT", type=["xlsx", "xls", "csv", "txt"], label_visibility="collapsed",
                        key=f"gst_file_{st.session_state.get('upload_round', 0)}")
    # the count appears in plain green text right under the box it came from
    for col, found in ((left, extract_entries(st.session_state.get("gst_text", ""))), (right, _file_entries())):
        if found:
            col.markdown(detected_text(found), unsafe_allow_html=True)
    entries = current_entries()
    _, action = st.columns([5, 1])
    action.button(SEARCH_LABEL, type="primary", width="stretch", icon=":material/search:",
                  on_click=_start_search, disabled=not entries, key="search_btn")
    components.html(KEYBOARD_JS, height=0)
    if st.session_state.get("input_error"):
        st.warning(st.session_state.pop("input_error"))


# ------------------------------------------------------------------ run
def run(gstins, opts):
    progress = st.progress(0.0, text="Starting...")

    def on_progress(done, total, record, source):
        name = (record.get("details") or {}).get("lgnm") or record.get("error", "")
        progress.progress(done / total, text=f"{done}/{total} · {record['gstin']} · {name}")

    solver = get_solver()
    result = run_batch(gstins, opts, solver=solver, progress=on_progress)
    progress.progress(1.0, text=f"Done in {result['stats']['duration_s']:.0f}s")
    st.session_state.result = result
    st.session_state.ran_at = dt.datetime.now()


def excel_bytes(records):
    with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp:
        path = tmp.name
    try:
        write_excel(records, path, summary_only=True)  # the GST Summary sheet only
        with open(path, "rb") as f:
            return f.read()
    finally:
        os.unlink(path)


# ------------------------------------------------------------------ dashboard
def performance_tiles(stats):
    sub, wrong = stats["captcha_submitted"], stats["captcha_wrong"]
    searched = max(stats["searched"], 1)
    c = st.columns(6)
    c[0].metric("GSTINs", stats["total"])
    c[1].metric("Success rate", f"{stats['ok'] / max(stats['total'], 1):.0%}",
                help=f"{stats['ok']} OK · {stats['failed']} failed ({stats['invalid']} invalid)")
    c[2].metric("Time taken", f"{stats['duration_s']:.0f} s")
    c[3].metric("Per GSTIN", f"{stats['duration_s'] / searched:.2f} s" if stats["searched"] else "–",
                help=f"Average time per searched GSTIN · {stats['workers']} parallel workers")
    c[4].metric("Captchas", f"{(sub - wrong) / sub:.0%}" if sub else "–",
                help=f"Captcha accuracy: {sub} submitted · {wrong} rejected (each retried automatically)")
    c[5].metric("Repeated", stats.get("duplicates", 0),
                help="GSTINs listed more than once in the input – fetched once, repeated in the output at each "
                     "position")


def portfolio_tiles(k):
    c = st.columns(6)
    c[0].metric("Active", f"{k['active']}", help=f"{k['active_share']:.0%} of fetched GSTINs")
    c[1].metric("Cancelled", k["cancelled"])
    c[2].metric("Suspended", k["suspended"])
    c[3].metric("3B up to date", f"{k['up_to_date_share']:.0%}",
                help="GSTR-3B up to date: the latest due monthly return has been filed")
    c[4].metric("E-invoicing", f"{k['einvoice_share']:.0%}", help="Share with e-invoicing enabled")
    c[5].metric("Aadhaar", f"{k['aadhaar_share']:.0%}", help="Share with Aadhaar authentication")


def dashboard(result):
    stats, records = result["stats"], result["records"]
    rows = build_frame(records)                 # one row per input entry, input order (table/Excel)
    df = rows.drop_duplicates("GSTIN")          # one row per GSTIN (KPIs and charts)
    ok = df[df["Fetched"] == "OK"]

    st.subheader("Run performance")
    performance_tiles(stats)
    st.subheader("Taxpayer portfolio")
    portfolio_tiles(kpis(df))

    left, right = st.columns([3, 1])
    with right:
        st.download_button("Download Excel (GST Summary)", excel_bytes(records), width="stretch",
                           icon=":material/download:", type="primary",
                           help="The Excel file contains only the GST Summary sheet. All other details are "
                                "shown in the Full details tab.",
                           file_name=f"gst_details_{st.session_state.ran_at:%Y%m%d_%H%M%S}.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    tabs = st.tabs(["Overview", "Compliance", "Results table", "Full details", "Performance"])

    with tabs[0]:
        a, b = st.columns(2)
        labels, values = counts(df["Status Group"], ["Active", "Suspended", "Cancelled", "Other", "Unknown",
                                                     "Not fetched"])
        a.plotly_chart(bar(labels, values, [STATUS_COLORS[l] for l in labels], "GSTIN status"),
                       width="stretch")
        labels, values = counts(ok["Constitution of Business"].replace("", "Unknown"))
        b.plotly_chart(bar(labels[:8], values[:8], title="Constitution of business"), width="stretch")
        a, b = st.columns(2)
        labels, values = counts(df["State"])
        a.plotly_chart(bar(labels[:10], values[:10], title="State (from GSTIN)"), width="stretch")
        labels, values = counts(ok["Nature Of Core Business Activity"].replace("", "Unknown"))
        b.plotly_chart(bar(labels[:8], values[:8], title="Nature of core business activity"), width="stretch")

    with tabs[1]:
        labels, values = counts(ok["Filing Compliance"], COMPLIANCE_ORDER)
        st.plotly_chart(bar(labels, values, [COMPLIANCE_COLORS[l] for l in labels],
                            "GSTR-3B filing compliance (monthly filers)"), width="stretch")
        st.caption("Up to date = the latest GSTR-3B that is already due (20th of the following month) has been "
                   "filed. Quarterly (QRMP) filers may show as behind.")
        risky = ok[(ok["Status Group"] != "Active") | (ok["Filing Compliance"].isin(
            ["3+ months behind", "No GSTR-3B found"]))]
        st.markdown(f"**Needs attention: {len(risky)}** (not active, or GSTR-3B 3+ months behind / missing)")
        st.dataframe(risky[["GSTIN", "Legal Name", "Trade Name", "GSTIN Status", "Filing Compliance",
                            "Last GSTR-3B Filed", "Last GSTR-1 Filed"]], hide_index=True, width="stretch")

    with tabs[2]:
        f1, f2, f3 = st.columns(3)
        status_f = f1.multiselect("Status", sorted(df["Status Group"].unique()))
        comp_f = f2.multiselect("Filing compliance", [c for c in COMPLIANCE_ORDER if c in set(df["Filing Compliance"])])
        search = f3.text_input("Search name / GSTIN")
        view = rows.copy()
        view.insert(0, "S.No", range(1, len(view) + 1))  # input serial number
        if status_f:
            view = view[view["Status Group"].isin(status_f)]
        if comp_f:
            view = view[view["Filing Compliance"].isin(comp_f)]
        if search:
            s = search.lower()
            view = view[view["GSTIN"].str.lower().str.contains(s) | view["Legal Name"].str.lower().str.contains(s)
                        | view["Trade Name"].fillna("").str.lower().str.contains(s)]
        st.caption(f"{len(view)} of {len(rows)} rows (same order as the input)")
        st.dataframe(view, hide_index=True, width="stretch", height=520)

    with tabs[3]:
        full_details(records)

    with tabs[4]:
        t = stats.get("timings") or {}
        if t:
            order = ["captcha download", "captcha OCR", "captcha fetch+read", "waiting for prefetched captcha",
                     "search request", "goods request", "returns request", "total per GSTIN"]
            labels = [k for k in order if k in t] + [k for k in t if k not in order]
            st.plotly_chart(bar(labels, [t[k] for k in labels], title="Average seconds per step",
                                fmt="{:.2f}s"), width="stretch")
            st.caption("Goods/returns run in parallel with the captcha search, and the next captcha is prepared "
                       "in the background, so steps overlap – they don't add up to the total.")
        else:
            st.info("No GSTINs were searched in this run (all entries were invalid).")
        c = st.columns(4)
        c[0].metric("Captcha images fetched", stats["captcha_fetched"])
        c[1].metric("Captchas submitted", stats["captcha_submitted"])
        c[2].metric("Rejected (auto-retried)", stats["captcha_wrong"])
        c[3].metric("GSTINs retried", stats["retried"])


def full_details(records):
    """Everything the portal returned beyond the GST Summary - shown in the app only, never in the Excel."""
    fetched = list({r["gstin"]: r for r in records if r.get("ok")}.values())  # unique, input order
    if not fetched:
        st.info("No GSTIN was fetched successfully in this run.")
        return
    st.caption("Shown here only – the Excel download contains just the GST Summary sheet.")
    one, all_ = st.tabs(["One GSTIN", "All GSTINs"])

    with one:
        labels = {r["gstin"]: f"{r['gstin']} – {r['details'].get('lgnm', '')}" for r in fetched}
        gstin = st.selectbox("GSTIN", list(labels), format_func=labels.get)
        rec = next(r for r in fetched if r["gstin"] == gstin)
        d = rec["details"]
        info = {k: v for k, v in flatten_details(d).items() if str(v).strip() not in ("", "NA", "0")}
        if "Nature of Core Business Activity" in info:
            info["Nature of Core Business Activity"] = core_activity(d.get("ntcrbs"))
        left, right = st.columns([1, 1], gap="large")
        with left:
            st.markdown("**Taxpayer details**")
            st.dataframe(pd.DataFrame({"Field": list(info), "Value": [str(v) for v in info.values()]}),
                         hide_index=True, width="stretch", height=min(38 * len(info) + 40, 640))
        with right:
            goods = detail_records(gstin, rec.get("goods") or {})
            st.markdown(f"**Goods & services ({len(goods)})**")
            if goods:
                st.dataframe(pd.DataFrame(goods).drop(columns=["GSTIN"]), hide_index=True, width="stretch")
            else:
                st.caption("None reported.")
            places = additional_places(d)
            st.markdown(f"**Additional places of business ({len(places)})**")
            if places:
                st.dataframe(pd.DataFrame(places).drop(columns=["GSTIN", "Legal Name"]), hide_index=True,
                             width="stretch")
            else:
                st.caption("None.")
        rets = [row for fy, payload in (rec.get("returns") or {}).items()
                for row in detail_records(gstin, payload)]
        st.markdown(f"**Return filing history ({len(rets)})**")
        if rets:
            rdf = pd.DataFrame(rets).drop(columns=["GSTIN", "Section"], errors="ignore")
            types = sorted(rdf["Return Type"].dropna().unique()) if "Return Type" in rdf else []
            pick = st.multiselect("Return type", types, default=types, key=f"rt_{gstin}")
            if types:
                rdf = rdf[rdf["Return Type"].isin(pick)]
            st.dataframe(rdf, hide_index=True, width="stretch", height=360)
        else:
            st.caption("No return filing data.")

    with all_:
        goods_all = [row for r in fetched for row in detail_records(r["gstin"], r.get("goods") or {})]
        rets_all = [row for r in fetched for payload in (r.get("returns") or {}).values()
                    for row in detail_records(r["gstin"], payload)]
        places_all = [row for r in fetched for row in additional_places(r["details"])]
        details_all = [flatten_details(r["details"]) for r in fetched]
        for title, data, height in [("Taxpayer details", details_all, 320), ("Goods & services", goods_all, 320),
                                    ("Return filing history", rets_all, 420),
                                    ("Additional places of business", places_all, 260)]:
            with st.expander(f"{title} ({len(data)} rows)", expanded=title == "Taxpayer details"):
                if data:
                    st.dataframe(pd.DataFrame(data), hide_index=True, width="stretch", height=height)
                else:
                    st.caption("None.")


def history_tab():
    hist = load_history()
    if not hist:
        st.info("No runs recorded yet.")
        return
    h = pd.DataFrame(hist)
    h["Run"] = pd.to_datetime(h["started_at"]).dt.strftime("%d %b %H:%M")
    h["Sec / searched GSTIN"] = (h["duration_s"] / h["searched"].where(h["searched"] > 0)).round(2)
    h["Captcha accuracy"] = ((h["captcha_submitted"] - h["captcha_wrong"])
                             / h["captcha_submitted"].where(h["captcha_submitted"] > 0))
    c = st.columns(4)
    c[0].metric("Runs", len(h))
    c[1].metric("GSTINs processed", int(h["total"].sum()))
    c[2].metric("Overall success", f"{h['ok'].sum() / max(h['total'].sum(), 1):.1%}")
    sub = h["captcha_submitted"].sum()
    c[3].metric("Overall captcha accuracy", f"{(sub - h['captcha_wrong'].sum()) / sub:.1%}" if sub else "–")
    searched = h[h["searched"] > 0].tail(30)
    if len(searched) >= 2:
        a, b = st.columns(2)
        a.plotly_chart(line(searched["Run"], searched["Sec / searched GSTIN"], "Seconds per searched GSTIN",
                            "%{y:.2f}s"), width="stretch")
        b.plotly_chart(line(searched["Run"], searched["Captcha accuracy"] * 100, "Captcha accuracy (%)",
                            "%{y:.0f}%"), width="stretch")
    show = h[["Run", "total", "ok", "failed", "searched", "workers", "duration_s",
              "Sec / searched GSTIN", "captcha_submitted", "captcha_wrong"]].iloc[::-1]
    st.dataframe(show.rename(columns={"total": "GSTINs", "ok": "OK", "failed": "Failed",
                                      "searched": "Searched", "workers": "Workers", "duration_s": "Seconds",
                                      "captcha_submitted": "Captchas", "captcha_wrong": "Rejected"}),
                 hide_index=True, width="stretch")


# ------------------------------------------------------------------ pages
WHAT_YOU_GET = [
    ("badge", "Legal & trade name", "Legal name of business and the trade name it operates under."),
    ("verified", "Status with dates", "Active, suspended or cancelled – with the effective date of the change."),
    ("account_balance", "Jurisdiction", "Administrative and other office exactly as the portal shows them."),
    ("receipt_long", "Returns", "Latest GSTR-1 and GSTR-3B period with filing dates, plus full history."),
    ("category", "HSN & activity", "HSN codes, nature of core business activity and constitution."),
]


def what_you_get():
    """The data each search returns – the columns of the GST Summary."""
    tiles = "".join(f'<div class="feat"><span class="material-symbols-rounded">{icon}</span><div><b>{title}</b>'
                    f'<p>{text}</p></div></div>' for icon, title, text in WHAT_YOU_GET)
    st.markdown('<div class="about"><div class="about-head"><div class="badge">What you get</div>'
                '<h2>From a list of GSTINs to a verified summary in seconds</h2>'
                '<p>Public taxpayer data from the GST portal (services.gst.gov.in), fetched and organised for each '
                f'GSTIN.</p></div><div class="feats">{tiles}</div></div>', unsafe_allow_html=True)


def feedback_and_contact():
    """Two small buttons at the bottom; each opens its content in a pop-up."""
    st.markdown('<div class="section-gap"></div>', unsafe_allow_html=True)
    _, fb_col, ct_col, _ = st.columns([3, 1, 1, 3], gap="small")
    with fb_col.popover("Feedback", icon=":material/rate_review:", width="stretch"):
        st.markdown("**Feedback** – tell us what works and what should be better.")
        with st.form("feedback", clear_on_submit=True, border=False):
            rating = st.feedback("stars", key="fb_rating")
            message = st.text_area("Your feedback", label_visibility="collapsed", height=100,
                                   placeholder="Your suggestion, a problem you noticed, or a feature you would like…")
            if st.form_submit_button("Send feedback", type="primary", icon=":material/send:"):
                if message.strip() or rating is not None:
                    save_feedback(st.session_state.user, None if rating is None else rating + 1, message)
                    st.success("Thank you – your feedback has been sent.")
                else:
                    st.warning("Please add a rating or a message.")
    with ct_col.popover("Contact", icon=":material/support_agent:", width="stretch"):
        c = contact_details()
        rows = []
        if c.get("name"):
            rows.append(("person", html.escape(c["name"])))
        if c.get("email"):
            e = html.escape(c["email"])
            rows.append(("mail", f'<a href="mailto:{e}">{e}</a>'))
        if c.get("phone"):
            p = html.escape(c["phone"])
            rows.append(("call", f'<a href="tel:{p.replace(" ", "")}">{p}</a>'))
        rows.append(("schedule", html.escape(c.get("hours", "Mon – Sat, 10:00 – 18:00 IST"))))
        items = "".join(f'<div class="contact-row"><span class="material-symbols-rounded">{icon}</span>'
                        f'<span>{text}</span></div>' for icon, text in rows)
        st.markdown(f"**Contact** – questions, access or bulk needs.{items}", unsafe_allow_html=True)
        if not (c.get("email") or c.get("phone")) and is_admin(st.session_state.user):
            st.caption("Admin: add your email / phone in Secrets → [contact] to show them here.")
    st.markdown('<div class="footer-note">Public taxpayer information from services.gst.gov.in · '
                f'© {dt.date.today().year} GST Taxpayer Search</div>', unsafe_allow_html=True)


def search_page():
    input_panel()
    if "pending" in st.session_state:
        run(st.session_state.pop("pending"), RUN_OPTIONS)
    if "result" in st.session_state:
        dashboard(st.session_state.result)
    what_you_get()
    feedback_and_contact()


def history_page():
    hero("Run history", "Speed, success rate and captcha accuracy of runs on this server.")
    history_tab()


def admin_page():
    hero("Admin", "Approve or revoke access for people who signed in with Google.", badge="Administration")
    rows = access_table()
    pending = [r for r in rows if r["Status"] == "Pending"]
    c = st.columns(3)
    c[0].metric("Pending requests", len(pending))
    c[1].metric("Approved users", sum(r["Status"].startswith("Approved") for r in rows))
    c[2].metric("Admins", sum(r["Status"] == "Admin" for r in rows))
    if rows:
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    else:
        st.info("Nobody has requested access yet. New users appear here after their first Google sign-in.")

    editable = [r["Email"] for r in rows if r["Status"] != "Admin"]
    if editable:
        st.subheader("Change access")
        default = editable.index(pending[0]["Email"]) if pending else 0
        a, b, d = st.columns([3, 1, 1], vertical_alignment="bottom")
        email = a.selectbox("User", editable, index=default)
        if b.button("Approve", type="primary", width="stretch", icon=":material/check:"):
            set_access(email, True)
            st.toast(f"Approved {email}")
            st.rerun()
        if d.button("Revoke", width="stretch", icon=":material/block:"):
            set_access(email, False)
            st.toast(f"Revoked {email}")
            st.rerun()

    st.subheader("Feedback")
    fb = load_feedback()
    if fb:
        st.dataframe(pd.DataFrame(fb).rename(columns={"at": "Received", "email": "Email", "name": "Name",
                                                      "rating": "Stars", "message": "Message"}),
                     hide_index=True, width="stretch")
    else:
        st.caption("No feedback yet.")

    st.subheader("Make approvals permanent")
    st.markdown("Approvals made here are kept on the app server, which Streamlit Cloud resets whenever the app "
                "restarts or is redeployed. To make them permanent, open **Manage app → ⋮ → Settings → Secrets** "
                "and replace the `[access]` section with:")
    st.code(secrets_snippet(), language="toml")
    if not auth_configured():
        st.info("Google sign-in is not configured, so the app is running in local mode (no login). "
                "See the README section *Google sign-in* to switch it on.")


def main():
    st.set_page_config(page_title="GST Taxpayer Search", page_icon="🧾", layout="wide",
                       initial_sidebar_state="collapsed")
    inject_css()
    user = current_user()
    if user is None:
        login_page()
        return
    admin = is_admin(user)
    if not is_approved(user):
        record_request(user)
        pending_page(user)
        return
    pages = [st.Page(search_page, title="Search", icon=":material/search:", url_path="search", default=True)]
    if admin:  # Run history and Admin exist only for the admin
        pages += [st.Page(history_page, title="Run history", icon=":material/history:", url_path="history"),
                  st.Page(admin_page, title="Admin", icon=":material/admin_panel_settings:", url_path="admin")]
    st.session_state.user = user
    nav = st.navigation(pages, position="top" if admin else "hidden")
    top_bar(user, admin)
    nav.run()


main()
