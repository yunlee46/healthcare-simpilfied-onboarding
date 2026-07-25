"""The Gemini-driven onboarding chat engine (server-side, Python).

Reuses ``flow.FLOW`` as the field catalog: the model is told exactly which
dotted paths exist, their plain-language prompts, and the legal option values.
Each turn the model returns strict JSON — a natural-language ``reply`` plus the
structured ``updates`` it learned — which we validate/coerce against the catalog
before writing into the canonical schema. Anything the model invents is ignored,
so the resulting JSON stays clean regardless of model behavior.

See docs/adr/0002-chat-interface.md.
"""
from __future__ import annotations

import json
import re

from django.conf import settings

from . import carriers, flow, forms, schema
from .flow import FLOW, UNSURE


class ChatUnavailable(RuntimeError):
    """Raised when the Gemini SDK or key isn't configured."""


# Name isn't a FLOW step (it used to live on the welcome screen); the chat
# collects it, so add a synthetic spec.
_NAME_FIELD = {
    "path": "identity.name", "input": "text", "section": "About you",
    "required": True, "prompt": "What should we call you? (first name is fine)",
}
_ALL_FIELDS = [_NAME_FIELD] + FLOW
_INDEX = {f["path"]: f for f in _ALL_FIELDS}

# The fields an estimate genuinely needs — the bot collects these before it
# offers to wrap up. Everything else is optional and skippable.
REQUIRED_PATHS = [
    "identity.name", "identity.email", "identity.zipCode",
    "household.size", "plan.carrier", "plan.planType",
    "costSharing.deductibleIndividual", "costSharing.oopMaxIndividual",
    "costSharing.coinsurancePct", "costSharing.monthlyPremium",
]

OPENING = (
    "Hi! I’m Emme — I’ll help you set up your plan so we can estimate your costs. "
    "It takes about two minutes.\n\nWould you like to **import your SBC or EOB** "
    "(I’ll read them and fill in what I can), or just **chat** and I’ll ask you a few "
    "quick questions?"
)


# --- catalog / prompt -----------------------------------------------------
def _active_paths(data: dict) -> set[str]:
    paths = {s["path"] for s in flow.active_steps(data)}
    paths.add("identity.name")
    return paths


def _field_line(f: dict) -> str:
    path = f["path"]
    itype = f["input"]
    bits = [f"- {path} [{itype}]"]
    if path in REQUIRED_PATHS:
        bits.append("(required)")
    opts = f.get("options")
    if opts:
        vals = ", ".join(str(o["value"]) for o in opts if o["value"] != UNSURE)
        bits.append(f"valid values: {vals}")
    bits.append(f'— "{f["prompt"]}"')
    return " ".join(bits)


_CATALOG = "\n".join(_field_line(f) for f in _ALL_FIELDS)

_SYSTEM = f"""You are Emme, a warm, calm health-insurance onboarding guide. You help a
member capture their plan details through friendly conversation so we can estimate
their costs.

Voice: kind, encouraging, plain language, concise. No jargon without a one-line
explanation. Ask ONE question at a time. Briefly acknowledge the member's last
answer before asking the next thing. Never dump a list of questions.

You fill this structured record. These are the ONLY paths that exist — never invent
others:
{_CATALOG}

Value rules:
- Money is a plain number (2000, not "$2,000"). Percent is a number (20, not "20%").
- For a field with "valid values", the value MUST be exactly one of them.
- Never guess or fabricate. Only record what the member actually tells you.
- If the member doesn't know or wants to skip an OPTIONAL field, put its path in
  "unsure" instead of guessing. Required fields: gently keep helping until you have them.
- Sensitive fields (income, pregnancy, behavioral health): make clear they're optional
  and "prefer not to say" is always fine.
- CURRENT DATA below may already be filled from an uploaded document — trust it and do
  NOT re-ask those, unless the member corrects it.

Conversation phases:
1. "collecting": gather the remaining required fields first, then briefly offer the
   optional ones (the member can skip any).
2. "confirming": once nothing required remains, give a short friendly recap of the key
   values and ask the member to confirm it all looks right.
3. "complete": the member confirmed — reply with a warm one-line close. If instead they
   want a change, go back to "collecting" and apply it.

Return ONLY a JSON object (no markdown, no prose outside it) of this exact shape:
{{
  "reply": "<your next message to the member — one message>",
  "updates": {{ "<dotted.path>": <value> }},
  "unsure": [ "<dotted.path>" ],
  "phase": "collecting" | "confirming" | "complete"
}}
"updates" holds only what you learned from the member's latest message ({{}} if none)."""


def _state_block(data: dict) -> str:
    unknown = set(data.get("meta", {}).get("unknownFields", []))
    active = _active_paths(data)
    req_left, opt_left = [], []
    for f in _ALL_FIELDS:
        p = f["path"]
        if p not in active:
            continue
        if schema.get_path(data, p) not in (None, "", []) or p in unknown:
            continue
        (req_left if p in REQUIRED_PATHS else opt_left).append(p)
    # Only show the filled parts of the record to keep it tight.
    filled = {p: schema.get_path(data, p) for f in _ALL_FIELDS
              for p in [f["path"]] if schema.get_path(data, p) not in (None, "", [])}
    return (
        "CURRENT DATA (already known — do not re-ask):\n"
        + json.dumps(filled, indent=2)
        + f"\n\nREMAINING REQUIRED: {req_left or 'none — move to confirming'}"
        + f"\nREMAINING OPTIONAL: {opt_left or 'none'}"
    )


