"""Server-side Gemini vision extraction.

Lazy-imports the SDK so the app runs fine without it — the manual flow is the
demo-safe core. If the key or SDK is missing, ``extract_document`` raises
``GeminiUnavailable`` and the view falls back to manual for the whole doc.

Key rule baked into the prompt: an EOB only carries YTD-met amounts, never the
plan rules. Those come from the SBC. Never guess.
"""
from __future__ import annotations

import json

from django.conf import settings

# Paths the model is allowed to fill, so we can safely flatten/whitelist output.
ALLOWED_PATHS = {
    "plan.carrier",
    "plan.planName",
    "plan.metalTier",
    "plan.planType",
    "costSharing.deductibleIndividual",
    "costSharing.deductibleFamily",
    "costSharing.deductibleMetYTD",
    "costSharing.oopMaxIndividual",
    "costSharing.oopMaxFamily",
    "costSharing.oopMetYTD",
    "costSharing.pcpCopay",
    "costSharing.specialistCopay",
    "costSharing.coinsurancePct",
    "costSharing.monthlyPremium",
}

PROMPT = """You are extracting structured data from a US health-insurance document
(an SBC — Summary of Benefits and Coverage, or an EOB — Explanation of Benefits).

Return ONLY a JSON object with this exact shape:
{
  "documentType": "SBC" | "EOB" | "other",
  "fields": {
    "plan.carrier": string|null,
    "plan.planName": string|null,
    "plan.metalTier": "Bronze"|"Silver"|"Gold"|"Platinum"|null,
    "plan.planType": "HMO"|"PPO"|"EPO"|"HDHP"|null,
    "costSharing.deductibleIndividual": number|null,
    "costSharing.deductibleFamily": number|null,
    "costSharing.deductibleMetYTD": number|null,
    "costSharing.oopMaxIndividual": number|null,
    "costSharing.oopMaxFamily": number|null,
    "costSharing.oopMetYTD": number|null,
    "costSharing.pcpCopay": number|null,
    "costSharing.specialistCopay": number|null,
    "costSharing.coinsurancePct": number|null,
    "costSharing.monthlyPremium": number|null
  },
  "unreadable": [list of the field keys above you could not read confidently]
}

CRITICAL RULES:
- Use null for anything not clearly and confidently present. NEVER guess.
- Money values are plain numbers (2000, not "$2,000"). Coinsurance is a percent number (20, not "20%").
- On an EOB: "deductible applied to date"/"met" -> costSharing.deductibleMetYTD;
  "out-of-pocket applied to date" -> costSharing.oopMetYTD.
  An EOB does NOT contain the TOTAL deductible, OOP max, copays, coinsurance,
  premium, metal tier, or plan type — leave all of those null for an EOB.
- The plan-rule fields (total deductible, OOP max, copays, coinsurance, metal
  tier, plan type) come from an SBC.
"""


class GeminiUnavailable(RuntimeError):
    """Raised when the SDK or API key isn't configured."""


class GeminiError(RuntimeError):
    """Raised when the API call or parsing fails."""


def is_configured() -> bool:
    if not settings.GEMINI_API_KEY:
        return False
    try:
        import google.genai  # noqa: F401
    except Exception:
        return False
    return True


def extract_document(uploads: list, doc_type_hint: str = "") -> dict:
    """Run extraction on one or more uploaded files.

    ``uploads`` is a list of (bytes, mime_type). Returns:
      {"documentType": str, "fields": {dotted: value}, "unreadable": [str]}
    """
    if not settings.GEMINI_API_KEY:
        raise GeminiUnavailable("GEMINI_API_KEY is not set.")
    try:
        from google import genai
        from google.genai import types
    except Exception as exc:  # pragma: no cover
        raise GeminiUnavailable("google-genai is not installed.") from exc

    client = genai.Client(api_key=settings.GEMINI_API_KEY)

    parts = [PROMPT]
    if doc_type_hint:
        parts.append(f"The user says this document is: {doc_type_hint}.")
    for raw, mime in uploads:
        parts.append(types.Part.from_bytes(data=raw, mime_type=mime))

    try:
        resp = client.models.generate_content(
            model=settings.GEMINI_MODEL,
            contents=parts,
            config=types.GenerateContentConfig(response_mime_type="application/json"),
        )
        payload = json.loads(resp.text)
    except Exception as exc:
        raise GeminiError(f"Extraction failed: {exc}") from exc

    return _sanitize(payload)


def _sanitize(payload: dict) -> dict:
    doc_type = str(payload.get("documentType", "other")).upper()
    if doc_type not in ("SBC", "EOB", "OTHER"):
        doc_type = "OTHER"

    raw_fields = payload.get("fields", {}) or {}
    fields: dict = {}
    for key, value in raw_fields.items():
        if key in ALLOWED_PATHS and value not in (None, "", []):
            fields[key] = value

    # Enforce the EOB rule server-side too, in case the model slips.
    if doc_type == "EOB":
        fields = {
            k: v
            for k, v in fields.items()
            if k in ("costSharing.deductibleMetYTD", "costSharing.oopMetYTD")
        }

    unreadable = [k for k in payload.get("unreadable", []) if k in ALLOWED_PATHS]
    return {"documentType": doc_type, "fields": fields, "unreadable": unreadable}
