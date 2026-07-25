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

# Domain-fact guards (see CLAUDE.md): premium is never on any document; YTD-met
# amounts come only from an EOB; metal tier is not an SBC field.
_EOB_ONLY = {"costSharing.deductibleMetYTD", "costSharing.oopMetYTD"}
_NEVER_FROM_DOC = {"costSharing.monthlyPremium"}
_NOT_ON_SBC_EXTRA = {"plan.metalTier"}

PROMPT = """You are extracting structured data from a US health-insurance document for a
member-facing tool. Return ONLY a JSON object — no prose, no markdown fences.

First classify "documentType": "SBC" (Summary of Benefits and Coverage),
"EOB" (Explanation of Benefits), or "other". Never guess — if it is neither an
SBC nor an EOB, return "other".

The SBC is a federally mandated template (PHS Act 2715, 45 CFR 147.200). Its
seven "Important Questions" appear verbatim and in this order:
  1. What is the overall deductible?
  2. Are there services covered before you meet your deductible?
  3. Are there other deductibles for specific services?
  4. What is the out-of-pocket limit for this plan?
  5. What is not included in the out-of-pocket limit?
  6. Will you pay less if you use a network provider?
  7. Do you need a referral to see a specialist?

Rules:
- Column labels vary by carrier — "Network Provider", "In-Network Provider",
  "Plan Provider", "Participating Provider", "Preferred Provider" all mean
  in-network. Some SBCs have three network columns; the leftmost is always the
  most generous. Read the in-network individual and family numbers.
- Cost-sharing cells are prose. Put the ORIGINAL text in "raw" and your parsed
  number in "value" (e.g. raw "$40 copay per visit, deductible does not apply"
  -> value 40). A deductible cell may hold several numbers across tiers;
  extract the in-network individual and family specifically.
- Money values are plain numbers (2000, not "$2,000"). Coinsurance is a percent
  number (20, not "20%").
- PREMIUM, METAL TIER and YEAR-TO-DATE amounts are NOT on an SBC. If you think
  you see them on an SBC, you are wrong — return null.
- On an EOB, read the accumulator table only: "Amount Applied to Date" (a.k.a.
  "met") is the YTD amount -> costSharing.deductibleMetYTD / costSharing.oopMetYTD,
  NOT the plan totals. "Met" may appear as a string where a number is expected —
  treat it as fully met. Do NOT infer plan totals, copays, coinsurance, premium,
  tier or plan type from an EOB.

Return this exact shape — every field is an object or null:
{
  "documentType": "SBC" | "EOB" | "other",
  "fields": {
    "plan.carrier":                    {"value": string|null, "confidence": 0-1, "snippet": string|null, "raw": string|null} | null,
    "plan.planName":                   {...} | null,
    "plan.metalTier":                  {...} | null,
    "plan.planType":                   {...} | null,
    "costSharing.deductibleIndividual":{...} | null,
    "costSharing.deductibleFamily":    {...} | null,
    "costSharing.deductibleMetYTD":    {...} | null,
    "costSharing.oopMaxIndividual":    {...} | null,
    "costSharing.oopMaxFamily":        {...} | null,
    "costSharing.oopMetYTD":           {...} | null,
    "costSharing.pcpCopay":            {...} | null,
    "costSharing.specialistCopay":     {...} | null,
    "costSharing.coinsurancePct":      {...} | null,
    "costSharing.monthlyPremium":      {...} | null
  },
  "unreadable": [field keys you could not read confidently]
}
Use null for the whole field object when a value is not clearly present. NEVER guess."""


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


def _coerce(key: str, value):
    """Normalize a parsed value to the schema's type (money/percent -> number)."""
    if value is None or value == "":
        return None
    if key in ("plan.carrier", "plan.planName", "plan.metalTier", "plan.planType"):
        return str(value).strip() or None
    # numeric fields: strip any stray formatting the model left in
    try:
        num = float(str(value).replace("$", "").replace(",", "").replace("%", "").strip())
    except ValueError:
        return None
    return int(num) if num == int(num) else round(num, 2)


def _sanitize(payload: dict) -> dict:
    """Flatten the model's per-field objects into:
      fields:   {dotted_path: value}
      evidence: {dotted_path: {confidence, snippet, raw}}
    so the merge can record provenance. Tolerates either the rich object shape
    or a bare value, so an older/looser model response still works.
    """
    doc_type = str(payload.get("documentType", "other")).upper()
    if doc_type not in ("SBC", "EOB", "OTHER"):
        doc_type = "OTHER"

    raw_fields = payload.get("fields", {}) or {}
    fields: dict = {}
    evidence: dict = {}
    for key, obj in raw_fields.items():
        if key not in ALLOWED_PATHS or obj in (None, "", []):
            continue
        if isinstance(obj, dict):
            value = _coerce(key, obj.get("value"))
            conf, snippet, raw = obj.get("confidence"), obj.get("snippet"), obj.get("raw")
        else:
            value, conf, snippet, raw = _coerce(key, obj), None, None, None
        if value in (None, "", []):
            continue
        fields[key] = value
        evidence[key] = {
            "confidence": conf if isinstance(conf, (int, float)) else None,
            "snippet": str(snippet) if snippet else None,
            "raw": str(raw) if raw else None,
        }

    # Enforce the domain facts server-side, in case the model slips:
    #   premium is NEVER on any document; YTD-met and metal tier are NOT on an SBC.
    def _drop(keys):
        for k in keys:
            fields.pop(k, None)
            evidence.pop(k, None)

    _drop(_NEVER_FROM_DOC)
    if doc_type == "EOB":
        _drop([k for k in list(fields) if k not in _EOB_ONLY])
    elif doc_type == "SBC":
        _drop(_EOB_ONLY | _NOT_ON_SBC_EXTRA)

    unreadable = [k for k in payload.get("unreadable", []) if k in ALLOWED_PATHS]
    return {"documentType": doc_type, "fields": fields,
            "evidence": evidence, "unreadable": unreadable}
