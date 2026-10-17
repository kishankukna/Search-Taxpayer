"""Turn the portal's JSON into flat, human-readable rows."""

import json

DETAIL_LABELS = {
    "gstin": "GSTIN",
    "lgnm": "Legal Name of Business",
    "tradeNam": "Trade Name",
    "sts": "GSTIN Status",
    "rgdt": "Effective Date of Registration",
    "cxdt": "Effective Date of Cancellation",
    "dty": "Taxpayer Type",
    "ctb": "Constitution of Business",
    "nba": "Nature of Business Activities",
    "ntcrbs": "Nature of Core Business Activity",
    "stj": "State Jurisdiction",
    "stjCd": "State Jurisdiction Code",
    "ctj": "Centre Jurisdiction",
    "ctjCd": "Centre Jurisdiction Code",
    "einvoiceStatus": "E-Invoicing Status",
    "adhrVFlag": "Aadhaar Authenticated",
    "adhrVdt": "Aadhaar Authentication Date",
    "ekycVFlag": "e-KYC Verified",
    "isFieldVisitConducted": "Field Visit Conducted",
    "cmpRt": "Composition Rate",
    "mbr": "Proprietor / Partners / Directors",
    "lstupdt": "Last Updated",
}

ADDR_ORDER = ["bno", "flno", "bnm", "st", "loc", "locality", "landMark", "city", "dst", "stcd", "pncd"]

SECTION_LABELS = {"bzgddtls": "Goods", "bzsdtls": "Services", "filingStatus": "Filing Status"}

FIELD_LABELS = {
    "hsncd": "HSN Code", "gdes": "Description",
    "saccd": "SAC Code", "sdes": "Description",
    "fy": "Financial Year", "taxp": "Tax Period", "mof": "Mode of Filing",
    "dof": "Date of Filing", "rtntype": "Return Type", "arn": "ARN", "status": "Status",
    "valid": "Valid",
}


def _text(value):
    if value is None:
        return ""
    if isinstance(value, list):
        if all(not isinstance(v, (dict, list)) for v in value):
            return ", ".join(str(v) for v in value if v not in (None, ""))
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return value


def format_address(addr):
    if not isinstance(addr, dict):
        return _text(addr)
    parts = []
    for key in ADDR_ORDER:
        val = addr.get(key)
        if val not in (None, "") and str(val).strip() not in parts:
            parts.append(str(val).strip())
    return ", ".join(parts)


def _place(place):
    """Split a pradr/adadr entry into (address text, nature, lat, long)."""
    place = place or {}
    addr = place.get("addr", place)
    text = place.get("adr") or (addr.get("adr") if isinstance(addr, dict) else None)
    return (
        text or format_address(addr),
        _text(place.get("ntr")),
        addr.get("lt", "") if isinstance(addr, dict) else "",
        addr.get("lg", "") if isinstance(addr, dict) else "",
    )


def flatten_details(d):
    """One row with every field from the taxpayer-details response."""
    row = {label: _text(d.get(key)) for key, label in DETAIL_LABELS.items()}

    address, nature, lat, lng = _place(d.get("pradr"))
    row["Principal Place of Business"] = address
    row["Nature of Business at Principal Place"] = nature
    row["Principal Place Latitude"] = lat
    row["Principal Place Longitude"] = lng
    row["Additional Places Count"] = len(d.get("adadr") or [])

    # Keep any field we don't know a label for, so nothing the portal returns is lost.
    for key, val in d.items():
        if key not in DETAIL_LABELS and key not in ("pradr", "adadr"):
            row[key] = _text(val)
    return row


def additional_places(d):
    rows = []
    for i, place in enumerate(d.get("adadr") or [], 1):
        address, nature, lat, lng = _place(place)
        rows.append({
            "GSTIN": d.get("gstin"), "Legal Name": d.get("lgnm"), "#": i,
            "Address": address, "Nature of Business": nature, "Latitude": lat, "Longitude": lng,
        })
    return rows


def _iter_records(obj, section="", context=None):
    context = context or {}
    if isinstance(obj, list):
        for item in obj:
            yield from _iter_records(item, section, context)
    elif isinstance(obj, dict):
        scalars = {k: v for k, v in obj.items() if not isinstance(v, (dict, list))}
        nested = {k: v for k, v in obj.items() if isinstance(v, (dict, list))}
        if not nested:
            yield section, {**context, **scalars}
        else:
            for key, val in nested.items():
                yield from _iter_records(val, SECTION_LABELS.get(key, key), {**context, **scalars})


def records(gstin, payload, extra=None):
    """Flatten a nested goods/returns payload into labelled rows."""
    rows = []
    for section, rec in _iter_records(payload):
        row = {"GSTIN": gstin, **(extra or {}), "Section": section}
        for key, val in rec.items():
            row[FIELD_LABELS.get(key, key)] = val
        rows.append(row)
    return rows
