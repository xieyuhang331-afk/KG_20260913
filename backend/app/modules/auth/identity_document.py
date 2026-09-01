from __future__ import annotations

import re
from datetime import date

_PRC_ID_RE = re.compile(r"^[0-9]{17}[0-9X]$", re.ASCII)
_WEIGHTS = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
_CHECK_CODES = "10X98765432"


def canonicalize_prc_resident_identity(value: str) -> str:
    if type(value) is not str:
        raise ValueError("IDENTITY_DOCUMENT_INVALID")
    normalized = value.strip()
    if len(normalized) == 18 and normalized[-1:] == "x":
        normalized = normalized[:-1] + "X"
    if _PRC_ID_RE.fullmatch(normalized) is None:
        raise ValueError("IDENTITY_DOCUMENT_INVALID")
    expected = _CHECK_CODES[
        sum(
            int(digit) * weight
            for digit, weight in zip(normalized[:17], _WEIGHTS, strict=True)
        )
        % 11
    ]
    if normalized[-1] != expected:
        raise ValueError("IDENTITY_DOCUMENT_INVALID")
    try:
        date(
            int(normalized[6:10]),
            int(normalized[10:12]),
            int(normalized[12:14]),
        )
    except ValueError:
        raise ValueError("IDENTITY_DOCUMENT_INVALID") from None
    return normalized


def parse_prc_resident_identity_birth_date(value: str) -> date:
    canonical = canonicalize_prc_resident_identity(value)
    return date(
        int(canonical[6:10]),
        int(canonical[10:12]),
        int(canonical[12:14]),
    )


def mask_prc_resident_identity(value: str) -> str:
    canonical = canonicalize_prc_resident_identity(value)
    return f"{canonical[:6]}********{canonical[-4:]}"
