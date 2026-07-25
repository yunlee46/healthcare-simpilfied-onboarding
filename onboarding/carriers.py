"""A short, forgiving carrier list for the searchable (datalist) input.

Not exhaustive — "Other" free text is always accepted. ``match`` does the
"did you mean?" normalization so messy input like "blue cross" becomes the
canonical name.
"""
from __future__ import annotations

CARRIERS = [
    "Aetna",
    "Anthem",
    "Blue Cross Blue Shield",
    "Cigna",
    "Kaiser Permanente",
    "UnitedHealthcare",
    "Humana",
    "Centene",
    "Molina Healthcare",
    "Oscar Health",
    "Health Net",
    "WellCare",
    "Ambetter",
    "Highmark",
    "Independence Blue Cross",
    "Horizon Blue Cross Blue Shield",
    "Premera Blue Cross",
    "Regence",
    "Medica",
    "UPMC Health Plan",
]

# Common shorthands -> canonical.
_ALIASES = {
    "bcbs": "Blue Cross Blue Shield",
    "blue cross": "Blue Cross Blue Shield",
    "blue shield": "Blue Cross Blue Shield",
    "united": "UnitedHealthcare",
    "united health": "UnitedHealthcare",
    "uhc": "UnitedHealthcare",
    "kaiser": "Kaiser Permanente",
}


def match(raw: str) -> tuple[str, bool]:
    """Return ``(canonical_name, was_suggested)``.

    ``was_suggested`` is True when we changed the user's text to a canonical
    name, so the UI can show a "did you mean?" confirmation.
    """
    if not raw:
        return "", False
    text = raw.strip()
    low = text.lower()

    for name in CARRIERS:
        if low == name.lower():
            return name, False

    if low in _ALIASES:
        return _ALIASES[low], True

    for name in CARRIERS:
        if low in name.lower() or name.lower() in low:
            return name, low != name.lower()

    # Unknown carrier — keep exactly what they typed.
    return text, False
