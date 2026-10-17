"""One-row-per-GSTIN summary sheet in the user's standard column layout."""

import datetime as dt

import gst_parser as parser

COLUMNS = [
    "GSTIN", "Legal Name", "Trade Name", "Date of Registration", "GSTIN Status",
    "Nature of Business", "Constitution", "Administrative Office", "Other Office",
    "Taxpayer Type", "PPoB", "Aadhaar Authenticated", "HSN",
    "Last GSTR-1 Filed", "Last GSTR-3B Filed",
]

WIDTHS = {
    "GSTIN": 18, "Legal Name": 30, "Trade Name": 30, "Date of Registration": 14,
    "GSTIN Status": 13, "Nature of Business": 30, "Constitution": 18,
    "Administrative Office": 45, "Other Office": 45, "Taxpayer Type": 14, "PPoB": 50,
    "Aadhaar Authenticated": 14, "HSN": 45, "Last GSTR-1 Filed": 26, "Last GSTR-3B Filed": 26,
}

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
    """Latest filed return of a type, as 'Sep-2026 (Filed 11/10/2026)'."""
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
    return f"{_period(rec.get('fy'), rec.get('taxp'))} (Filed {rec.get('dof')})"


def hsn_list(goods):
    lines = []
    for _, rec in parser._iter_records(goods or {}):
        code = rec.get("hsncd") or rec.get("saccd")
        desc = (rec.get("gdes") or rec.get("sdes") or "").strip()
        if code:
            lines.append(f"{code} - {desc}" if desc else str(code))
    return "\n".join(lines)


def summary_row(rec):
    if not rec.get("ok"):
        return {"GSTIN": rec["gstin"], "Legal Name": f"ERROR: {rec.get('error')}"}
    d = rec["details"]
    ppob, _, _, _ = parser._place(d.get("pradr"))
    return {
        "GSTIN": d.get("gstin") or rec["gstin"],
        "Legal Name": d.get("lgnm"),
        "Trade Name": d.get("tradeNam"),
        "Date of Registration": d.get("rgdt"),
        "GSTIN Status": d.get("sts"),
        "Nature of Business": parser._text(d.get("nba")),
        "Constitution": d.get("ctb"),
        "Administrative Office": d.get("stj"),
        "Other Office": d.get("ctj"),
        "Taxpayer Type": d.get("dty"),
        "PPoB": ppob,
        "Aadhaar Authenticated": d.get("adhrVFlag"),
        "HSN": hsn_list(rec.get("goods")),
        "Last GSTR-1 Filed": last_filed(rec.get("returns"), "GSTR1"),
        "Last GSTR-3B Filed": last_filed(rec.get("returns"), "GSTR3B"),
    }


def write_summary_sheet(wb, records, title="GST Summary"):
    """Add the formatted summary sheet (filters, wrapped headers) to an xlsxwriter workbook.
    Call it before adding other sheets so it is the first tab."""
    ws = wb.add_worksheet(title)
    head = wb.add_format({"bold": True, "bg_color": "#D9E1F2", "border": 1, "border_color": "#999999",
                          "align": "center", "valign": "vcenter", "text_wrap": True})
    body = wb.add_format({"valign": "top", "text_wrap": True, "border": 1, "border_color": "#999999"})
    ws.write_row(0, 0, COLUMNS, head)
    for r, rec in enumerate(records, 1):
        row = summary_row(rec)
        ws.write_row(r, 0, [str(row.get(c) or "") for c in COLUMNS], body)
    for i, col in enumerate(COLUMNS):
        ws.set_column(i, i, WIDTHS.get(col, 15))
    ws.set_row(0, 32)
    ws.freeze_panes(1, 1)
    ws.autofilter(0, 0, len(records), len(COLUMNS) - 1)
    return ws
