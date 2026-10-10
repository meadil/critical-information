"""
Sync Supabase claims into the Notion review database.

Each claim becomes a Notion row with Status = pending, so a human can
approve/reject it before it is treated as trusted.

Re-running is safe: claims are matched on their Supabase id (stored in the
"Supabase ID" property), so a second run updates rather than duplicates.

Usage:
  python notion_sync.py                 # sync all claims with no Notion row
  python notion_sync.py --all           # re-sync every claim
  python notion_sync.py --limit 20
  python notion_sync.py --status approved  # only bring approved ones across
"""

import argparse
import os
import sys
import time
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

SELECT_CLAIM_TYPE = {
    "pain_point", "technique", "stat_data",
    "opinion", "definition", "contradiction_flag",
}
SELECT_VERIFIABILITY = {"evidence_given", "asserted_only"}
SELECT_SOURCE_TYPE = {
    "book", "podcast", "blog", "interview", "video", "article",
}

# Set from argv in main(). When False, already-reviewed claims are excluded so
# approved work never re-enters the Notion queue.
include_reviewed = False


def die(msg: str) -> None:
    raise SystemExit(f"ERROR: {msg}")


def supabase_headers() -> dict:
    if not SUPABASE_URL or not SUPABASE_KEY:
        die("SUPABASE_URL / SUPABASE_KEY missing from .env")
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
    }


def fetch_claims(limit: int | None, status: str | None) -> list[dict]:
    """Pull claims from Supabase, newest first.

    By default only UNREVIEWED claims are returned — a claim that has already
    been approved must not bounce back into the Notion inbox on every sync.
    Pass --include-reviewed to override (e.g. to deliberately re-review).
    """
    url = f"{SUPABASE_URL}/rest/v1/claims"
    params = {"select": "*", "order": "id", "reviewed": "eq.false" if not include_reviewed else None}
    params = {k: v for k, v in params.items() if v is not None}
    if status:
        params["supabase_status"] = f"eq.{status}"
    if limit:
        params["limit"] = str(limit)

    # PostgREST caps a response at 1000 rows by default and says so in
    # Content-Range, so a single request silently drops the rest. Page through
    # with a keyset cursor until the server says there is nothing more.
    rows: list[dict] = []
    cursor = None
    while True:
        page = {**params, "limit": 1000}
        if cursor:
            page["id"] = f"gt.{cursor}"
        resp = requests.get(url, headers=supabase_headers(), params=page, timeout=60)
        if resp.status_code >= 400:
            die(f"Supabase query failed ({resp.status_code}): {resp.text}")
        batch = resp.json()
        rows.extend(batch)
        if len(batch) < 1000:
            break
        cursor = batch[-1]["id"]
        if limit and len(rows) >= limit:
            break
    return rows[:limit] if limit else rows


