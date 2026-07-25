# Healthcare Simplified Onboarding

Emme's conversational "front door" for health-plan onboarding — a calm,
one-question-at-a-time flow that collects everything the pricing engine needs and
outputs clean, structured JSON.

**Built entirely in Python (Django).** No hand-written JavaScript — the whole
conversational flow is server-rendered, with state in a signed-cookie session.
See [`docs/adr/0001-emme-onboarding-architecture.md`](docs/adr/0001-emme-onboarding-architecture.md)
for the design and its rationale.

## What it does
- **Manual path** (works fully standalone): grouped, progressive questions with
  why-lines, forgiving input (`$2k` → `2000`, `blue cross` → `Blue Cross Blue Shield`),
  chips/steppers, honest progress bar, autosave, and skippable optional sections.
- **Upload path**: send an SBC/EOB photo or PDF to Gemini for extraction, then fall
  through to manual for anything unread. Degrades gracefully to manual if Gemini
  isn't configured.
- **Summary**: "Here's what we know about your plan" card + readiness indicator +
  **View / Download JSON** — the B2B unlock.

## Run it

```bash
python -m venv .venv
# Windows:  .venv\Scripts\activate
# macOS/Linux:  source .venv/bin/activate

pip install -r requirements.txt      # Django is required; google-genai only for uploads
python manage.py runserver
```

Open http://127.0.0.1:8000/. No database, no migrations — state lives in the session cookie.

### Optional: enable document extraction
Copy `.env.example` to `.env` and set `GEMINI_API_KEY`. Without it, the manual flow
(the demo-safe core) runs fully and uploads fall back to manual.

## Structure
```
emme/          Django project (settings, urls) — signed-cookie sessions, no DB
onboarding/    the single app
  flow.py      the conversational step machine (prompts, why-lines, inputs)
  forms.py     forgiving input normalization
  schema.py    the canonical JSON contract + document-merge rule
  gemini.py    server-side Gemini extraction (lazy-imported, optional)
  carriers.py  forgiving carrier matching ("did you mean?")
  templates/   server-rendered screens
  static/      one CSS file (chips/sliders are CSS-only)
docs/adr/      architecture decision record
```
