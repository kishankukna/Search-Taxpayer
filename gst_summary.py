"""One-row-per-GSTIN summary sheet in the user's standard column layout."""

import datetime as dt
import re

import gst_parser as parser

COLUMNS = [
    "GSTIN", "Legal Name", "Trade Name", "Date of Registration", "Administrative Office",
    "GSTIN Status", "Nature Of Core Business Activity", "Constitution of Business", "Other Office",
    "Taxpayer Type", "PPOB", "Aadhaar Authenticated", "HSN",
    "Last GSTR-1 Filed", "Last GSTR-3B Filed",
]

WIDTHS = {
    "GSTIN": 18, "Legal Name": 30, "Trade Name": 30, "Date of Registration": 14,
    "Administrative Office": 40, "GSTIN Status": 13, "Nature Of Core Business Activity": 22,
    "Constitution of Business": 18, "Other Office": 40, "Taxpayer Type": 14, "PPOB": 45,
    "Aadhaar Authenticated": 14, "HSN": 45, "Last GSTR-1 Filed": 24, "Last GSTR-3B Filed": 24,
}

# Portal codes for "Nature Of Core Business Activity" (field ntcrbs), e.g. "TRD:TWD".
CORE_ACTIVITY = {
    "MFT": "Manufacturer",
    "SPO": "Service Provider and Others",
    "TRD": "Trader",
    "TWD": "Wholesaler/Distributor",
    "TRR": "Retailer",
}


def core_activity(code):
    """'TRD:TWD' -> 'Trader - Wholesaler/Distributor'; unknown codes are shown as they are."""
    code = str(code or "").strip()
    if not code or code.upper() == "NA":
        return code
    parts = [CORE_ACTIVITY.get(p.strip().upper()) for p in code.split(":")]
    return " - ".join(parts) if all(parts) else code

_MONTHS = ["January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December"]


def _date(text):
    try:
        return dt.datetime.strptime(str(text).strip(), "%d/%m/%Y")
    except ValueError:
        return None


def _period(fy, taxp):
    """('2026-2027', 'September') -> 'Sep-2026'; Jan-Mar fall in the second year of the FY."""
    taxp = str(taxp or "").strip()
    years = str(fy or "").split("-")
    if taxp in _MONTHS and len(years) == 2 and years[0].isdigit():
        m = _MONTHS.index(taxp) + 1
        year = int(years[0]) + (1 if m <= 3 else 0)
        return f"{taxp[:3]}-{year}"
    return f"{taxp} {fy}".strip()


def last_filed(returns, rtntype):
    """Latest filed return of a type, as 'Oct-2025' + new line + '(Filed on 04/11/2025)'."""
    best = None
    for payload in (returns or {}).values():
        for _, rec in parser._iter_records(payload):
            if str(rec.get("rtntype", "")).upper() != rtntype:
                continue
            if str(rec.get("status", "")).strip().lower() not in ("filed", ""):
                continue
            filed_on = _date(rec.get("dof"))
            if filed_on and (best is None or filed_on > best[0]):
                best = (filed_on, rec)
    if not best:
        return "Not found"
    rec = best[1]
    return f"{_period(rec.get('fy'), rec.get('taxp'))}\n(Filed on {rec.get('dof')})"


def hsn_list(goods):
    """HSN/SAC codes only (no descriptions), separated by commas: '3004, 300410, 300420'."""
    codes = []
    for _, rec in parser._iter_records(goods or {}):
        code = str(rec.get("hsncd") or rec.get("saccd") or "").strip()
        if code and code not in codes:
            codes.append(code)
    return ", ".join(codes)


JURISDICTION_MARK = "(Jurisdictional Office)"
# split "State - CBIC,Zone - CHANDIGARH,..." at commas that start a new "Key - value" part, so a
# comma inside a value ("Zone - South Zone, Parwanoo") is kept
_PART_SPLIT = re.compile(r",\s*(?=[A-Za-z][A-Za-z ]*\s-\s)")


def _jurisdiction_text(raw, kind):
    parts = [p.strip() for p in _PART_SPLIT.split(str(raw or "").replace(JURISDICTION_MARK, "")) if p.strip()]
    return "\n".join([f"(JURISDICTION - {kind})"] + parts) if parts else ""


def offices(d):
    """(administrative office, other office), as the portal shows them. The portal marks the
    administrative jurisdiction with '(Jurisdictional Office)' - either the state (stj) or the
    centre (ctj) one - and the other is shown as 'Other Office'."""
    state, centre = d.get("stj") or "", d.get("ctj") or ""
    state_txt, centre_txt = _jurisdiction_text(state, "STATE"), _jurisdiction_text(centre, "CENTER")
    if JURISDICTION_MARK in centre and JURISDICTION_MARK not in state:
        return centre_txt, state_txt
    return state_txt, centre_txt


def status_text(d):
    """'Active', or e.g. 'Cancelled suo-moto (Effective from 01/11/2025)' / 'Suspended (Effective from
    09/09/2026)'. The portal keeps the cancellation/suspension effective date in cxdt."""
    status, since = str(d.get("sts") or "").strip(), str(d.get("cxdt") or "").strip()
    if since and not status.lower().startswith("active"):
        return f"{status} (Effective from {since})"
    return status


def aadhaar_text(d):
    """'Yes (On 19/06/2023)', 'Yes' or 'No'."""
    flag, on = str(d.get("adhrVFlag") or "").strip(), str(d.get("adhrVdt") or "").strip()
    return f"{flag} (On {on})" if flag.lower() == "yes" and on else flag


def summary_row(rec):
    if not rec.get("ok"):
        return {"GSTIN": rec["gstin"], "Legal Name": f"ERROR: {rec.get('error')}"}
    d = rec["details"]
    ppob, _, _, _ = parser._place(d.get("pradr"))
    admin_office, other_office = offices(d)
    return {
        "GSTIN": d.get("gstin") or rec["gstin"],
        "Legal Name": d.get("lgnm"),
        "Trade Name": d.get("tradeNam"),
        "Date of Registration": d.get("rgdt"),
        "Administrative Office": admin_office,
        "GSTIN Status": status_text(d),
        "Nature Of Core Business Activity": core_activity(d.get("ntcrbs")),
        "Constitution of Business": d.get("ctb"),
        "Other Office": other_office,
        "Taxpayer Type": d.get("dty"),
        "PPOB": ppob,
        "Aadhaar Authenticated": aadhaar_text(d),
        "HSN": hsn_list(rec.get("goods")),
        "Last GSTR-1 Filed": last_filed(rec.get("returns"), "GSTR1"),
        "Last GSTR-3B Filed": last_filed(rec.get("returns"), "GSTR3B"),
    }


def write_summary_sheet(wb, records, title="GST Summary"):
    """Add the summary sheet to an xlsxwriter workbook: one row per input entry in input order,
    bold header, every cell centred and wrapped, thin borders, no frozen panes or filters.
    Call it before adding other sheets so it is the first tab."""
    ws = wb.add_worksheet(title)
    head = wb.add_format({"bold": True, "border": 1, "align": "center", "valign": "vcenter",
                          "text_wrap": True})
    body = wb.add_format({"border": 1, "align": "center", "valign": "vcenter", "text_wrap": True})
    ws.write_row(0, 0, COLUMNS, head)
    for r, rec in enumerate(records, 1):
        row = summary_row(rec)
        ws.write_row(r, 0, [str(row.get(c) or "") for c in COLUMNS], body)
    for i, col in enumerate(COLUMNS):
        ws.set_column(i, i, WIDTHS.get(col, 15))
    ws.set_row(0, 32)
    return ws
