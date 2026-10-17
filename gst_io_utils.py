"""Reading GSTIN lists and writing results."""

import json
import os

import pandas as pd
import xlsxwriter

import gst_parser as parser
from gst_gstin import GSTIN_RE, extract_gstins, normalize


def read_gstins_from_file(path, column=None, sheet=None):
    """Read GSTINs from .xlsx/.xls/.csv/.txt. Uses `column` if given, else auto-detects."""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".txt", ".text"):
        with open(path, encoding="utf-8-sig") as f:
            return extract_gstins(f.read())

    if ext == ".csv":
        df = pd.read_csv(path, dtype=str, keep_default_na=False)
    else:
        df = pd.read_excel(path, dtype=str, keep_default_na=False, sheet_name=sheet or 0)

    if column:
        match = [c for c in df.columns if str(c).strip().lower() == column.strip().lower()]
        if not match:
            raise ValueError(f"Column '{column}' not found. Columns: {list(df.columns)}")
        values = df[match[0]]
    else:
        # Pick the column with the most GSTIN-looking values; fall back to scanning every cell.
        best, best_hits = None, 0
        for col in df.columns:
            hits = df[col].map(lambda v: bool(GSTIN_RE.fullmatch(normalize(v)))).sum()
            if hits > best_hits:
                best, best_hits = col, hits
        values = df[best] if best is not None else df.astype(str).agg(" ".join, axis=1)

    return extract_gstins(" ".join(normalize(v) for v in values))


def raw_path(raw_dir, gstin):
    return os.path.join(raw_dir, f"{gstin}.json")


def save_raw(raw_dir, record):
    os.makedirs(raw_dir, exist_ok=True)
    with open(raw_path(raw_dir, record["gstin"]), "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2, ensure_ascii=False)


def load_raw(raw_dir, gstin):
    path = raw_path(raw_dir, gstin)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_excel(records, out_path, summary_only=False):
    """records: list of dicts {gstin, ok, error, details, goods, returns, warnings, fetched_at}.
    First sheet is the one-row-per-GSTIN summary; detail sheets follow unless summary_only."""
    from gst_summary import write_summary_sheet

    summary, places, goods, returns, errors = [], [], [], [], []

    for rec in records:
        g = rec["gstin"]
        if not rec.get("ok"):
            errors.append({"GSTIN": g, "Error": rec.get("error"), "Checked At": rec.get("fetched_at")})
            continue
        d = rec["details"]
        row = parser.flatten_details(d)
        row["Fetched At"] = rec.get("fetched_at")
        row["Warnings"] = "; ".join(rec.get("warnings") or [])
        summary.append(row)
        places.extend(parser.additional_places(d))
        if rec.get("goods"):
            goods.extend(parser.records(g, rec["goods"]))
        for fy, payload in (rec.get("returns") or {}).items():
            returns.extend(parser.records(g, payload, {"Requested FY": fy}))

    sheets = {
        "Taxpayer Details": summary,
        "Additional Places": places,
        "Goods & Services": goods,
        "Return Filing": returns,
        "Errors": errors,
    }
    if summary_only:
        sheets = {}
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    # xlsxwriter is several times faster than openpyxl for writing. Keep every value as text
    # (HSN codes like 09022040 must not lose their leading zero; nothing becomes a formula/URL).
    wb = xlsxwriter.Workbook(out_path, {"strings_to_numbers": False, "strings_to_formulas": False,
                                        "strings_to_urls": False})
    write_summary_sheet(wb, records)
    head = wb.add_format({"bold": True, "bg_color": "#D9E1F2"})
    for name, rows in sheets.items():
        if not rows:
            continue
        cols = list(dict.fromkeys(k for row in rows for k in row))
        ws = wb.add_worksheet(name)
        ws.write_row(0, 0, cols, head)
        for r, row in enumerate(rows, 1):
            ws.write_row(r, 0, [_cell(row.get(c)) for c in cols])
        for i, col in enumerate(cols):
            width = max([len(str(col))] + [len(str(row.get(col) or "")) for row in rows[:500]])
            ws.set_column(i, i, min(max(width + 2, 10), 60))
        ws.freeze_panes(1, 1)
        ws.autofilter(0, 0, len(rows), len(cols) - 1)
    wb.close()
    return {"GST Summary": len(records), **{k: len(v) for k, v in sheets.items()}}


def _cell(value):
    if value is None or (isinstance(value, float) and value != value):
        return ""
    if isinstance(value, (int, float)):
        return value
    return str(value)
