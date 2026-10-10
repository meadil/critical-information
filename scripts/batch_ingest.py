"""
Batch-ingest a long book through the running toolbox API.

Splits the extracted text into page-aware chunks, runs atomize_and_tag on each,
then stores every claim via store_claim. Deduplicates by Supabase id at the end.

Usage:
  python batch_ingest.py <pdf_path> <title> <author> [source_type] [--dry-run]
"""

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

import requests

API = "http://localhost:8000"
CHUNK_CHARS = 12000
RETRIES = 3

# Must match the taxonomy in atomize_and_tag.py's TAG_PROMPT, and the
# CLAIM_TYPES list in notion_sync.py.
VALID_CLAIM_TYPES = {
    "pain_point", "technique", "stat_data",
    "opinion", "definition", "contradiction_flag",
}


def post(endpoint: str, payload: dict, timeout: int = 900) -> dict:
    resp = requests.post(f"{API}/{endpoint}", json=payload, timeout=timeout)
    if resp.status_code >= 400:
        raise RuntimeError(f"{endpoint} failed ({resp.status_code}): {resp.text[:300]}")
    return resp.json()


PROGRESS_PATH = Path(__file__).parent.parent / "claims_pending.json"


def save_progress(args, chunks, claims, failures, done: int) -> None:
    """Write claims + a resume point to disk after every chunk.

    Claims otherwise live only in memory until the final chunk, so a crash or
    reboot would discard the entire run.
    """
    PROGRESS_PATH.write_text(
        json.dumps(claims, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    marker = Path(__file__).parent.parent / "ingest_status.json"
    marker.write_text(json.dumps({
        "status": "running" if done < len(chunks) else "atomized",
        "source": args.pdf,
        "title": args.title,
        "author": args.author,
        "source_type": args.type,
        "chunks_total": len(chunks),
        "chunks_done": done,
        "next_chunk": done,
        "chunks_failed": len(failures),
        "claims_so_far": len(claims),
        "failures": failures,
        "dry_run": args.dry_run,
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }, indent=2), encoding="utf-8")


def wait_for_api(timeout: int = 30) -> None:
    """Refuse to start unless the toolbox API is actually up.

    Without this, a dead API turns every chunk into an instant connection
    error and the run "completes" in seconds having processed nothing.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            requests.get(f"{API}/docs", timeout=5)
            return
        except requests.RequestException:
            time.sleep(2)
    raise SystemExit(
        f"ERROR: toolbox API is not answering on {API}.\n"
        "Start the stack first:  powershell -NoProfile -ExecutionPolicy Bypass -File .\\start.ps1"
    )


def extract(pdf: str) -> str:
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).parent / "extract_pdf.py"), pdf],
        capture_output=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"extract failed: {proc.stderr.decode('utf-8', 'replace')[:300]}")
    return proc.stdout.decode("utf-8", "replace").replace("\r\n", "\n")


def to_chunks(text: str) -> list[str]:
    """Group pages into chunks under CHUNK_CHARS, keeping page markers."""
    parts = re.split(r"--- page (\d+) ---\n", text)
    pages: list[str] = []
    for i in range(1, len(parts), 2):
        body = parts[i + 1].strip()
        if body:
            pages.append(f"[p.{parts[i]}] {body}")

    chunks: list[str] = []
    cur: list[str] = []
    size = 0
    for page in pages:
        if cur and size + len(page) > CHUNK_CHARS:
            chunks.append("\n".join(cur))
            cur, size = [], 0
        cur.append(page)
        size += len(page)
    if cur:
        chunks.append("\n".join(cur))
    return chunks


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("title")
    ap.add_argument("author")
    ap.add_argument("--type", default="book")
    ap.add_argument("--dry-run", action="store_true",
                    help="atomize only, do not store anything")
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--end", type=int, default=None)
    ap.add_argument("--workers", type=int, default=1,
                    help="chunks to atomize concurrently (BROKEN: atomize_and_tag "
                         "shells out to the opencode CLI, which cannot run "
                         "concurrent sessions. Keep at 1.)")
    ap.add_argument("--resume", action="store_true",
                    help="continue from a previous interrupted run")
    ap.add_argument("--store-only", action="store_true",
                    help="skip atomizing entirely and store the claims already "
                         "in claims_pending.json (e.g. after a --dry-run pass)")
    args = ap.parse_args()

    wait_for_api()

    all_claims: list[dict] = []
    failures: list[str] = []
    failed_indices: set[int] = set()
    start = args.start

    if args.store_only:
        if not PROGRESS_PATH.exists():
            raise SystemExit(f"ERROR: {PROGRESS_PATH.name} not found. "
                             "Run a --dry-run pass first.")
        all_claims = json.loads(PROGRESS_PATH.read_text(encoding="utf-8"))
        print(f"Store-only: loaded {len(all_claims)} claims from "
              f"{PROGRESS_PATH.name}, skipping atomize.\n")
        return store_all(args, all_claims)

    if args.resume:
        marker = Path(__file__).parent.parent / "ingest_status.json"
        if marker.exists():
            info = json.loads(marker.read_text(encoding="utf-8"))
            if info.get("source") == args.pdf:
                start = int(info.get("next_chunk", 0))
                failures = info.get("failures", [])
                print(f"Resuming after chunk {start} of {info.get('chunks_total')}")
            else:
                print("Status file is for a different source; starting fresh.")
        else:
            print("No status file to resume from; starting fresh.")

    print(f"Extracting {args.pdf}")
    text = extract(args.pdf)
    chunks = to_chunks(text)
    print(f"{len(chunks)} chunks from {len(text):,} characters\n")

    if start > 0 and PROGRESS_PATH.exists():
        try:
            all_claims = json.loads(PROGRESS_PATH.read_text(encoding="utf-8"))
            print(f"Restored {len(all_claims)} claims from a previous run.\n")
        except (json.JSONDecodeError, OSError):
            all_claims = []

    # NOTE: concurrency is intentionally not supported. atomize_and_tag shells
    # out to the opencode CLI, which cannot run concurrent sessions -- parallel
    # attempts fail with reader-thread errors. --workers is kept only so the
    # failure is documented rather than silently mis-parallelised.
    if args.workers > 1:
        print("--workers is not supported (the opencode CLI cannot run "
              "concurrent sessions). Falling back to serial.\n")

    for idx in range(start, args.end if args.end is not None else len(chunks)):
        chunk = chunks[idx]
        print(f"[{idx + 1}/{len(chunks)}] atomizing {len(chunk):,} chars ...", flush=True)
        started = time.time()
        try:
            res = post("atomize_and_tag", {
                "text": chunk,
                "source_title": args.title,
                "source_author": args.author,
                "source_type": args.type,
                "source_location": Path(args.pdf).name,
            })
        except Exception as e:
            failures.append(f"chunk {idx}: {e}")
            failed_indices.add(idx)
            print(f"    FAILED: {e}")
            if len(failures) >= 3:
                print("\nABORTING: 3+ chunk failures. The toolbox API or the "
                      "opencode CLI is likely down. Restart the stack, then "
                      "re-run with --resume to continue from the last good chunk.")
                break
            continue

        raw = (res.get("stdout") or "").strip()
        if res.get("returncode") != 0 or not raw:
            err = (res.get("stderr") or "")[:300]
            # The opencode CLI call is intermittently flaky (a subprocess
            # reader-thread decode error), so retry before giving up.
            print(f"    attempt failed (rc={res.get('returncode')}), retrying ...", flush=True)
            retried = False
            for attempt in range(RETRIES):
                time.sleep(5 * (attempt + 1))
                try:
                    res = post("atomize_and_tag", {
                        "text": chunk,
                        "source_title": args.title,
                        "source_author": args.author,
                        "source_type": args.type,
                        "source_location": Path(args.pdf).name,
                    })
                    raw = (res.get("stdout") or "").strip()
                    if res.get("returncode") == 0 and raw:
                        print(f"    retry {attempt + 1} succeeded", flush=True)
                        retried = True
                        break
                    err = (res.get("stderr") or "")[:300]
                except Exception as e:
                    err = str(e)[:300]

            if not retried:
                failures.append(f"chunk {idx}: {err}")
                failed_indices.add(idx)
                print(f"    FAILED after {RETRIES} retries: {err}")
                save_progress(args, chunks, all_claims, failures, done=idx)
                if len(failures) >= 3:
                    print("\nABORTING: 3+ chunks failed even after retries. "
                          "Check that the stack is up, then re-run with --resume "
                          "to continue from the last good chunk.")
                    break
                continue

        try:
            claims = json.loads(raw)
        except json.JSONDecodeError:
            claims = json.loads(re.sub(r"^```(?:json)?|```$", "", raw, flags=re.M).strip())

        elapsed = time.time() - started
        print(f"    {len(claims)} claims in {elapsed:.0f}s")
        all_claims.extend(claims)
        # Persist after every chunk. Claims live in memory until the very end
        # otherwise, so a crash or reboot would discard the whole run.
        # `done` is the number of chunks fully finished, which is also exactly
        # where a --resume should pick up.
        save_progress(args, chunks, all_claims, failures, done=idx + 1)

    print(f"\nAtomized: {len(all_claims)} claims total, {len(failures)} chunk failure(s)")
    if failures:
        for f in failures:
            print(f"  {f}")

    # Record the true resume point. Chunk indices come from actual successful
    # output, NOT from the failure list -- a chunk that failed once and
    # succeeded on retry is complete, and must not be re-run on --resume.
    # Gaps between 0 and the highest successful index are incomplete chunks.
    succeeded = {i for i in range(start, len(chunks)) if i not in failed_indices}
    done = len(chunks)
    missing = [i for i in range(len(chunks)) if i not in succeeded]
    if missing:
        done = min(missing)

    print(f"Saved raw claims to {PROGRESS_PATH.name}")
    print(f"Chunks succeeded: {len(succeeded)}/{len(chunks)}"
          + (f" (incomplete: {missing})" if missing else ""))
    save_progress(args, chunks, all_claims, failures, done=done)

    if args.dry_run:
        print("\n--dry-run: not storing. First 3 claims:")
        for c in all_claims[:3]:
            print(f"  [{c.get('claim_type')}] {c.get('claim_text', '')[:110]}")
        return

    store_all(args, all_claims)


def store_all(args, all_claims: list[dict]) -> None:
    """Filter and store every claim into Supabase via the toolbox API."""
    # Drop malformed claims before storing. The tagger occasionally emits a
    # stray object where claim_text is actually a type name and the real text
    # is missing, which would create an empty row in the knowledge base.
    good, dropped = [], []
    for c in all_claims:
        if not isinstance(c, dict):
            dropped.append(repr(c)[:80])
            continue
        text = c.get("claim_text")
        ctype = c.get("claim_type")
        if not isinstance(text, str) or not text.strip() or text.strip() == ctype:
            dropped.append(repr(c)[:80])
            continue
        if ctype not in VALID_CLAIM_TYPES:
            dropped.append(repr(c)[:80])
            continue
        good.append(c)

    print(f"\nFiltered {len(dropped)} malformed claim(s); storing {len(good)} ...")
    for d in dropped[:5]:
        print(f"  dropped: {d}")

    stored = merged = failed = 0
    for i, c in enumerate(good, 1):
        tags = c.get("topic_tags") or []
        if isinstance(tags, list):
            tags = ",".join(tags)
        try:
            res = post("store_claim", {
                "claim_text": c.get("claim_text", ""),
                "claim_type": c.get("claim_type", "opinion"),
                "topic_tags": tags,
                "verifiability": c.get("verifiability", "asserted_only"),
                "source_title": args.title,
                "source_author": args.author,
                "source_type": args.type,
                "source_location": Path(args.pdf).name,
            }, timeout=300)
            if res.get("returncode") != 0:
                failed += 1
                if failed <= 5:
                    print(f"  FAILED: {(res.get('stderr') or '')[:200]}")
            else:
                out_txt = (res.get("stdout") or "").lower()
                if "merged" in out_txt or "corroboration" in out_txt:
                    merged += 1
                else:
                    stored += 1
        except Exception as e:
            failed += 1
            if failed <= 5:
                print(f"  FAILED: {e}")
        if i % 25 == 0 or i == len(good):
            print(f"  {i}/{len(good)}  (new={stored} merged={merged} failed={failed})",
                  flush=True)

    print(f"\nDone. new={stored} merged={merged} failed={failed} dropped={len(dropped)}")


if __name__ == "__main__":
    main()
