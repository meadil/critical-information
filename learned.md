# learned.md — lessons paid for the hard way

Bugs and traps in this project, each one found the expensive way. Read this
before touching the pipeline. Do not repeat these.

Format: what happened -> why -> the rule.

---

## 1. A script's return code is not evidence

**What happened.** `batch_ingest.py` ran all 26 chunks, every single one failed
with a connection error because the API server was down, and the script still
printed "Saved raw claims" and finished successfully in seconds. I reported it
as a healthy two-hour run in progress.

**Why.** The script had no preflight check and no way to distinguish "the server
is dead" from "this chunk is bad". It counted failures, logged them, and
exited 0.

**Rule.** Before a long run, verify the thing you depend on is actually up —
call the health endpoint and confirm, don't infer. After a run, verify the
*output* (row counts, files written), not the exit status. See also lesson 4.

---

## 2. Never hold results in memory across a long run

**What happened.** The first version of `batch_ingest.py` accumulated all
claims in a list and wrote to disk once, at the very end. Two hours of work
existed only inside the running process. A crash, reboot, or closed terminal
would have destroyed all of it with no way to recover.

**Why.** I wrote the happy path first and added persistence last, then never
came back to it.

**Rule.** Persist after every unit of work, and record a resume point. This
is not optional for anything long-running. When the run later aborted at chunk
12, `--resume` saved 805 claims and hours of compute.

---

## 3. Windows defaults will corrupt your text

**What happened.** `Predictable Revenue` extracted as
`Predictable Revenue�...`. Curly quotes and accented characters were destroyed
across a 270-page book. For a long time this was logged as "mojibake through
the n8n/FastAPI transport" — a completely wrong diagnosis that sent us
chasing a transport-layer bug that did not exist.

**Why.** `print()` encodes to cp1252 on Windows. The corruption happened at
the source, long before any transport.

**Rule.** `sys.stdout.reconfigure(encoding="utf-8", errors="replace")` at the
top of any script whose output another program reads. And when output looks
wrong, suspect the *producer* before the transport — I assumed the network
because the symptom appeared in a chain, not where it originated.

Related: PowerShell itself reads `.ps1` files as ANSI, not UTF-8, so keep
those ASCII-only. A non-ASCII em-dash made a script fail to parse entirely.

---

## 4. A clean-looking log can mean nothing happened

**What happened.** The failed run's log ended with a normal-looking summary
line. I read the tail, saw plausible text, and nearly reported success.
Checking the *per-chunk* lines revealed every chunk had errored.

**Why.** I looked at the end of a log instead of the middle. Summary lines
print regardless of what actually succeeded.

**Rule.** Count successes, don't read vibes. `ingest_status.json` exists
specifically so the agent can read a machine-checked answer (chunks done,
claims total, failure list) instead of parsing prose.

---

## 5. `subprocess` + `text=True` fails on a background thread

**What happened.** Roughly 1 chunk in 4 died with
`Exception in thread Thread-1 (_readerthread)`. It looked like the server was
down, and my abort message said so — confidently and wrongly.

**Why.** With `capture_output=True, text=True`, Python reads the child's pipes
on a helper thread and decodes there. One undecodable byte raises
`UnicodeDecodeError` in that thread, which cannot be caught at the call site
and takes down the whole subprocess call.

**Rule.** Capture raw bytes, decode yourself with `errors="replace"`. Also
check for an *empty* reply and raise — a silent empty result is worse than a
crash, because it looks like success.

---

## 6. Intermittent failures need retries, and honest error messages

**What happened.** My abort guard printed "the toolbox API is probably down."
The API was perfectly healthy. The flakiness was in the CLI call, and the
message sent me down the wrong path entirely.

**Why.** I wrote the message from a guess about a symptom I hadn't actually
traced, then read my own plausible story back as fact.

**Rule.** An error message should describe what the code *knows*, not what I
suspect. If the check was "3 chunks failed", the message is "3 chunks failed",
not a theory about the cause. And anything intermittent gets retries with
backoff — the underlying cause may still be a bug, but the pipeline should not
lose a two-hour run to a 25% flake rate.

---

## 7. Do not name a container or variable you have not verified

**What happened.** Container names in this project are `n8n-n8n-1` (compose
prefixes them), not the `n8n-1` I assumed. Separately, `stop.ps1` stopped all
five n8n-project containers while `start.ps1` only restarted one, so the stack
could not be brought back up. And `$pid` is a reserved PowerShell variable —
assigning to it silently failed.

**Why.** I wrote what the name *should* be instead of asking Docker.

**Rule.** Query for the real thing: `docker ps --filter publish=5678`, not a
hardcoded guess. Same for PowerShell — `$pid`, `$host`, `$error` and friends
are read-only. And when a script starts or stops a *set* of things, it must
handle the whole set, not the one that was convenient.

---

## 8. Query strings in PowerShell need `${var}`

**What happened.** `"$t?select=id"` read an empty variable, because
PowerShell allows `?` in variable names. A table check returned 404 and I
briefly believed a table was missing.

**Why.** Interpolated `$var?` greedily takes the `?` as part of the name.

**Rule.** Write `"${t}?select=id"`. Same trap for any adjacent character
PowerShell accepts in a name.

---

## 9. Notion's database-view API is a no-op

**What happened.** Creating filtered views returned HTTP 200 and the script
reported success. Re-reading the database showed no views saved.

**Why.** The API accepts and ignores the request for non-OAuth integrations.

**Rule.** Verify persistence by reading back. Don't build on this — add views
manually in the UI. Related: Notion rejects `url` properties that aren't
valid URLs, and caps title text at 2000 chars.

---

## 10. Think before deleting, especially the user's data

**What happened.** Review-pulling deletes rejected claims from Supabase *by
design*. That was the user's explicit instruction, and it is still the most
dangerous thing in this project.

**Why.** Not a bug — a design choice, but one that destroys data by default.

**Rule.** Destructive defaults get a dry-run, an `--archive` escape hatch, and
a plain-language warning before they run. And never leave test data in the
user's database: every test row created during this project was deleted
afterwards.

---

## 11. Explain before running SQL, and never claim a read-only result is safe

**What happened.** `run_sql` returns `void`, so a `SELECT` through it gives
back nothing — the natural next move is "wrap it in a view", which creates a
database object as a side effect of a *read* request.

**Why.** A read that appears harmless is still a write to the schema.

**Rule.** Explain every SQL statement's effect in plain language before
running it (see AGENTS.md). Creating a view to satisfy a read is still a
schema change and gets explained as one.

---

## Working rule that ties it all together

**Verify against the real service, every time, before telling the user
something works.** Three bugs in this project shipped "successfully": the
silent re-push of approved claims, the empty-text failure of the atomizer, and
the dead-server run in lesson 1. All three were caught only by checking actual
Supabase and Notion state afterward. Return codes and self-reported success
are not evidence.
