"""Dashboard KPIs computed from fetched records (used by app.py)."""

import datetime as dt

import pandas as pd

import gst_parser as parser
from gst_summary import COLUMNS, summary_row

STATE_CODES = {
    "01": "Jammu & Kashmir", "02": "Himachal Pradesh", "03": "Punjab", "04": "Chandigarh",
    "05": "Uttarakhand", "06": "Haryana", "07": "Delhi", "08": "Rajasthan", "09": "Uttar Pradesh",
    "10": "Bihar", "11": "Sikkim", "12": "Arunachal Pradesh", "13": "Nagaland", "14": "Manipur",
    "15": "Mizoram", "16": "Tripura", "17": "Meghalaya", "18": "Assam", "19": "West Bengal",
    "20": "Jharkhand", "21": "Odisha", "22": "Chhattisgarh", "23": "Madhya Pradesh", "24": "Gujarat",
    "26": "Dadra & Nagar Haveli and Daman & Diu", "27": "Maharashtra", "29": "Karnataka", "30": "Goa",
    "31": "Lakshadweep", "32": "Kerala", "33": "Tamil Nadu", "34": "Puducherry",
    "35": "Andaman & Nicobar", "36": "Telangana", "37": "Andhra Pradesh", "38": "Ladakh",
    "97": "Other Territory", "99": "Centre Jurisdiction",
}

_MONTHS = ["January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December"]

COMPLIANCE_ORDER = ["Up to date", "1–2 months behind", "3+ months behind", "No GSTR-3B found"]


def status_group(status):
    s = str(status or "").lower()
    if s.startswith("active"):
        return "Active"
    if "cancel" in s:
        return "Cancelled"
    if "suspend" in s:
        return "Suspended"
    return "Other" if s else "Unknown"


def _period_start(fy, taxp):
    taxp = str(taxp or "").strip()
    years = str(fy or "").split("-")
    if taxp in _MONTHS and len(years) == 2 and years[0].isdigit():
        m = _MONTHS.index(taxp) + 1
        return dt.date(int(years[0]) + (1 if m <= 3 else 0), m, 1)
    return None


def latest_period(returns, rtntype):
    """Most recent filed tax period (date of its first day) for a return type, or None."""
    best = None
    for payload in (returns or {}).values():
        for _, rec in parser._iter_records(payload):
            if str(rec.get("rtntype", "")).upper() != rtntype:
                continue
            if str(rec.get("status", "")).strip().lower() not in ("filed", ""):
                continue
            start = _period_start(rec.get("fy"), rec.get("taxp"))
            if start and (best is None or start > best):
                best = start
    return best


def latest_due_period(today=None):
    """GSTR-3B for month M is due on the 20th of M+1: the latest period that should be filed."""
    today = today or dt.date.today()
    back = 1 if today.day > 20 else 2
    y, m = today.year, today.month - back
    while m <= 0:
        m += 12
        y -= 1
    return dt.date(y, m, 1)


def months_between(a, b):
    return (b.year - a.year) * 12 + (b.month - a.month)


def compliance_bucket(last_3b, due):
    if last_3b is None:
        return "No GSTR-3B found", None
    behind = max(0, months_between(last_3b, due))
    if behind == 0:
        return "Up to date", 0
    return ("1–2 months behind" if behind <= 2 else "3+ months behind"), behind


def build_frame(records, today=None):
    """One row per GSTIN: the user's summary columns + analysis fields."""
    due = latest_due_period(today)
    rows = []
    for rec in records:
        row = summary_row(rec)
        g = rec["gstin"]
        row["State"] = STATE_CODES.get(g[:2], f"Code {g[:2]}")
        row["Fetched"] = "OK" if rec.get("ok") else "Failed"
        row["Error"] = "" if rec.get("ok") else rec.get("error", "")
        if rec.get("ok"):
            d = rec["details"]
            row["Status Group"] = status_group(d.get("sts"))
            row["Nature of Business"] = parser._text(d.get("nba"))
            row["E-Invoicing"] = d.get("einvoiceStatus") or ""
            last_3b = latest_period(rec.get("returns"), "GSTR3B")
            row["Filing Compliance"], row["Months Behind (3B)"] = compliance_bucket(last_3b, due)
            row["HSN Count"] = len([x for x in (row.get("HSN") or "").split(",") if x.strip()])
        else:
            row.update({"Status Group": "Not fetched", "Nature of Business": "", "E-Invoicing": "",
                        "Filing Compliance": "",
                        "Months Behind (3B)": None, "HSN Count": 0})
        rows.append(row)
    cols = COLUMNS + ["State", "Status Group", "Nature of Business", "Filing Compliance", "Months Behind (3B)",
                      "E-Invoicing", "HSN Count", "Fetched", "Error"]
    return pd.DataFrame(rows, columns=cols)


def kpis(df):
    ok = df[df["Fetched"] == "OK"]
    n = len(ok)

    def share(mask):
        return (mask.sum() / n) if n else 0.0

    return {
        "fetched": n,
        "active": int((ok["Status Group"] == "Active").sum()),
        "cancelled": int((ok["Status Group"] == "Cancelled").sum()),
        "suspended": int((ok["Status Group"] == "Suspended").sum()),
        "active_share": share(ok["Status Group"] == "Active"),
        "up_to_date_share": share(ok["Filing Compliance"] == "Up to date"),
        "behind_3plus": int((ok["Filing Compliance"] == "3+ months behind").sum()),
        "einvoice_share": share(ok["E-Invoicing"].str.lower() == "yes"),
        "aadhaar_share": share(ok["Aadhaar Authenticated"].astype(str).str.lower().str.startswith("yes")),
    }
