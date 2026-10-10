"""
Create the Notion claim-review database if it does not exist yet.

Requires a parent page that has been shared with the Notion integration.
Set NOTION_PARENT_PAGE_ID in .env to that page's ID.

Usage:
  python notion_setup.py
  python notion_setup.py --parent-page-id <id>

Prints the created/existing database id on success.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

API = "https://api.notion.com/v1"
VERSION = "2022-06-28"

# Mirrors the taxonomy the tagging model actually emits.
CLAIM_TYPES = [
    "pain_point", "technique", "stat_data",
    "opinion", "definition", "contradiction_flag",
]
VERIFIABILITY = ["evidence_given", "asserted_only"]
SOURCE_TYPES = ["book", "podcast", "blog", "interview", "video", "article"]
REVIEW_STATUS = ["pending", "approved", "rejected", "needs_research"]


def request(method: str, path: str, token: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        f"{API}{path}", data=data, method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Notion-Version": VERSION,
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        raise SystemExit(f"Notion API error {e.code}: {body}") from None


def select(name: str, options: list[str], color: str = "default") -> dict:
    return {
        "select": {
            "name": name,
            "options": [{"name": o, "color": color} for o in options],
        }
    }


def build_database(parent_id: str) -> dict:
    return {
        "parent": {"type": "page_id", "page_id": parent_id},
        "title": [{"type": "text", "text": {"content": "Critical Info - Claim Review"}}],
        "properties": {
            "Claim": {
                "title": {}
            },
            "Status": select("Status", REVIEW_STATUS),
            "Claim Type": select("Claim Type", CLAIM_TYPES),
            "Verifiability": select("Verifiability", VERIFIABILITY),
            "Topic Tags": {
                "multi_select": {"options": []}   # Notion grows this automatically
            },
            "Source": {"rich_text": {}},
            "Author": {"rich_text": {}},
            "Source Type": select("Source Type", SOURCE_TYPES),
            "Source Location": {"url": {}},
            "Corroborations": {"number": {"format": "number"}},
            "Notes": {"rich_text": {}},
            "Supabase ID": {"rich_text": {}},
        },
    }


def find_existing(token: str, parent_id: str) -> str | None:
    """Search for an already-created review database under the parent page."""
    result = request("POST", "/search", token, {
        "query": "Critical Info - Claim Review",
        "filter": {"value": "database", "property": "object"},
    })
    for item in result.get("results", []):
        parent = item.get("parent", {})
        if parent.get("page_id") == parent_id:
            return item["id"]
    return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parent-page-id")
    args = parser.parse_args()

    token = os.environ.get("NOTION_API_KEY", "").strip()
    if not token or not token.startswith("ntn_"):
        raise SystemExit("NOTION_API_KEY missing or malformed in .env")

    parent_id = (args.parent_page_id or os.environ.get("NOTION_PARENT_PAGE_ID", "")).strip()
    if not parent_id:
        raise SystemExit(
            "No parent page id.\n"
            "  1. In Notion, open (or create) a page to hold the review database.\n"
            "  2. ... > Connections > add your integration to that page.\n"
            "  3. Copy the page id from its URL (the 32-char hex after the last dash).\n"
            "  4. Re-run:  python notion_setup.py --parent-page-id <id>"
        )

    existing = find_existing(token, parent_id)
    if existing:
        print(f"Database already exists: {existing}")
        print(f"Add to .env:  NOTION_DATABASE_ID={existing}")
        return

    db = request("POST", "/databases", token, build_database(parent_id))
    db_id = db["id"]
    print(f"Created database: {db_id}")
    print(f"Add to .env:  NOTION_DATABASE_ID={db_id}")


if __name__ == "__main__":
    main()
