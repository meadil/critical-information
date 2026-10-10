"""
Review boundary: apply Notion verdicts, then clean up both stores.

Model:
  Notion     = review inbox. Approved/rejected rows are removed once handled.
  Supabase   = trusted store. Only approved claims survive there.

What this script does with each Notion Status:
  approved        -> Supabase reviewed = true, notes recorded, Notion row DELETED
  rejected        -> Notion row DELETED, Supabase row DELETED (or archived, --archive)
  needs_research  -> Notion row DELETED, Supabase row DELETED (or archived, --archive)
  pending         -> left completely alone in both stores

`--archive` moves rejected / needs_research claims into the `claims_rejected`
table instead of destroying them, so you can re-review later. Off by default.

Usage:
  python notion_pull.py                  # apply everything
  python notion_pull.py --dry-run        # show the plan, change nothing
  python notion_pull.py --archive        # keep excluded claims for later
  python notion_pull.py --archive-create # build the archive table (run once)
"""

import argparse
import os
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
NOTION_KEY = os.environ.get("NOTION_API_KEY", "")
NOTION_DB = os.environ.get("NOTION_DATABASE_ID", "")

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"

# Supabase table that receives archived claims when --archive is on.
ARCHIVE_TABLE = "claims_rejected"

APPROVE = "approved"
EXCLUDE = {"rejected", "needs_research"}

ARCHIVE_DDL = """
create table if not exists public.claims_rejected (
  id                  uuid primary key,
  claim_text          text,
  source_title        text,
  source_author       text,
  source_type         text,
  source_location     text,
  claim_type          text,
  topic_tags          jsonb,
  verifiability       text,
  corroboration_count int,
  notes               text,
  rejected_at         timestamptz default now()
);
"""


def die(msg: str) -> None:
    raise SystemExit(f"ERROR: {msg}")


def sb_headers() -> dict:
    if not SUPABASE_URL or not SUPABASE_KEY:
        die("SUPABASE_URL / SUPABASE_KEY missing from .env")
    return {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}


def sb_write_headers() -> dict:
    return {**sb_headers(), "Content-Type": "application/json", "Prefer": "return=minimal"}


