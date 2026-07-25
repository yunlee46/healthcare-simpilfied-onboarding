"""All journey logic, server-side. No JavaScript anywhere.

State lives in the signed-cookie session under ``data`` (the canonical schema).
Each answered step is written immediately, so a refresh resumes in place.
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone

from django.conf import settings
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from . import flow, forms, gemini, schema
from .carriers import CARRIERS

# Fields that make an estimate meaningful (drives the readiness indicator).
_IMPORTANT = [
    ("plan.carrier", "Carrier"),
    ("costSharing.deductibleIndividual", "Deductible"),
    ("costSharing.oopMaxIndividual", "Out-of-pocket max"),
    ("costSharing.coinsurancePct", "Coinsurance"),
    ("costSharing.monthlyPremium", "Monthly premium"),
]


# --- session helpers ------------------------------------------------------
def _get_data(request):
    data = request.session.get("data")
    if not data:
        data = schema.empty_schema()
        request.session["data"] = data
    return data


def _save(request, data):
    _apply_inferences(data)
    request.session["data"] = data
    request.session.modified = True


def _touch_source(request, data):
    """Keep meta.source honest: manual / upload / mixed."""
    has_doc = bool(data["meta"].get("fieldsFromDocument"))
    manual = bool(request.session.get("manual_used"))
    if has_doc and manual:
        data["meta"]["source"] = "mixed"
    elif has_doc:
        data["meta"]["source"] = "upload"
    else:
        data["meta"]["source"] = "manual"


def _record_step_source(request, data, st):
    """Record per-field provenance for a manually answered step.

    manual = the member typed/picked it; assumed = an explicit 'start at $0';
    unknown = an explicit 'I'm not sure' skip with no value.
    """
    path = st["path"]
    if st["input"] == "prescriptions":
        return
    value = schema.get_path(data, path)
    action = request.POST.get("action", "next")
    if action == "skip":
        if value == st.get("skip_value") and st.get("skip_value") is not None:
            schema.set_source(data, path, "assumed")
        else:
            schema.set_source(data, path, "unknown")
    elif value not in (None, "", []):
        schema.set_source(data, path, "manual", member_confirmed=True)
    else:
        schema.set_source(data, path, "unknown")


def _apply_inferences(data):
    """Derive fields we can, and label them 'inferred'.

    HSA eligibility follows from an HDHP plan type — unless the member has
    already answered it themselves.
    """
    plan_type = schema.get_path(data, "plan.planType")
    elig_src = schema.get_source(data, "hsa.eligible")
    member_set = bool(elig_src) and elig_src.get("source") in ("manual",)
    if plan_type == "HDHP" and not member_set and schema.get_path(data, "hsa.eligible") is None:
        schema.set_path(data, "hsa.eligible", True)
        schema.mark_unknown(data, "hsa.eligible", False)
        schema.set_source(data, "hsa.eligible", "inferred",
                          snippet="Plan type is HDHP, which is HSA-eligible.")


# --- screens --------------------------------------------------------------
@require_http_methods(["GET", "POST"])
def welcome(request):
    if request.method == "POST":
        name = (request.POST.get("name") or "").strip()
        if not name:
            return render(request, "onboarding/welcome.html",
                          {"error": "Just a first name is perfect."})
        # Fresh start each time someone begins from the welcome screen.
        data = schema.empty_schema()
        data["identity"]["name"] = name
        request.session["manual_used"] = False
        _save(request, data)
        return redirect("path_choice")

    data = request.session.get("data") or {}
    name = schema.get_path(data, "identity.name") if data else None
    return render(request, "onboarding/welcome.html", {"name": name})


@require_http_methods(["GET"])
def path_choice(request):
    data = _get_data(request)
    return render(request, "onboarding/path.html",
                  {"name": schema.get_path(data, "identity.name")})


@require_http_methods(["GET"])
def manual_start(request):
    data = _get_data(request)
    return redirect("step", step_id=flow.first_step_id(data))


def _safe_next(raw):
    """Only allow an internal path back into the flow."""
    if raw and raw.startswith("/") and not raw.startswith("//"):
        return raw
    return ""


@require_http_methods(["GET"])
def upload(request):
    return render(request, "onboarding/upload.html",
                  {"gemini_ready": gemini.is_configured(),
                   "next": _safe_next(request.GET.get("next", "")),
                   "flash": request.session.pop("flash", None)})


@require_http_methods(["POST"])
def extract(request):
    data = _get_data(request)
    doc_type_hint = request.POST.get("docType", "")
    files = request.FILES.getlist("document")

    if not files:
        request.session["flash"] = "Pick a photo or file first."
        return redirect("upload")

    uploads = [(f.read(), f.content_type or "application/octet-stream") for f in files]

    try:
        result = gemini.extract_document(uploads, doc_type_hint)
    except gemini.GeminiUnavailable:
        request.session["flash"] = (
            "Document reading isn't set up right now — no problem, "
            "let's do it by hand. It only takes a minute."
        )
        return redirect("manual_start")
    except gemini.GeminiError:
        request.session["flash"] = (
            "I couldn't read that clearly — let's fill it in together instead."
        )
        return redirect("manual_start")

    filled = schema.merge_extraction(data, result["fields"], result["documentType"],
                                     result.get("evidence"))
    _touch_source(request, data)
    _save(request, data)

    doc_label = result["documentType"] if result["documentType"] in ("SBC", "EOB") else "document"
    request.session["extract_summary"] = {
        "documentType": result["documentType"],
        "docLabel": doc_label,
        "count": len(filled),
        "filled": filled,
        "unreadable": result.get("unreadable", []),
    }

    # Mid-flow upload (from the persistent 📎): drop the member back where they
    # were with a toast, so they never lose their place. First upload from the
    # path screen has no `next` and goes to review to show everything we found.
    nxt = _safe_next(request.POST.get("next", ""))
    if nxt:
        request.session["flash"] = (
            f"+{len(filled)} answered from your {doc_label}" if filled
            else "Couldn't get much from that one — no problem, just tell me."
        )
        return redirect(nxt)
    return redirect("review")


@require_http_methods(["GET", "POST"])
def step(request, step_id):
    data = _get_data(request)
    st = flow.get_step(step_id)
    active_ids = [s["id"] for s in flow.active_steps(data)]

    if st is None or step_id not in active_ids:
        return redirect("review")

    return_to = request.POST.get("return") or request.GET.get("return") or ""

    if request.method == "POST":
        if st["input"] == "prescriptions":
            done = _handle_prescriptions(request, data)
            _save(request, data)
            if not done:
                url = reverse("step", args=[step_id])
                return redirect(f"{url}?return=review" if return_to == "review" else url)
        else:
            error = forms.handle(st, request, data)
            if error:
                return render(request, "onboarding/step.html",
                              _step_ctx(st, data, step_id, error=error, return_to=return_to))
            request.session["manual_used"] = True
            _record_step_source(request, data, st)
            _touch_source(request, data)
            _save(request, data)

        if return_to == "review":
            return redirect("review")
        nxt = flow.next_step_id(step_id, data)
        return redirect("step", step_id=nxt) if nxt else redirect("review")

    ctx = _step_ctx(st, data, step_id, return_to=return_to)
    ctx["flash"] = request.session.pop("flash", None)
    return render(request, "onboarding/step.html", ctx)


def _handle_prescriptions(request, data) -> bool:
    """Returns True when the user is done with the prescriptions step."""
    action = request.POST.get("action", "done")
    scripts = data.setdefault("prescriptions", [])

    if action == "add":
        name = (request.POST.get("drugName") or "").strip()
        if name:
            scripts.append({
                "drugName": name,
                "dosage": (request.POST.get("dosage") or "").strip() or None,
                "frequency": request.POST.get("frequency") or None,
                "paymentMethod": request.POST.get("paymentMethod") or None,
                "preferredPharmacy": (request.POST.get("pharmacy") or "").strip() or None,
            })
        return False

    if action == "remove":
        try:
            idx = int(request.POST.get("index", ""))
            scripts.pop(idx)
        except (ValueError, IndexError):
            pass
        return False

    # done / skip
    schema.mark_unknown(data, "prescriptions", len(scripts) == 0)
    request.session["manual_used"] = True
    return True


def _step_ctx(st, data, step_id, error=None, return_to=""):
    current = schema.get_path(data, st["path"])
    return {
        "step": st,
        "step_id": step_id,
        "value": current,
        "value_str": "" if current is None else current,
        "selected": {str(current)} if not isinstance(current, list) else set(map(str, current)),
        "error": error,
        "progress": flow.progress(step_id, data),
        "left": flow.steps_left(data),
        "section": st.get("section", ""),
        "prev_id": flow.prev_step_id(step_id, data),
        "return_to": return_to,
        "name": schema.get_path(data, "identity.name"),
        "prescriptions": data.get("prescriptions", []),
        "carriers": CARRIERS,
        "show_attach": True,
    }


@require_http_methods(["GET"])
def review(request):
    data = _get_data(request)
    flash = request.session.pop("extract_summary", None)
    groups = _grouped_view(data)
    return render(request, "onboarding/review.html",
                  {"data": data, "groups": groups, "extract": flash,
                   "left": flow.steps_left(data),
                   "name": schema.get_path(data, "identity.name"),
                   "show_attach": True})


# Provenance chip per source (icon, label, css modifier).
_CHIPS = {
    "sbc":            ("📄", "From your SBC", "doc"),
    "eob":            ("🧾", "From your EOB", "doc"),
    "card":           ("💳", "From your card", "doc"),
    "manual":         ("💬", "You told me", "manual"),
    "inferred":       ("✨", "We worked it out", "inferred"),
    "assumed":        ("🅰", "Assumed", "assumed"),
    "not_applicable": ("—", "Not applicable", "unknown"),
    "unknown":        ("❓", "Not sure yet", "unknown"),
}


def _chip_for(data, path, value):
    src = schema.get_source(data, path)
    key = src["source"] if src else ("manual" if value not in (None, "", []) else "unknown")
    if key not in _CHIPS:
        key = "manual" if value not in (None, "", []) else "unknown"
    icon, label, css = _CHIPS[key]
    conf = src.get("confidence") if src else None
    return {"icon": icon, "label": label, "css": css,
            "confidence": round(conf * 100) if isinstance(conf, (int, float)) else None,
            "snippet": (src or {}).get("snippet")}


def _grouped_view(data):
    """Build (section -> rows) for review, each row carrying a provenance chip."""
    groups: dict[str, list] = {}
    for st in flow.active_steps(data):
        section = st.get("section", "Other")
        value = schema.get_path(data, st["path"])
        groups.setdefault(section, []).append({
            "id": st["id"],
            "prompt": st["prompt"],
            "display": _display(st, value),
            "path": st["path"],
            "chip": _chip_for(data, st["path"], value),
        })
    return groups


def _display(st, value):
    if value in (None, "", []):
        return "—"
    if st["input"] == "prescriptions":
        return ", ".join(p.get("drugName", "?") for p in value) or "—"
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    if st["input"] == "money":
        return f"${value:,}"
    # Map chip values back to their labels where possible.
    for opt in st.get("options", []):
        if str(opt["value"]) == str(value):
            return opt["label"]
    return str(value)


@require_http_methods(["GET"])
def summary(request):
    data = _get_data(request)
    _touch_source(request, data)
    data["meta"]["completedAt"] = datetime.now(timezone.utc).isoformat()
    _save(request, data)

    missing = [label for path, label in _IMPORTANT
               if schema.get_path(data, path) in (None, "", [])]
    ready = len(missing) == 0

    ytd_pct = _ytd_pct(data)
    pretty = json.dumps(data, indent=2)

    # Provenance breakdown for the receipt — where each captured value came from.
    order = ["sbc", "eob", "card", "manual", "inferred", "assumed"]
    counts = Counter(v.get("source") for v in data["meta"].get("sources", {}).values())
    provenance = [{"icon": _CHIPS[k][0], "label": _CHIPS[k][1], "css": _CHIPS[k][2],
                   "count": counts[k]} for k in order if counts.get(k)]

    return render(request, "onboarding/summary.html", {
        "data": data,
        "json": pretty,
        "ready": ready,
        "missing": missing,
        "ytd_pct": ytd_pct,
        "provenance": provenance,
        "name": schema.get_path(data, "identity.name"),
        "show_attach": True,
    })


def _ytd_pct(data):
    met = schema.get_path(data, "costSharing.deductibleMetYTD")
    total = schema.get_path(data, "costSharing.deductibleIndividual")
    if not total or met in (None, ""):
        return None
    try:
        return max(0, min(100, int(round(met / total * 100))))
    except (TypeError, ZeroDivisionError):
        return None


@require_http_methods(["GET"])
def summary_json(request):
    data = _get_data(request)
    payload = json.dumps(data, indent=2)
    resp = HttpResponse(payload, content_type="application/json")
    resp["Content-Disposition"] = 'attachment; filename="emme-plan.json"'
    return resp


@require_http_methods(["GET", "POST"])
def reset(request):
    request.session.flush()
    return redirect("welcome")
