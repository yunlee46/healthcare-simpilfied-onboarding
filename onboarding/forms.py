"""Forgiving front, strict back.

Accept messy input ("$2k", "blue cross"), normalize it to clean values, and
write them into the canonical schema. Every ``handle_*`` returns an error
string (to re-render the step) or ``None`` on success.
"""
from __future__ import annotations

import re

from . import carriers, schema
from .flow import UNSURE

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def parse_money(raw: str):
    """"$2k" -> 2000.0, "2,000" -> 2000.0, "" -> None. Raises ValueError."""
    if raw is None:
        return None
    s = raw.strip().lower().replace("$", "").replace(",", "").replace(" ", "")
    if not s:
        return None
    mult = 1
    if s.endswith("k"):
        mult = 1000
        s = s[:-1]
    value = float(s) * mult
    if value < 0:
        raise ValueError("negative")
    # Keep it clean: whole dollars come out as ints.
    return int(value) if value == int(value) else round(value, 2)


def _cast_chip(value: str, cast: str):
    if cast == "int":
        return int(value)
    return value


def handle(step: dict, request, data: dict) -> str | None:
    """Read POST for ``step``, normalize, and store into ``data``.

    Returns an error message to re-render, or None to advance.
    """
    action = request.POST.get("action", "next")
    path = step["path"]
    input_type = step["input"]

    # Explicit skip / "I'm not sure".
    if action == "skip":
        skip_value = step.get("skip_value", None)
        schema.set_path(data, path, skip_value)
        schema.mark_unknown(data, path, skip_value is None)
        return None

    if input_type in ("text",):
        val = (request.POST.get("value") or "").strip()
        if not val and step.get("required"):
            return "This one's required — mind filling it in?"
        schema.set_path(data, path, val or None)
        schema.mark_unknown(data, path, not val)
        return None

    if input_type == "email":
        val = (request.POST.get("value") or "").strip()
        if not val:
            return "We need an email to send your estimate."
        if not _EMAIL_RE.match(val):
            return "That doesn't look like an email — mind checking it?"
        schema.set_path(data, path, val)
        schema.mark_unknown(data, path, False)
        return None

    if input_type == "zip":
        val = (request.POST.get("value") or "").strip()
        digits = re.sub(r"\D", "", val)
        if len(digits) != 5:
            return "A ZIP code is 5 digits — mind checking?"
        schema.set_path(data, path, digits)
        schema.mark_unknown(data, path, False)
        return None

    if input_type == "money":
        raw = request.POST.get("value") or ""
        try:
            val = parse_money(raw)
        except ValueError:
            return "I couldn't read that as an amount — try something like $2,000."
        if val is None:
            if step.get("required"):
                return "Mind entering an amount?"
            schema.set_path(data, path, step.get("skip_value"))
            schema.mark_unknown(data, path, step.get("skip_value") is None)
            return None
        schema.set_path(data, path, val)
        schema.mark_unknown(data, path, False)
        return None

    if input_type in ("chips", "stepper"):
        val = request.POST.get("value")
        options = {str(o["value"]) for o in step.get("options", [])}
        if val is None or val == "":
            if step.get("required"):
                return "Pick one to continue."
            schema.set_path(data, path, None)
            schema.mark_unknown(data, path, True)
            return None
        if val == UNSURE:
            schema.set_path(data, path, None)
            schema.mark_unknown(data, path, True)
            return None
        if val not in options:
            return "Hmm, that wasn't one of the options — mind trying again?"
        schema.set_path(data, path, _cast_chip(val, step.get("cast", "str")))
        schema.mark_unknown(data, path, False)
        return None

    if input_type == "carrier":
        raw = (request.POST.get("value") or "").strip()
        if not raw:
            schema.set_path(data, path, None)
            schema.mark_unknown(data, path, True)
            return None
        name, _suggested = carriers.match(raw)
        schema.set_path(data, path, name)
        schema.mark_unknown(data, path, False)
        return None

    if input_type == "multiselect":
        selected = request.POST.getlist("value")
        valid = {o["value"] for o in step.get("options", [])}
        chosen = [v for v in selected if v in valid]
        if step.get("allow_other"):
            other = (request.POST.get("other") or "").strip()
            if other:
                chosen.append(other)
        schema.set_path(data, path, chosen)
        schema.mark_unknown(data, path, len(chosen) == 0)
        return None

    if input_type == "prescriptions":
        # Handled directly in the view (add/remove/done actions).
        return None

    return None
