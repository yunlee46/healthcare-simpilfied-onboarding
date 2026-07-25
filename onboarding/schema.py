"""The canonical JSON contract and helpers to read/write it by dotted path.

Both the manual flow and the document extractor write into this one shape.
Missing / unknown values are ``null`` (``None``). ``meta.unknownFields`` lists the
paths the member skipped so the pricing engine knows what to estimate vs. trust.
"""
from __future__ import annotations

import copy
from typing import Any


def empty_schema() -> dict:
    return {
        "identity": {"name": None, "email": None, "zipCode": None},
        "household": {"size": None, "incomeRange": None, "filingStatus": None},
        "plan": {"carrier": None, "planName": None, "metalTier": None, "planType": None},
        "costSharing": {
            "deductibleIndividual": None,
            "deductibleFamily": None,
            "deductibleMetYTD": None,
            "oopMaxIndividual": None,
            "oopMaxFamily": None,
            "oopMetYTD": None,
            "pcpCopay": None,
            "specialistCopay": None,
            "coinsurancePct": None,
            "monthlyPremium": None,
        },
        "hsa": {
            "eligible": None,
            "currentBalance": None,
            "ytdContributions": None,
            "employerContribution": None,
        },
        "prescriptions": [],
        "upcomingCare": {
            "plannedProcedures": [],
            "chronicConditions": [],
            "pregnancy": None,
            "behavioralHealthNeeds": None,
        },
        "meta": {
            "completedAt": None,
            "source": "manual",
            "fieldsFromDocument": [],
            "unknownFields": [],
        },
    }


def get_path(data: dict, dotted: str) -> Any:
    node: Any = data
    for part in dotted.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


def set_path(data: dict, dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    node = data
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value


def mark_unknown(data: dict, dotted: str, unknown: bool) -> None:
    """Add/remove a path from meta.unknownFields (idempotent)."""
    lst = data.setdefault("meta", {}).setdefault("unknownFields", [])
    if unknown and dotted not in lst:
        lst.append(dotted)
    if not unknown and dotted in lst:
        lst.remove(dotted)


def mark_from_document(data: dict, dotted: str) -> None:
    lst = data.setdefault("meta", {}).setdefault("fieldsFromDocument", [])
    if dotted not in lst:
        lst.append(dotted)


# --- Document merge -------------------------------------------------------
# SBC wins for plan rules; EOB wins for YTD-met amounts.
_SBC_FIELDS = {
    "costSharing.deductibleIndividual",
    "costSharing.deductibleFamily",
    "costSharing.oopMaxIndividual",
    "costSharing.oopMaxFamily",
    "costSharing.pcpCopay",
    "costSharing.specialistCopay",
    "costSharing.coinsurancePct",
    "costSharing.monthlyPremium",
    "plan.metalTier",
    "plan.planType",
}
_EOB_FIELDS = {
    "costSharing.deductibleMetYTD",
    "costSharing.oopMetYTD",
}


def merge_extraction(data: dict, partial: dict, doc_type: str) -> list[str]:
    """Merge a flattened {dotted_path: value} extraction into ``data``.

    Applies the doc-merge rule: for a field that both docs can carry, the
    authoritative doc for that field is allowed to overwrite; otherwise we only
    fill blanks. Returns the list of paths that were populated.
    """
    doc_type = (doc_type or "").upper()
    filled: list[str] = []
    for dotted, value in partial.items():
        if value in (None, "", []):
            continue
        current = get_path(data, dotted)
        authoritative = (
            (doc_type == "SBC" and dotted in _SBC_FIELDS)
            or (doc_type == "EOB" and dotted in _EOB_FIELDS)
        )
        if current in (None, "", []) or authoritative:
            set_path(data, dotted, value)
            mark_from_document(data, dotted)
            mark_unknown(data, dotted, False)
            filled.append(dotted)
    return filled


def clone(data: dict) -> dict:
    return copy.deepcopy(data)
