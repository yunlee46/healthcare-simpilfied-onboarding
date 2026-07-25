"""The conversational flow as a server-side step machine.

Each step is one question on its own screen. ``views.step`` renders the current
step and, on POST, normalizes + stores the answer in the session, then advances
to the next step whose ``condition`` passes. Conditional steps (family
deductible, HSA) are decided from session state.

Input types handled by the view/template:
  text | email | zip | money | chips | stepper | carrier | multiselect |
  prescriptions
"""
from __future__ import annotations

from . import schema

UNSURE = "__unsure__"  # sentinel chip -> stores null + marks field unknown


def chip(value, label):
    return {"value": value, "label": label}


def unsure_chip(label="Not sure"):
    return {"value": UNSURE, "label": label}


# Each step:
#   id, path, section (for grouping), input, prompt, why (subline),
#   options (chips), cast ("int"/"str"), required (bool),
#   optional (bool -> show a Skip button), skip_value (value used on skip),
#   condition (callable(data) -> bool), placeholder, help
FLOW = [
    # --- Identity (name is collected on the welcome screen) --------------
    {
        "id": "email", "path": "identity.email", "section": "About you",
        "input": "email", "required": True,
        "prompt": "Where should we send your cost estimate?",
        "why": "So you can get your results — we won't spam you.",
        "placeholder": "you@example.com",
    },
    {
        "id": "zip", "path": "identity.zipCode", "section": "About you",
        "input": "zip", "required": True,
        "prompt": "What's your ZIP code?",
        "why": "Pricing and networks are regional.",
        "placeholder": "12345",
    },
    # --- Household -------------------------------------------------------
    {
        "id": "household-size", "path": "household.size", "section": "Household",
        "input": "stepper", "cast": "int", "required": True,
        "prompt": "How many people are on your plan, including you?",
        "why": "Family deductibles differ from individual ones.",
        "options": [chip(1, "1"), chip(2, "2"), chip(3, "3"),
                    chip(4, "4"), chip(5, "5"), chip(6, "6+")],
    },
    {
        "id": "income", "path": "household.incomeRange", "section": "Household",
        "input": "chips", "cast": "str", "optional": True,
        "prompt": "Which range fits your household income?",
        "why": "Helps estimate what you'll actually pay.",
        "options": [
            chip("<30k", "Under $30k"), chip("30-50k", "$30k–$50k"),
            chip("50-75k", "$50k–$75k"), chip("75-100k", "$75k–$100k"),
            chip("100-150k", "$100k–$150k"), chip("150k+", "Over $150k"),
        ],
    },
    {
        "id": "filing", "path": "household.filingStatus", "section": "Household",
        "input": "chips", "cast": "str", "optional": True,
        "prompt": "How do you file taxes?",
        "why": "Filing status affects subsidies and thresholds.",
        "options": [
            chip("single", "Single"),
            chip("married_joint", "Married — joint"),
            chip("married_separate", "Married — separate"),
            chip("head_of_household", "Head of household"),
        ],
    },
    # --- Plan details ----------------------------------------------------
    {
        "id": "carrier", "path": "plan.carrier", "section": "Your plan",
        "input": "carrier", "optional": True,
        "prompt": "Who's your insurance through?",
        "why": "We'll match it to the right network.",
        "placeholder": "Start typing your carrier…",
    },
    {
        "id": "plan-name", "path": "plan.planName", "section": "Your plan",
        "input": "text", "optional": True,
        "prompt": "What's your plan called?",
        "why": "Optional — it's on the top of your SBC if you have it handy.",
        "placeholder": "e.g. Silver PPO 2000",
    },
    {
        "id": "metal-tier", "path": "plan.metalTier", "section": "Your plan",
        "input": "chips", "cast": "str", "optional": True,
        "prompt": "Do you know your plan's metal tier?",
        "why": "Bronze to Platinum — it hints at how costs are split.",
        "options": [
            chip("Bronze", "Bronze"), chip("Silver", "Silver"),
            chip("Gold", "Gold"), chip("Platinum", "Platinum"),
            unsure_chip(),
        ],
    },
    {
        "id": "plan-type", "path": "plan.planType", "section": "Your plan",
        "input": "chips", "cast": "str", "optional": True,
        "prompt": "What type of plan is it?",
        "why": "This changes how referrals and out-of-network care work.",
        "options": [
            chip("HMO", "HMO — in-network only, needs referrals"),
            chip("PPO", "PPO — flexible, see specialists directly"),
            chip("EPO", "EPO — in-network only, no referrals"),
            chip("HDHP", "HDHP — high deductible, HSA-eligible"),
            unsure_chip(),
        ],
    },
    # --- Cost-sharing (pricing-critical) --------------------------------
    {
        "id": "deductible-ind", "path": "costSharing.deductibleIndividual",
        "section": "Costs", "input": "money", "optional": True,
        "prompt": "What's your deductible for one person?",
        "why": "What you pay before insurance kicks in.",
        "placeholder": "$2,000",
    },
    {
        "id": "deductible-fam", "path": "costSharing.deductibleFamily",
        "section": "Costs", "input": "money", "optional": True,
        "prompt": "And the deductible for your whole family?",
        "why": "Families usually have a separate, higher number.",
        "placeholder": "$4,000",
        "condition": lambda d: (schema.get_path(d, "household.size") or 1) > 1,
    },
    {
        "id": "deductible-met", "path": "costSharing.deductibleMetYTD",
        "section": "Costs", "input": "money", "optional": True, "skip_value": 0,
        "prompt": "How much of that have you already paid this year?",
        "why": "Start at $0 if you're not sure.",
        "placeholder": "$0",
    },
    {
        "id": "oop-ind", "path": "costSharing.oopMaxIndividual",
        "section": "Costs", "input": "money", "optional": True,
        "prompt": "What's the most you'd pay in a whole year (one person)?",
        "why": "Your out-of-pocket max — the safety ceiling.",
        "placeholder": "$8,000",
    },
    {
        "id": "oop-fam", "path": "costSharing.oopMaxFamily",
        "section": "Costs", "input": "money", "optional": True,
        "prompt": "And the most your whole family would pay in a year?",
        "why": "The family out-of-pocket ceiling.",
        "placeholder": "$16,000",
        "condition": lambda d: (schema.get_path(d, "household.size") or 1) > 1,
    },
    {
        "id": "oop-met", "path": "costSharing.oopMetYTD",
        "section": "Costs", "input": "money", "optional": True, "skip_value": 0,
        "prompt": "How much of that have you hit this year?",
        "why": "Start at $0 if you're not sure.",
        "placeholder": "$0",
    },
    {
        "id": "pcp-copay", "path": "costSharing.pcpCopay",
        "section": "Costs", "input": "chips", "cast": "int", "optional": True,
        "prompt": "Typical copay for a regular doctor visit?",
        "why": "The flat fee you pay to see your primary doctor.",
        "options": [chip(0, "$0"), chip(25, "$25"), chip(50, "$50"),
                    chip(75, "$75+"), unsure_chip()],
    },
    {
        "id": "specialist-copay", "path": "costSharing.specialistCopay",
        "section": "Costs", "input": "chips", "cast": "int", "optional": True,
        "prompt": "And a copay to see a specialist?",
        "why": "Specialists usually cost a bit more than a regular visit.",
        "options": [chip(0, "$0"), chip(25, "$25"), chip(50, "$50"),
                    chip(75, "$75+"), unsure_chip()],
    },
    {
        "id": "coinsurance", "path": "costSharing.coinsurancePct",
        "section": "Costs", "input": "chips", "cast": "int", "optional": True,
        "prompt": "After your deductible, what share do you pay?",
        "why": "Your part of the bill once the deductible is met.",
        "options": [chip(0, "0%"), chip(10, "10%"), chip(20, "20%"),
                    chip(30, "30%"), chip(40, "40%"), unsure_chip()],
    },
    {
        "id": "premium", "path": "costSharing.monthlyPremium",
        "section": "Costs", "input": "money", "optional": True,
        "prompt": "Roughly what do you pay each month?",
        "why": "Your monthly premium — a ballpark is fine.",
        "placeholder": "$450",
    },
    # --- HSA (only for HDHP / not sure) ---------------------------------
    {
        "id": "hsa-eligible", "path": "hsa.eligible",
        "section": "HSA", "input": "chips", "cast": "str", "optional": True,
        "prompt": "Do you have a Health Savings Account (HSA)?",
        "why": "HDHP plans often come with one — skip if this isn't you.",
        "options": [chip("yes", "Yes"), chip("no", "No"), unsure_chip()],
        "condition": lambda d: schema.get_path(d, "plan.planType") in (None, "HDHP"),
    },
    {
        "id": "hsa-balance", "path": "hsa.currentBalance",
        "section": "HSA", "input": "money", "optional": True,
        "prompt": "What's your current HSA balance?",
        "why": "Roughly is fine.",
        "placeholder": "$1,200",
        "condition": lambda d: schema.get_path(d, "hsa.eligible") == "yes",
    },
    {
        "id": "hsa-ytd", "path": "hsa.ytdContributions",
        "section": "HSA", "input": "money", "optional": True,
        "prompt": "How much have you contributed this year?",
        "why": "Your contributions so far in the current year.",
        "placeholder": "$500",
        "condition": lambda d: schema.get_path(d, "hsa.eligible") == "yes",
    },
    {
        "id": "hsa-employer", "path": "hsa.employerContribution",
        "section": "HSA", "input": "money", "optional": True,
        "prompt": "Does your employer contribute? How much this year?",
        "why": "Employer contributions count toward your total.",
        "placeholder": "$0",
        "condition": lambda d: schema.get_path(d, "hsa.eligible") == "yes",
    },
    # --- Prescriptions (repeatable, skippable) --------------------------
    {
        "id": "prescriptions", "path": "prescriptions", "section": "Prescriptions",
        "input": "prescriptions", "optional": True,
        "prompt": "Any medications you take regularly?",
        "why": "Add as many as you like, or skip — this is optional.",
    },
    # --- Upcoming care (skippable, gentle) ------------------------------
    {
        "id": "procedures", "path": "upcomingCare.plannedProcedures",
        "section": "Upcoming care", "input": "multiselect", "optional": True,
        "prompt": "Any procedures or care you're planning this year?",
        "why": "Helps us estimate bigger upcoming costs. Pick any that apply.",
        "options": [
            chip("imaging", "MRI / CT scan"), chip("surgery", "Surgery"),
            chip("physical_therapy", "Physical therapy"),
            chip("maternity", "Maternity care"), chip("dental", "Major dental"),
            chip("vision", "Vision / eye care"),
        ],
        "allow_other": True,
    },
    {
        "id": "conditions", "path": "upcomingCare.chronicConditions",
        "section": "Upcoming care", "input": "multiselect", "optional": True,
        "prompt": "Any ongoing conditions we should factor in?",
        "why": "Only if you're comfortable sharing — it improves the estimate.",
        "options": [
            chip("diabetes", "Diabetes"), chip("hypertension", "High blood pressure"),
            chip("asthma", "Asthma / COPD"), chip("heart", "Heart condition"),
            chip("arthritis", "Arthritis"), chip("thyroid", "Thyroid"),
        ],
        "allow_other": True,
    },
    {
        "id": "pregnancy", "path": "upcomingCare.pregnancy",
        "section": "Upcoming care", "input": "chips", "cast": "str", "optional": True,
        "prompt": "Are you expecting, or planning to be, this year?",
        "why": "Maternity care is a big cost driver — always okay to skip.",
        "options": [chip("yes", "Yes"), chip("no", "No"),
                    chip("prefer_not_to_say", "Prefer not to say")],
    },
    {
        "id": "behavioral", "path": "upcomingCare.behavioralHealthNeeds",
        "section": "Upcoming care", "input": "chips", "cast": "str", "optional": True,
        "prompt": "Do you expect to use behavioral or mental health care?",
        "why": "So your plan covers what matters. Always okay to skip.",
        "options": [chip("yes", "Yes"), chip("no", "No"),
                    chip("prefer_not_to_say", "Prefer not to say")],
    },
]

_BY_ID = {s["id"]: s for s in FLOW}


def _passes(step, data) -> bool:
    cond = step.get("condition")
    return True if cond is None else bool(cond(data))


def active_steps(data: dict) -> list[dict]:
    return [s for s in FLOW if _passes(s, data)]


def get_step(step_id: str):
    return _BY_ID.get(step_id)


def first_step_id(data: dict) -> str:
    return active_steps(data)[0]["id"]


def next_step_id(current_id: str, data: dict):
    ids = [s["id"] for s in active_steps(data)]
    if current_id not in ids:
        return None
    i = ids.index(current_id)
    return ids[i + 1] if i + 1 < len(ids) else None


def prev_step_id(current_id: str, data: dict):
    ids = [s["id"] for s in active_steps(data)]
    if current_id not in ids:
        return None
    i = ids.index(current_id)
    return ids[i - 1] if i > 0 else None


def progress(current_id: str, data: dict) -> int:
    """Honest progress: how far through the active steps we are (0–100)."""
    ids = [s["id"] for s in active_steps(data)]
    if current_id not in ids or not ids:
        return 0
    return int(round((ids.index(current_id)) / len(ids) * 100))
