# Healthcare Simplified Onboarding

Emme's conversational "front door" for health-plan onboarding — a **Gemini-driven
chat** that collects everything the pricing engine needs and outputs clean,
structured JSON.

The chatbot logic (prompting, validation, extraction) lives in **Python (Django)**;
a small vanilla-JS widget renders the chat. See the architecture decisions in
[`docs/adr/0002-chat-interface.md`](docs/adr/0002-chat-interface.md) (chat) and
[`docs/adr/0001-emme-onboarding-architecture.md`](docs/adr/0001-emme-onboarding-architecture.md)
(data contract, extraction, provenance — still authoritative).

## What it does
- **Opening choice**: the bot asks whether to **import your SBC/EOB** or **just chat**.
  Once chatting, the import options live on the composer's **+** (attach) button.
- **Guided chat**: a system prompt (built from the field catalog) has Gemini ask for
  the needed fields one at a time, in plain language. Every answer is validated and
  coerced server-side (`$2k` → `2000`, `blue cross` → `Blue Cross Blue Shield`, chips
  to legal values), so the JSON stays clean no matter what the model emits.
- **Document-first**: attach an SBC/EOB and OCR/extraction fills fields first
  (SBC-wins / EOB-wins merge, with provenance); the bot then only asks for the rest.
- **Confirmation**: when all required fields are captured, the bot recaps and asks you
  to confirm before finishing.
- **Summary**: "Here's what we know about your plan" card + readiness indicator +
  **View / Download JSON** — the B2B unlock.

## Run it

```bash
python -m venv .venv
# Windows:  .venv\Scripts\activate
# macOS/Linux:  source .venv/bin/activate

pip install -r requirements.txt
python manage.py runserver
```

Open http://127.0.0.1:8000/. No database, no migrations — session state is stored in
files under `.sessions/` (big enough for a chat transcript).

### Set your Gemini key (required for the chat)
Copy `.env.example` to `.env` and set `GEMINI_API_KEY` (and optionally `GEMINI_MODEL`).
Without a key the app still loads, but the bot will politely ask you to add one.

## Structure
```
emme/          Django project (settings, urls) — file-based sessions, no DB
onboarding/    the single app
  chat.py      the Gemini chat engine: system prompt from the field catalog,
               per-turn JSON (reply + updates + phase), server-side validation
  flow.py      the field catalog (paths, prompts, valid option values) reused by chat
  gemini.py    server-side SBC/EOB vision extraction
  schema.py    the canonical JSON contract, doc-merge rule, per-field provenance
  carriers.py  forgiving carrier matching ("did you mean?")
  forms.py     value normalization (shared money/parse helpers)
  views.py     chat_home + chat_api + chat_upload (JSON) + summary/export
  templates/   chat.html (+ summary); static/ chat.js + styles.css
docs/adr/      architecture decision records (0001 data/extraction, 0002 chat)
```
