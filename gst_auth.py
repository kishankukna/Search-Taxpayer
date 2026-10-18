"""Who is using the dashboard, and are they allowed to fetch data?

Sign-in is Streamlit's built-in Google login (st.login). Signing in for the first time creates an
access request; an admin approves it on the Admin page.

Configuration lives in Streamlit Secrets (survives restarts):

    [auth]                      # Google OAuth - see README
    redirect_uri = "https://<your-app>.streamlit.app/oauth2callback"
    cookie_secret = "<long random string>"
    client_id = "<...>.apps.googleusercontent.com"
    client_secret = "<...>"
    server_metadata_url = "https://accounts.google.com/.well-known/openid-configuration"

    [access]
    admins = ["you@gmail.com"]           # can use the app and approve others
    approved = ["colleague@gmail.com"]   # permanent approvals

Approvals clicked on the Admin page take effect immediately but are kept on the server's disk,
which Streamlit Cloud wipes on restart/redeploy; the Admin page shows the `approved = [...]` line
to paste into Secrets to make them permanent.

Without an [auth] section (e.g. running on your own PC) the app runs in local mode: no login,
the local user is an admin. GST_DEV_USER=<email> simulates a signed-in user (for testing only).
"""

import datetime as dt
import json
import os
import threading

import streamlit as st

from gst_runner import OUT_DIR

ACCESS_PATH = os.path.join(OUT_DIR, "access.json")
_lock = threading.Lock()


def _secrets_section(name):
    try:
        return st.secrets.get(name) or {}
    except Exception:  # no secrets.toml at all
        return {}


def auth_configured():
    return bool(_secrets_section("auth").get("client_id"))


def _secret_emails(key):
    return {str(e).strip().lower() for e in _secrets_section("access").get(key, []) if str(e).strip()}


def current_user():
    """Signed-in user as a dict, or None if a login is required and nobody is signed in."""
    dev = os.environ.get("GST_DEV_USER")
    if dev:
        return {"email": dev.lower(), "name": dev.split("@")[0].title(), "picture": None, "mode": "dev"}
    if not auth_configured():
        return {"email": "local", "name": "Local user", "picture": None, "mode": "local"}
    if not st.user.get("is_logged_in", False):
        return None
    return {"email": str(st.user.get("email", "")).lower(), "name": st.user.get("name") or st.user.get("email"),
            "picture": st.user.get("picture"), "mode": "google"}


# ------------------------------------------------------------------ access list on disk
def _load():
    if not os.path.exists(ACCESS_PATH):
        return {"requests": {}, "approved": [], "revoked": []}
    with open(ACCESS_PATH, encoding="utf-8") as f:
        data = json.load(f)
    data.setdefault("requests", {})
    data.setdefault("approved", [])
    data.setdefault("revoked", [])
    return data


def _save(data):
    os.makedirs(OUT_DIR, exist_ok=True)
    tmp = ACCESS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, ACCESS_PATH)


def is_admin(user):
    return user["mode"] in ("local",) or user["email"] in _secret_emails("admins") or (
        user["mode"] == "dev" and os.environ.get("GST_DEV_ADMIN") == "1")


def is_approved(user):
    if is_admin(user):
        return True
    email = user["email"]
    data = _load()
    if email in data["revoked"]:
        return False
    return email in _secret_emails("approved") or email in data["approved"]


def record_request(user):
    """Remember that this user asked for access (first sign-in)."""
    with _lock:
        data = _load()
        req = data["requests"].get(user["email"])
        now = dt.datetime.now().isoformat(timespec="seconds")
        if req is None:
            data["requests"][user["email"]] = {"name": user["name"], "requested_at": now, "last_seen": now}
        else:
            req["last_seen"] = now
        _save(data)


def set_access(email, approved):
    email = email.strip().lower()
    with _lock:
        data = _load()
        if approved:
            data["approved"] = sorted(set(data["approved"]) | {email})
            data["revoked"] = [e for e in data["revoked"] if e != email]
        else:
            data["approved"] = [e for e in data["approved"] if e != email]
            data["revoked"] = sorted(set(data["revoked"]) | {email})
        _save(data)


def access_table():
    """Rows for the Admin page: everyone who requested access or is approved anywhere."""
    data = _load()
    admins, secret_ok = _secret_emails("admins"), _secret_emails("approved")
    emails = set(data["requests"]) | set(data["approved"]) | secret_ok | admins
    rows = []
    for email in sorted(emails):
        req = data["requests"].get(email, {})
        if email in admins:
            status = "Admin"
        elif email in data["revoked"]:
            status = "Revoked"
        elif email in secret_ok:
            status = "Approved (permanent)"
        elif email in data["approved"]:
            status = "Approved (until restart)"
        else:
            status = "Pending"
        rows.append({"Email": email, "Name": req.get("name", ""), "Status": status,
                     "Requested": req.get("requested_at", ""), "Last seen": req.get("last_seen", "")})
    return rows


def secrets_snippet():
    """The `approved = [...]` line that makes current approvals permanent."""
    data = _load()
    emails = sorted((_secret_emails("approved") | set(data["approved"])) - set(data["revoked"]))
    return "[access]\nadmins = " + json.dumps(sorted(_secret_emails("admins"))) + "\napproved = " + json.dumps(emails)