def notion_headers() -> dict:
    if not NOTION_KEY or not NOTION_DB:
        die("NOTION_API_KEY / NOTION_DATABASE_ID missing from .env")
    return {
        "Authorization": f"Bearer {NOTION_KEY}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


# --------------------------------------------------------------- Notion read

def fetch_rows() -> list[dict]:
    rows: list[dict] = []
    cursor = None
    while True:
        payload: dict = {"page_size": 100}
        if cursor:
            payload["start_cursor"] = cursor
        resp = requests.post(
            f"{NOTION_API}/databases/{NOTION_DB}/query",
            headers=notion_headers(), json=payload, timeout=30,
        )
        if resp.status_code >= 400:
            die(f"Notion query failed ({resp.status_code}): {resp.text}")
        data = resp.json()
        rows.extend(data.get("results", []))
        if not data.get("has_more"):
            return rows
        cursor = data.get("next_cursor")


def row_to_decision(row: dict) -> dict | None:
    props = row.get("properties", {})

    sb_id = ""
    for part in props.get("Supabase ID", {}).get("rich_text", []):
        sb_id = part.get("plain_text", "").strip()
    if not sb_id:
        return None

    status = (props.get("Status", {}).get("select") or {}).get("name", "pending")
    if status == "pending":
        return None

    title = props.get("Claim", {}).get("title", [])
    notes = ""
    for part in props.get("Notes", {}).get("rich_text", []):
        notes = part.get("plain_text", "")

    return {
        "notion_page_id": row["id"],
        "supabase_id": sb_id,
        "status": status,
        "preview": (title[0].get("plain_text", "") if title else "")[:70],
        "notes": notes,
    }


def delete_notion_row(page_id: str) -> str | None:
    """Archive the Notion page to trash so the review inbox stays short."""
    resp = requests.patch(
        f"{NOTION_API}/pages/{page_id}",
        headers={**notion_headers(), "Content-Type": "application/json"},
        json={"archived": True},
        timeout=30,
    )
    if resp.status_code >= 300:
        return f"({resp.status_code}) {resp.text[:160]}"
    return None


# ------------------------------------------------------------- Supabase work

def get_claim(sb_id: str) -> dict | None:
    resp = requests.get(
        f"{SUPABASE_URL}/rest/v1/claims",
        headers=sb_headers(),
        params={"id": f"eq.{sb_id}", "select": "id,reviewed,notes,claim_text"},
        timeout=30,
    )
    if resp.status_code >= 400:
        die(f"Supabase read failed ({resp.status_code}): {resp.text}")
    data = resp.json()
    return data[0] if data else None


def mark_approved(sb_id: str, notes: str) -> str | None:
    note = f"review: approved"
    if notes:
        note += f" | {notes[:500]}"
    resp = requests.patch(
        f"{SUPABASE_URL}/rest/v1/claims",
        headers=sb_write_headers(),
        params={"id": f"eq.{sb_id}"},
        json={"reviewed": True, "notes": note},
        timeout=30,
    )
    if resp.status_code >= 300:
        return f"({resp.status_code}) {resp.text[:160]}"
    return None


def archive_claim(sb_id: str) -> str | None:
    """Copy an excluded claim into the archive table, then delete the original."""
    claim = get_claim(sb_id)
    if claim is None:
        return None  # already gone; nothing to archive

    insert = requests.post(
        f"{SUPABASE_URL}/rest/v1/{ARCHIVE_TABLE}",
        headers=sb_write_headers(),
        json=[claim],
        timeout=30,
    )
    if insert.status_code >= 300:
        return f"archive insert failed ({insert.status_code}): {insert.text[:160]}"
    return delete_claim(sb_id)


def delete_claim(sb_id: str) -> str | None:
    resp = requests.delete(
        f"{SUPABASE_URL}/rest/v1/claims",
        headers=sb_write_headers(),
        params={"id": f"eq.{sb_id}"},
        timeout=30,
    )
    if resp.status_code >= 300:
        return f"({resp.status_code}) {resp.text[:160]}"
    return None


def archive_table_exists() -> bool:
    resp = requests.get(
        f"{SUPABASE_URL}/rest/v1/{ARCHIVE_TABLE}",
        headers=sb_headers(), params={"select": "id", "limit": 1}, timeout=30,
    )
    return resp.status_code < 400


# ------------------------------------------------------------------- driver

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--archive", action="store_true",
                        help="move excluded claims to claims_rejected instead of deleting them")
    parser.add_argument("--archive-create", action="store_true",
                        help="print the DDL for the archive table and exit")
    args = parser.parse_args()

    if args.archive_create:
        print("Run this in the Supabase SQL Editor:\n")
        print(ARCHIVE_DDL)
        return

    rows = fetch_rows()
    if not rows:
        print("Nothing in the Notion review inbox. Run /review first.")
        return

    decisions = [d for d in (row_to_decision(r) for r in rows) if d]
    pending = len(rows) - len(decisions)

    print(f"Notion inbox: {len(rows)} rows | decided: {len(decisions)} | pending: {pending}")

    if not decisions:
        print("No verdicts to apply — everything is still pending. Nothing changed.")
        return

    if args.archive and not archive_table_exists():
        die(f"--archive needs the {ARCHIVE_TABLE} table. Run with --archive-create first.")

    counts = {"approved": 0, "excluded": 0, "orphaned": 0, "errors": 0}
    failures: list[str] = []

    for d in decisions:
        if d["status"] == APPROVE:
            action = "approve"
        else:
            action = "exclude"

        if get_claim(d["supabase_id"]) is None:
            # Orphaned row: the Supabase claim is already gone (deleted by an
            # earlier pull, or never stored). Nothing left to apply, so clear it
            # from the inbox rather than failing the run forever.
            if args.dry_run:
                print(f"  [would clear orphaned Notion row] {d['preview']!r}")
            else:
                err = delete_notion_row(d["notion_page_id"])
                if err:
                    failures.append(f"  {d['preview']!r} -> orphan clear failed {err}")
                    counts["errors"] += 1
                    continue
                print(f"  [orphaned, cleared from Notion]    {d['preview']!r}")
            counts["orphaned"] += 1
            continue

        if args.dry_run:
            if action == "approve":
                print(f"  [would approve + clear from Notion] {d['preview']!r}")
            else:
                where = "archive" if args.archive else "DELETE from Supabase"
                print(f"  [would {where} + clear from Notion] {d['preview']!r}")
            counts["approved" if action == "approve" else "excluded"] += 1
            continue

        # 1. Notion is the inbox: once a verdict is applied, the row is done here.
        err = delete_notion_row(d["notion_page_id"])
        if err:
            failures.append(f"  {d['preview']!r} -> notion clear failed {err}")
            counts["errors"] += 1
            continue

        # 2. Apply the verdict in Supabase, the store that persists.
        if action == "approve":
            err = mark_approved(d["supabase_id"], d["notes"])
            if err:
                failures.append(f"  {d['preview']!r} -> approve failed {err}")
                counts["errors"] += 1
                continue
            print(f"  [approved, kept in Supabase]        {d['preview']!r}")
        else:
            err = archive_claim(d["supabase_id"]) if args.archive else delete_claim(d["supabase_id"])
            if err:
                failures.append(f"  {d['preview']!r} -> exclusion failed {err}")
                counts["errors"] += 1
                continue
            verb = "archived" if args.archive else "rejected"
            print(f"  [{verb}, removed from Supabase]    {d['preview']!r}")

        counts["approved" if action == "approve" else "excluded"] += 1

    verb_past = "would apply" if args.dry_run else "applied"
    print(
        f"\n{verb_past}: {counts['approved']} approved (kept in Supabase), "
        f"{counts['excluded']} excluded (removed from Supabase), "
        f"{counts['orphaned']} orphaned (cleared from Notion), "
        f"{pending} still pending in Notion."
    )
    if failures:
        print(f"\nFailed: {len(failures)}")
        for line in failures[:10]:
            print(line)


if __name__ == "__main__":
    main()
