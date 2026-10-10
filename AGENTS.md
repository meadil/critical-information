# AGENTS.md — Critical Information project

Rules for any AI agent working in this folder. Read before changing anything.

**Read `learned.md` too** — it lists the bugs and traps in this project that
have already cost time. Do not repeat them.

---

## Supabase: explain all SQL before running it

This project has a `run_sql(text)` function that executes **arbitrary SQL** with
full database privileges. It can drop tables, wipe rows, and break the project.

**Mandatory rule: never call `run_sql` (or run DDL through any other path)
without first telling the user, in plain language, what the statement will do
and what it could break.**

- Write the intent in one or two short sentences, not a SQL dump.
- Say what it touches and whether it is reversible.
- For anything destructive (DROP, TRUNCATE, DELETE, ALTER on a live column),
  state plainly that it is destructive and confirm the user wants it.
- If the user asks for a change, that is not the same as having explained the
  SQL. Explain first, then run.

Examples of an acceptable explanation:

> This adds a `notes` column to the claims table. Existing rows get an empty
> value; nothing is deleted and it is easy to undo.

> This drops the `claims_rejected` archive table and all rows in it. Permanent —
> do you want that?

### Known limitation: `run_sql` returns `void`

It gives no rows back, so a `SELECT` through it returns nothing useful. To read
query results, create a view and read it over REST instead:

```
-- step 1 (via run_sql, after explaining)
create or replace view public.v_agent_sql as select ...;

-- step 2 (plain REST GET, no explanation needed — it only reads)
GET {SUPABASE_URL}/rest/v1/v_agent_sql?select=*
```

Views created this way are read-only and safe. Drop them when no longer needed
(explain that before dropping). `v_agent_sql` currently holds a claim-count
summary and can be refreshed at any time.

---

## Credentials

`.env` holds the Supabase **service_role** key, the Notion integration token,
and the n8n API key.

- The Supabase key **bypasses Row Level Security**. It can read, write, and
  delete anything. Never expose it to a browser, a client-side app, or any
  service that talks to the public internet.
- Never commit `.env`. It must be in `.gitignore` before this project is
  pushed to GitHub.
- Never paste a key into a file other than `.env`, and never echo one in output
  or logs. Refer to it as "the key in .env".

---

## The review loop has two stores, and the boundary matters

- **Notion is the review inbox.** It should only ever contain rows still
  awaiting a decision.
- **Supabase is the trusted store.** It should only contain approved claims.

| Notion Status  | Supabase                      | Notion    |
|----------------|-------------------------------|-----------|
| `approved`     | kept, `reviewed = true`       | removed   |
| `rejected`     | deleted (or archived)         | removed   |
| `needs_research` | deleted (or archived)       | removed   |
| `pending`      | untouched                     | stays     |

Do not let a claim re-enter the inbox after it has been approved.
`notion_sync.py` filters on `reviewed = false` for exactly this reason.

**Destructive by design:** a plain `/review-pull` deletes rejected and
`needs_research` claims from Supabase. Suggest `--dry-run` first when the user
is unsure, and mention `--archive` when a decision might be worth revisiting.

---

## Ways this breaks that have already bitten us

- **Never use `$pid`** in PowerShell — it is a reserved read-only variable.
- **PowerShell reads `.ps1` as ANSI, not UTF-8.** Keep scripts ASCII-only;
  em-dashes and smart quotes will break parsing.
- **Quoting inside `Start-Process -Command`** is fragile. Use the
  `Launch-Window` helper pattern in `start.ps1`.
- **PowerShell variable names can include `?`.** `"$t?select=id"` silently
  reads an empty variable — use `"${t}?select=id"`.
- **n8n container names are compose-prefixed.** The main one is `n8n-n8n-1`,
  not `n8n-1`. Detect by asking Docker which container publishes `:5678`.
- **Notion database views do not persist via the API.** Requests return 200 and
  nothing is saved. Do not try to automate them.
- **Notion property limits:** titles cap at 2000 chars, `url` properties reject
  anything that is not a valid URL. Both are handled in `notion_sync.py`.

---

## Before claiming something works

Verify it against the real service, not a script's return code. Two real bugs
in this project shipped "successfully" and were only caught by checking actual
Supabase/Notion state afterwards. When touching the review loop, confirm the
final row counts in both stores.

Never leave test data behind in the user's database.

---

## Project commands

From an opencode session started in this folder:

- `/start` — bring up Docker, n8n, the toolbox API, ngrok, activate the workflow
- `/stop` — tear it back down
- `/status` — report what is up, change nothing
- `/process <url|path>` — full ingest: extract, atomize, tag, store
- `/review` — push unreviewed Supabase claims to the Notion inbox
- `/review-pull [--dry-run|--archive]` — apply Notion verdicts, move the boundary

`PROJECT_STATUS.txt` is the handoff document and must be kept current.