def notion_headers() -> dict:
    if not NOTION_KEY or not NOTION_DB:
        die("NOTION_API_KEY / NOTION_DATABASE_ID missing from .env")
    return {
        "Authorization": f"Bearer {NOTION_KEY}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def existing_supabase_ids() -> set[str]:
    """Which Supabase claim ids already have a Notion row."""
    found: set[str] = set()
    cursor = None
    while True:
        payload: dict = {"page_size": 100, "filter": {"property": "Supabase ID", "rich_text": {"is_not_empty": True}}}
        if cursor:
            payload["start_cursor"] = cursor
        resp = requests.post(
            f"{NOTION_API}/databases/{NOTION_DB}/query",
            headers=notion_headers(), json=payload, timeout=30,
        )
        if resp.status_code >= 400:
            die(f"Notion query failed ({resp.status_code}): {resp.text}")
        data = resp.json()
        for row in data.get("results", []):
            for part in row["properties"]["Supabase ID"].get("rich_text", []):
                text = part.get("plain_text", "").strip()
                if text:
                    found.add(text)
        if not data.get("has_more"):
            return found
        cursor = data.get("next_cursor")


def safe_select(value: str | None, allowed: set[str], fallback: str) -> dict:
    if value and value in allowed:
        return {"name": value}
    return {"name": fallback}


def build_row(claim: dict) -> dict:
    """Map a Supabase claim onto Notion properties, tolerating missing columns."""
    claim_text = claim.get("claim_text") or claim.get("claim") or "(empty claim)"

    tags = claim.get("topic_tags")
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    elif not isinstance(tags, list):
        tags = []
    # Notion multi_select option names are capped at 100 chars.
    options = [{"name": t[:100]} for t in tags[:25]]

    props: dict = {
        "Claim": {"title": [{"text": {"content": claim_text[:1900]}}]},
        "Status": {"select": {"name": "pending"}},
        "Corroborations": {"number": claim.get("corroboration_count") or 1},
        "Supabase ID": {"rich_text": [{"text": {"content": str(claim.get("id", ""))}}]},
        "Source": {"rich_text": [{"text": {"content": (claim.get("source_title") or "")[:1900]}}]},
        "Author": {"rich_text": [{"text": {"content": (claim.get("source_author") or "")[:1900]}}]},
        "Notes": {"rich_text": []},
    }

    props["Claim Type"] = {"select": safe_select(
        claim.get("claim_type"), SELECT_CLAIM_TYPE, "opinion")}
    props["Verifiability"] = {"select": safe_select(
        claim.get("verifiability"), SELECT_VERIFIABILITY, "asserted_only")}
    props["Source Type"] = {"select": safe_select(
        claim.get("source_type"), SELECT_SOURCE_TYPE, "article")}

    if options:
        props["Topic Tags"] = {"multi_select": options}

    location = claim.get("source_location")
    # Notion rejects malformed URLs; only pass through ones that look like URLs.
    if location and str(location).startswith(("http://", "https://")):
        props["Source Location"] = {"url": str(location)}

    return {"parent": {"database_id": NOTION_DB}, "properties": props}


def push(claim: dict) -> str | None:
    """Create the Notion row. Returns an error string on failure, else None."""
    resp = requests.post(
        f"{NOTION_API}/pages",
        headers=notion_headers(), json=build_row(claim), timeout=30,
    )
    if resp.status_code == 429:
        # Notion rate-limits at roughly 3 requests/second. Back off instead of
        # recording a permanent failure for a row that would have been fine.
        wait = 2
        for _ in range(6):
            time.sleep(wait)
            wait *= 2
            retry = requests.post(
                f"{NOTION_API}/pages",
                headers=notion_headers(), json=build_row(claim), timeout=30,
            )
            if retry.status_code < 300:
                return None
            if retry.status_code != 429:
                return f"({retry.status_code}) {retry.text[:200]}"
        return "(429) rate limited, gave up after backoff"
    if resp.status_code < 300:
        return None
    return f"({resp.status_code}) {resp.text[:200]}"


def main() -> None:
    global include_reviewed
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--status", default=None,
                        help="only sync claims with this supabase_status value")
    parser.add_argument("--all", action="store_true",
                        help="re-sync claims that already have a Notion row too")
    parser.add_argument("--include-reviewed", action="store_true",
                        help="also push claims already marked reviewed (they will "
                             "normally re-enter the queue)")
    args = parser.parse_args()

    include_reviewed = args.include_reviewed
    claims = fetch_claims(args.limit, args.status)
    if not claims:
        print("No claims in Supabase yet — run /process on a source first.")
        return

    if args.all:
        already: set[str] = set()
    else:
        already = existing_supabase_ids()

    todo = [c for c in claims if str(c.get("id", "")) not in already]
    skipped = len(claims) - len(todo)
    print(f"Supabase claims: {len(claims)} | already in Notion: {skipped} | to push: {len(todo)}")

    if not todo:
        print("Nothing to do — every claim already has a Notion row.")
        return

    ok = 0
    failures: list[str] = []
    for i, claim in enumerate(todo, 1):
        err = push(claim)
        if err:
            text = (claim.get("claim_text") or claim.get("claim") or "")[:60]
            failures.append(f"  {text!r} -> {err}")
        else:
            ok += 1
            if ok % 5 == 0 or i == len(todo):
                print(f"  pushed {ok}/{len(todo)}")

    print(f"\nDone. Pushed {ok} claim(s) to Notion.")
    if failures:
        print(f"Failed: {len(failures)}")
        print("\n".join(failures[:10]))
        sys.exit(1)


if __name__ == "__main__":
    main()
