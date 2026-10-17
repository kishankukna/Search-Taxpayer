"""GSTIN format and checksum validation."""

import re

_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"

# 2-digit state code + 13 alphanumerics. Kept loose on purpose so special
# registrations (TDS/TCS, NRTP, UN bodies) are not rejected; the checksum does the real check.
GSTIN_RE = re.compile(r"(?<![0-9A-Z])\d{2}[0-9A-Z]{13}(?![0-9A-Z])")


def checksum_char(first14: str) -> str:
    total = 0
    for i, ch in enumerate(first14):
        product = _CHARS.index(ch) * (2 if i % 2 else 1)
        total += product // 36 + product % 36
    return _CHARS[(36 - total % 36) % 36]


def normalize(value) -> str:
    return re.sub(r"\s+", "", str(value or "")).upper()


def is_valid_gstin(gstin: str) -> bool:
    gstin = normalize(gstin)
    if not re.fullmatch(r"\d{2}[0-9A-Z]{13}", gstin):
        return False
    return checksum_char(gstin[:14]) == gstin[14]


def extract_entries(lines) -> list:
    """Input entries in their original order, one per GSTIN found, duplicates KEPT, so the
    output can be written row-for-row in the same serial order as the input.
    `lines` is pasted text or an iterable of cells/lines. A line with digits but no valid-looking
    GSTIN is kept as-is (it becomes an error row); lines without digits (headers, blanks) are skipped."""
    if isinstance(lines, str):
        lines = lines.splitlines()
    entries = []
    for line in lines:
        text = str(line or "").upper()
        found = GSTIN_RE.findall(re.sub(r"[^0-9A-Z]+", " ", text))
        if found:
            entries.extend(found)
        elif re.search(r"\d", text):
            entries.append(re.sub(r"\s+", "", text)[:40])
    return entries


def extract_gstins(text: str) -> list:
    """Find every GSTIN-looking token in free text (order kept, duplicates removed)."""
    seen, out = set(), []
    for match in GSTIN_RE.findall(str(text).upper()):
        if match not in seen:
            seen.add(match)
            out.append(match)
    return out