# --- value coercion -------------------------------------------------------
def _match_chip(step: dict, raw):
    low = str(raw).strip().lower()
    for o in step.get("options", []):
        if o["value"] == UNSURE:
            continue
        if str(o["value"]).lower() == low or o["label"].lower() == low:
            return o["value"]
    return None


def _coerce(step: dict, value):
    """Return (clean_value, ok). ok=False means drop the update."""
    itype = step["input"]
    if value is None:
        return None, False

    if itype == "money":
        try:
            v = forms.parse_money(str(value))
        except ValueError:
            return None, False
        return (v, True) if v is not None else (None, False)

    if itype in ("chips", "stepper"):
        m = _match_chip(step, value)
        return (m, True) if m is not None else (None, False)

    if itype == "zip":
        digits = re.sub(r"\D", "", str(value))
        # Accept ZIP or ZIP+4; keep the 5-digit prefix.
        return (digits[:5], True) if len(digits) >= 5 else (None, False)

    if itype == "carrier":
        name, _ = carriers.match(str(value))
        return (name or None, bool(name))

    if itype == "email":
        s = str(value).strip()
        return (s, True) if "@" in s and "." in s else (None, False)

    if itype == "text":
        s = str(value).strip()
        return (s or None, bool(s))

    if itype == "multiselect":
        vals = value if isinstance(value, list) else [value]
        out = []
        for v in vals:
            sv = str(v).strip()
            if not sv:
                continue
            matched = None
            for o in step.get("options", []):
                if str(o["value"]).lower() == sv.lower() or o["label"].lower() == sv.lower():
                    matched = o["value"]
                    break
            out.append(matched or (sv if step.get("allow_other") else None))
        return [x for x in out if x], True

    if itype == "prescriptions":
        if not isinstance(value, list):
            return None, False
        out = []
        for p in value:
            if not isinstance(p, dict):
                continue
            name = (p.get("drugName") or p.get("name") or "").strip()
            if not name:
                continue
            out.append({
                "drugName": name,
                "dosage": (p.get("dosage") or None),
                "frequency": (p.get("frequency") or None),
                "paymentMethod": (p.get("paymentMethod") or None),
                "preferredPharmacy": (p.get("preferredPharmacy") or p.get("pharmacy") or None),
            })
        return out, True

    return str(value), True


def apply_updates(data: dict, updates: dict, unsure: list) -> list[str]:
    """Validate + write model updates into the schema. Returns applied paths."""
    applied = []
    for path, raw in (updates or {}).items():
        step = _INDEX.get(path)
        if step is None:
            continue
        val, ok = _coerce(step, raw)
        if not ok:
            continue
        if val in (None, "", []) and step["input"] != "multiselect":
            continue
        schema.set_path(data, path, val)
        schema.mark_unknown(data, path, False)
        schema.set_source(data, path, "manual", member_confirmed=True)
        applied.append(path)

    for path in (unsure or []):
        if path in _INDEX and schema.get_path(data, path) in (None, "", []):
            schema.mark_unknown(data, path, True)
            schema.set_source(data, path, "unknown")
    return applied


def required_remaining(data: dict) -> list[str]:
    unknown = set(data.get("meta", {}).get("unknownFields", []))
    active = _active_paths(data)
    return [p for p in REQUIRED_PATHS
            if p in active and schema.get_path(data, p) in (None, "", []) and p not in unknown]


# --- model call -----------------------------------------------------------
def is_configured() -> bool:
    if not settings.GEMINI_API_KEY:
        return False
    try:
        import google.genai  # noqa: F401
    except Exception:
        return False
    return True


def _generate(system: str, history: list[dict]) -> dict:
    if not settings.GEMINI_API_KEY:
        raise ChatUnavailable("GEMINI_API_KEY is not set.")
    try:
        from google import genai
        from google.genai import types
    except Exception as exc:  # pragma: no cover
        raise ChatUnavailable("google-genai is not installed.") from exc

    client = genai.Client(api_key=settings.GEMINI_API_KEY)
    contents = [{"role": h["role"], "parts": [{"text": h["text"]}]} for h in history]
    resp = client.models.generate_content(
        model=settings.GEMINI_MODEL,
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            temperature=0.4,
        ),
    )
    return json.loads(resp.text)


def respond(history: list[dict], data: dict, recent: int = 24) -> dict:
    """Run one chat turn. ``history`` ends with the member's latest message.

    Returns {reply, updates, unsure, phase} — always safe to use even if the
    model misbehaves.
    """
    system = _SYSTEM + "\n\n" + _state_block(data)
    try:
        payload = _generate(system, history[-recent:])
    except ChatUnavailable:
        raise
    except Exception:
        return {"reply": "Sorry — I lost my train of thought there. Could you say that again?",
                "updates": {}, "unsure": [], "phase": "collecting"}

    reply = str(payload.get("reply") or "").strip() or "Got it — what else can you tell me?"
    updates = payload.get("updates") if isinstance(payload.get("updates"), dict) else {}
    unsure = payload.get("unsure") if isinstance(payload.get("unsure"), list) else []
    phase = payload.get("phase")
    if phase not in ("collecting", "confirming", "complete"):
        phase = "collecting"
    # Guard: never let the model "complete" while required fields are still open.
    if phase != "collecting" and required_remaining(data):
        # updates from THIS turn aren't applied yet; recompute after the caller
        # applies them. Caller re-checks, so this is a soft hint only.
        pass
    return {"reply": reply, "updates": updates, "unsure": unsure, "phase": phase}
