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

try:
    try:  # project layout: gst_search/ package next to app.py (local runs, git push)
        from gst_analytics import COMPLIANCE_ORDER, build_frame, kpis
        from gst_auth import (access_table, auth_configured, current_user, is_admin, is_approved,
                                     record_request, secrets_snippet, set_access)
        from gst_captcha import build_solver
        from gst_gstin import extract_entries
        from gst_io_utils import read_gstins_from_file, write_excel
        from gst_parser import additional_places, flatten_details, records as detail_records
        from gst_runner import RunOptions, load_history, run_batch
        from gst_summary import core_activity
    except ModuleNotFoundError as exc:  # flat layout: gst_*.py files next to app.py (GitHub web upload)
        if exc.name not in ("gst_search", "gst_search.analytics"):
            raise
        from gst_analytics import COMPLIANCE_ORDER, build_frame, kpis
        from gst_auth import (access_table, auth_configured, current_user, is_admin, is_approved,
                              record_request, secrets_snippet, set_access)
        from gst_captcha import build_solver
        from gst_gstin import extract_entries
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
.block-container { padding: 3.6rem 2.6rem 7rem 2.6rem; max-width: 100%; }
/* the GST number box pinned at the bottom */
[data-testid="stChatInput"] { border: 2px solid rgba(42,120,214,.45) !important; border-radius: 16px !important;
                              box-shadow: 0 8px 28px rgba(16,66,129,.16); }
[data-testid="stChatInput"]:focus-within { border-color: #2a78d6 !important; }
[data-testid="stChatInputSubmitButton"] { background: #2a78d6 !important; color: #fff !important; border-radius: 10px !important; }
.brand { font-weight: 700; font-size: 1.05rem; display: flex; align-items: center; gap: 8px; padding-top: 6px; }
.brand .dot { width: 30px; height: 30px; border-radius: 9px; display: inline-flex; align-items: center;
              justify-content: center; background: linear-gradient(135deg, #104281, #2a78d6); color: #fff;
              font-size: .95rem; }
.step { border: 1px solid rgba(137,135,129,.25); border-radius: 18px; padding: 22px 24px; height: 100%;
        background: rgba(137,135,129,.04); }
.step .num { width: 34px; height: 34px; border-radius: 10px; background: rgba(42,120,214,.12); color: #2a78d6;
             font-weight: 700; display: flex; align-items: center; justify-content: center; margin-bottom: 12px; }
.step b { display: block; font-size: 1.02rem; margin-bottom: 4px; }
.step span { opacity: .72; font-size: .92rem; line-height: 1.5; }
.hint { text-align: center; opacity: .6; font-size: .85rem; margin-top: 18px; }
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
        st.markdown('<div class="card"><h3>Sign in</h3><p class="muted">Use your Google account. '
                    'First time here? Signing in creates your account and sends an access request '
                    'to the admin.</p></div>', unsafe_allow_html=True)
        st.write("")
        if st.button("Continue with Google", type="primary", width="stretch", icon=":material/login:"):
            st.login()
        st.caption("We only receive your name, email and profile photo from Google.")


def pending_page(user):
    hero("Access requested", "Your account is waiting for the admin's approval.", badge="Almost there")
    st.markdown(f'<div class="card"><h3>Hi {html.escape(user["name"] or "")} 👋</h3>'
                f'<p>We have recorded an access request for <b>{html.escape(user["email"])}</b>. '
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


def entries_from_submission(value):
    """GST numbers from the bottom input box: pasted text and/or attached Excel/CSV/TXT files,
    in the order given (duplicates kept so the output follows the input row for row)."""
    if value is None:
        return []
    text = value if isinstance(value, str) else (value.text or "")
    files = [] if isinstance(value, str) else (value.files or [])
    entries = extract_entries(text)
    for up in files:
        suffix = os.path.splitext(up.name)[1]
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(up.getvalue())
        try:
            entries += read_gstins_from_file(tmp.name)
        except Exception as exc:
            st.error(f"Could not read {up.name}: {exc}")
        finally:
            os.unlink(tmp.name)
    return entries


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
def search_page():
    hero("Taxpayer search", "Paste GST numbers in the box at the bottom and press Enter – or attach an Excel, "
         "CSV or TXT file. Results keep the same order as your input.")
    submitted = st.chat_input("Paste GST numbers and press Enter  ·  📎 attach Excel / CSV / TXT",
                              accept_file="multiple", file_type=["xlsx", "xls", "csv", "txt"])
    if submitted is not None:
        entries = entries_from_submission(submitted)
        if entries:
            run(entries, RUN_OPTIONS)
        else:
            st.toast("No GST numbers found in that input.", icon=":material/error:")
    if "result" in st.session_state:
        dashboard(st.session_state.result)
    else:
        cols = st.columns(3, gap="medium")
        for col, (num, title, text) in zip(cols, [
            ("1", "Paste or attach", "Paste GST numbers (one per line, or separated by spaces/commas) in the box "
                                     "below, or attach an Excel / CSV / TXT file."),
            ("2", "Press Enter", "The search starts at once: captchas are solved automatically and 8 GSTINs "
                                 "are searched in parallel."),
            ("3", "Review & download", "KPIs, compliance and full details on screen; the Excel download is the "
                                       "GST Summary in your format."),
        ]):
            col.markdown(f'<div class="step"><div class="num">{num}</div><b>{title}</b><span>{text}</span></div>',
                         unsafe_allow_html=True)
        st.markdown('<div class="hint">Shift + Enter adds a new line · repeated and invalid entries keep their '
                    'place in the output</div>', unsafe_allow_html=True)


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
    nav = st.navigation(pages, position="top" if admin else "hidden")
    top_bar(user, admin)
    nav.run()


main()
