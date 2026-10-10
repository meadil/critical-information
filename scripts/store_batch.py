"""
Store many claims into Supabase, loading the embedding model ONCE.

Why this exists: store_claim.py loads SentenceTransformer at module import, so
every single claim reloaded the model (~2 min each). 3,000 claims would take
days. This script batches everything in one process: load the model, embed all
claims, then check for duplicates and insert.

Duplicate handling matches store_claim.py exactly: cosine similarity >= 0.85 via
the match_claims RPC bumps corroboration_count instead of inserting.

Usage:
  python store_batch.py claims_pending.json --title "T" --author "A" [--type book]
  python store_batch.py claims_pending.json --title "T" --author "A" --limit 50
  python store_batch.py claims_pending.json --title "T" --author "A" --dry-run
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

SIMILARITY_THRESHOLD = 0.85
BATCH = 200  # rows per insert

VALID_CLAIM_TYPES = {
    "pain_point", "technique", "stat_data",
    "opinion", "definition", "contradiction_flag",
}


def die(msg: str) -> None:
    raise SystemExit(f"ERROR: {msg}")


def headers() -> dict:
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_KEY", "")
    if not url or not key:
        die("SUPABASE_URL / SUPABASE_KEY missing from .env")
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }


def load_claims(path: str) -> tuple[list[dict], list[dict]]:
    """Read claims and split out the malformed ones."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    good, dropped = [], []
    for c in data:
        if not isinstance(c, dict):
            dropped.append(c)
            continue
        text, ctype = c.get("claim_text"), c.get("claim_type")
        # The tagger sometimes emits claim_text that is really a type name.
        if not isinstance(text, str) or not text.strip() or text.strip() == ctype:
            dropped.append(c)
            continue
        if ctype not in VALID_CLAIM_TYPES:
            dropped.append(c)
            continue
        good.append(c)
    return good, dropped


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("claims_file")
    ap.add_argument("--title", required=True)
    ap.add_argument("--author", default="")
    ap.add_argument("--type", default="book")
    ap.add_argument("--location", default="")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--resume", action="store_true",
                    help="skip claims already present in Supabase, so an "
                         "interrupted run can continue where it stopped")
    args = ap.parse_args()

    claims, dropped = load_claims(args.claims_file)
    if args.limit:
        claims = claims[: args.limit]

    print(f"Loaded {len(claims)} valid claim(s), dropping {len(dropped)} malformed")
    if dropped:
        print(f"  e.g. {json.dumps(dropped[0], ensure_ascii=False)[:120]}")
    if not claims:
        return

    if args.dry_run:
        print("Dry run, nothing written. Sample:")
        for c in claims[:5]:
            print(f"  [{c['claim_type']}] {c['claim_text'][:100]}")
        return

    base = os.environ["SUPABASE_URL"].rstrip("/")
    hdr = headers()

    # Resume support: skip claims already present in Supabase. Without this,
    # a network drop partway through means redoing every dedup check, and the
    # prior run buffered inserts until the very end and lost all its work.
    existing: set[str] = set()
    if args.resume:
        print("Loading existing claim texts for resume...")
        cursor = None
        while True:
            # Order by id for a stable keyset cursor; claim_text alone is not unique.
            params = {"select": "id,claim_text", "order": "id", "limit": 1000}
            if cursor:
                params["id"] = f"gt.{cursor}"
            resp = requests.get(f"{base}/rest/v1/claims", headers=hdr, params=params, timeout=120)
            if resp.status_code >= 400:
                die(f"could not read existing claims ({resp.status_code}): {resp.text[:200]}")
            rows = resp.json()
            for r in rows:
                if r.get("claim_text"):
                    existing.add(r["claim_text"])
            if len(rows) < 1000:
                break
            cursor = rows[-1]["id"]
        print(f"  {len(existing)} claim(s) already in Supabase")

    print("Loading embedding model (one time)...")
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer("all-MiniLM-L6-v2")

    print("Embedding claims...")
    started = time.time()
    texts = [c["claim_text"] for c in claims]
    embeddings = model.encode(texts, batch_size=64, show_progress_bar=False)
    print(f"  embedded {len(embeddings)} claims in {time.time() - started:.0f}s\n")

    new_rows: list[dict] = []
    merged = 0
    skipped = 0
    for i, (c, emb) in enumerate(zip(claims, embeddings), 1):
        if args.resume and c["claim_text"] in existing:
            skipped += 1
            continue
        tags = c.get("topic_tags") or []
        if not isinstance(tags, list):
            tags = [t.strip() for t in str(tags).split(",") if t.strip()]

        # Duplicate check via the same RPC store_claim.py uses.
        match = requests.post(
            f"{base}/rest/v1/rpc/match_claims",
            headers=hdr,
            json={
                "query_embedding": emb.tolist(),
                "match_threshold": SIMILARITY_THRESHOLD,
                "match_count": 1,
            },
            timeout=60,
        )
        if match.status_code >= 400:
            die(f"match_claims failed ({match.status_code}): {match.text[:200]}")

        found = match.json()
        if found:
            old = found[0]
            count = (old.get("corroboration_count") or 0) + 1
            upd = requests.patch(
                f"{base}/rest/v1/claims",
                headers={**hdr, "Prefer": "return=minimal"},
                params={"id": f"eq.{old['id']}"},
                json={"corroboration_count": count},
                timeout=60,
            )
            if upd.status_code >= 300:
                print(f"  bump failed for {old['id'][:8]}: {upd.status_code}")
            else:
                merged += 1
        else:
            new_rows.append({
                "claim_text": c["claim_text"],
                "claim_type": c["claim_type"],
                "topic_tags": tags,
                "verifiability": c.get("verifiability", "asserted_only"),
                "source_title": args.title,
                "source_author": args.author,
                "source_type": args.type,
                "source_location": args.location or Path(args.claims_file).stem,
                "embedding": emb.tolist(),
            })

        # Flush to the database as we go. Buffering every insert until the end
        # means a dropped connection throws away the whole run.
        if len(new_rows) >= BATCH:
            inserted, failed_rows = flush(base, hdr, new_rows)
            if failed_rows:
                print(f"  !! {failed_rows} row(s) failed to insert")
            new_rows = []

        if i % 100 == 0 or i == len(claims):
            print(f"  checked {i}/{len(claims)} (merged={merged} skipped={skipped})",
                  flush=True)

    if new_rows:
        print(f"\nFlushing final {len(new_rows)} row(s)...")
        inserted, failed_rows = flush(base, hdr, new_rows)
        new_rows = []

    print(f"\nDone. merged={merged} skipped={skipped} dropped={len(dropped)}")


def flush(base: str, hdr: dict, rows: list[dict]) -> tuple[int, int]:
    """Insert a batch of rows. Returns (inserted, failed)."""
    resp = requests.post(
        f"{base}/rest/v1/claims",
        headers={**hdr, "Prefer": "return=minimal"},
        json=rows,
        timeout=300,
    )
    if resp.status_code >= 300:
        print(f"  insert batch FAILED ({resp.status_code}): {resp.text[:200]}")
        return 0, len(rows)
    print(f"  inserted {len(rows)} (running total in DB grows)", flush=True)
    return len(rows), 0


if __name__ == "__main__":
    main()
