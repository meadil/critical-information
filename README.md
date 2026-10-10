# Critical Information

**Turn books, podcasts, and articles into a searchable database of single, usable ideas.**

Most note-taking apps save whole chapters. This splits them into individual
**claims** — one idea per line — tags each one, and stores them in a database you
can query later.

---

## The problem it solves

Reading 10 business books gives you 10 books' worth of scattered highlights.
Six months later you can't find the one sentence that answered your question.

This turns *Predictable Revenue* into ~2,700 separate claims, each with:
- the idea, on its own
- a source (book, page)
- a type tag (`pain_point`, `tactic`, `framework`, `warning`…)
- a topic tag
- a verifiability score

Then you can ask: *"every pain point mentioned about cold email"* — and get a
clean list instead of hunting through highlights.

---

## How it works

```
PDF / podcast / URL
      ↓
  extract text
      ↓
  split into single-idea claims      ← the core idea
      ↓
  tag each claim (type, topic, source)
      ↓
  Notion  ← you review and approve
      ↓
  Supabase  ← approved claims stored here
```

**The review loop:** nothing reaches your database without you approving it.
Claims land in Notion first. Approve → it goes to the database and leaves Notion.
Reject or "needs research" → it's deleted. Pending → it stays for later.

---

## Getting started

```bash
pip install -r requirements.txt
copy .env.example .env    # then fill in your keys
./start.ps1               # Windows: starts the local API server
```

You need a `.env` with:
- `SUPABASE_URL` + `SUPABASE_SERVICE_KEY` — where claims are stored
- `NOTION_TOKEN` — the review inbox
- `N8N_API_KEY` — optional, only if you wire it to n8n

See `.env.example` for the full list.

---

## What's in here

| File | What it does |
|------|--------------|
| `toolbox_api.py` | The local API — every tool the agent calls lives here |
| `scripts/extract_pdf.py` | Pull text out of a PDF |
| `scripts/atomize_and_tag.py` | **The core.** Splits text into claims and tags them |
| `scripts/batch_ingest.py` | Runs a whole folder of sources at once |
| `scripts/notion_sync.py` | Pushes claims to Notion for review |
| `scripts/notion_pull.py` | Reads your decisions back from Notion |
| `scripts/transcribe.py` | Turns audio/video into text (Whisper) |
| `n8n_critical_info_toolbox.json` | n8n workflow exposing these tools to an AI agent |
| `learned.md` | Bugs already hit and fixed — **read this before changing anything** |
| `AGENTS.md` | Rules for AI agents working in this repo |
| `PROJECT_STATUS.txt` | Full technical handoff document |

---

## Notes

- Status: ~90% complete. The core loop works end to end.
- Raw claims output (`claims_pending.json`) and logs are gitignored — they're
  generated, not source.
- `.env` is gitignored. Never commit keys.

---

Built as part of learning to separate **standard information** (context) from
**critical information** (actually solves your problem).