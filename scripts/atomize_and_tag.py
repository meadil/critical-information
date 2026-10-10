"""
Atomize raw text into individual claims, then tag each one.
Reads raw text from stdin. Prints a JSON array of tagged claim objects to stdout.

Usage:
  python atomize_and_tag.py --title "..." --author "..." --type "..." --location "..." < text.txt

Requires OpenCode installed and on PATH.
EDIT the two model IDs below to match what your OpenCode setup actually calls them
(run `opencode models` or check the model picker to get the exact string).
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

# --- EDIT THESE ---
ATOMIZE_MODEL = "opencode/muse-spark-1.3-contributor-free"
TAG_MODEL = "opencode/nemotron-3-ultra-free"  # still needs your exact ID, see below
# -------------------

ATOMIZE_PROMPT = """You are extracting atomic, single-idea claims from the text below. A claim is one self-contained idea, technique, pain point, statistic, or definition -- not a whole paragraph.

Rules:
- Paraphrase each claim in your own words. Do not copy sentences verbatim.
- One idea per claim.
- Include anything even loosely relevant. Do not filter for importance yet.
- Output ONLY a JSON array of strings. No markdown fences, no explanation, nothing else.

Text:
\"\"\"
{text}
\"\"\"
"""

TAG_PROMPT = """For each claim below, classify it. Output a JSON array of objects, one per claim, with exactly these fields:
- "claim_text": the claim, unchanged
- "claim_type": one of "pain_point", "technique", "stat_data", "opinion", "definition", "contradiction_flag"
- "topic_tags": an array of 1 to 4 short lowercase topic tags
- "verifiability": one of "evidence_given", "asserted_only"

Output ONLY the JSON array. No markdown fences, no explanation.

Claims:
{claims_json}
"""


def find_opencode_bin() -> str:
    """Resolve the real opencode binary.

    On Windows npm installs a .cmd wrapper. Running it via shell=True
    mangles multi-line prompts (triple-quotes/newlines), so the model
    sees 'No text was included'. Bypass the wrapper by calling
    opencode.exe directly with shell=False.
    """
    wrapper = shutil.which("opencode")
    if os.name == "nt" and wrapper:
        npm_dir = Path(wrapper).parent
        candidate = npm_dir / "node_modules" / "opencode-ai" / "bin" / "opencode.exe"
        if candidate.exists():
            return str(candidate)
        alt = npm_dir / "opencode.exe"
        if alt.exists():
            return str(alt)
    return wrapper or "opencode"


_OPENCODE_BIN = find_opencode_bin()


def call_opencode(model: str, prompt: str) -> str:
    # Capture raw bytes and decode manually. With text=True, Python reads the
    # child's pipes on a background thread and a single undecodable byte
    # raises UnicodeDecodeError in that thread, which kills the whole call
    # (observed as "Exception in thread Thread-1 (_readerthread)", roughly
    # 1 run in 4). errors="replace" makes stray bytes harmless instead.
    result = subprocess.run(
        [_OPENCODE_BIN, "run", "-m", model, "--format", "json", prompt],
        capture_output=True,
        shell=False,
    )
    stdout = result.stdout.decode("utf-8", errors="replace")
    stderr = result.stderr.decode("utf-8", errors="replace")

    if result.returncode != 0:
        raise RuntimeError(f"opencode run failed (exit {result.returncode}): {stderr}")

    # --format json prints one JSON event per line. The model's actual reply
    # lives inside "type":"text" events, in part.text -- pull those out and
    # stitch them together in order (some models stream in multiple chunks).
    text_parts = []
    for line in stdout.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue  # skip any stray non-JSON line
        if event.get("type") == "text":
            text_parts.append(event.get("part", {}).get("text", ""))

    reply = "".join(text_parts)
    if not reply.strip():
        raise RuntimeError(
            f"opencode returned no text for model {model}. stderr: {stderr[:300]}"
        )
    return reply


def parse_json_loose(raw: str):
    """Models sometimes wrap JSON in markdown fences despite instructions -- strip those if present."""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--title", default="")
    parser.add_argument("--author", default="")
    parser.add_argument("--type", default="")
    parser.add_argument("--location", default="")
    args = parser.parse_args()

    text = sys.stdin.read()
    if not text.strip():
        print(json.dumps([]))
        return

    atomize_raw = call_opencode(ATOMIZE_MODEL, ATOMIZE_PROMPT.format(text=text))
    claims = parse_json_loose(atomize_raw)

    tag_raw = call_opencode(TAG_MODEL, TAG_PROMPT.format(claims_json=json.dumps(claims)))
    tagged = parse_json_loose(tag_raw)

    for c in tagged:
        c["source_title"] = args.title
        c["source_author"] = args.author
        c["source_type"] = args.type
        c["source_location"] = args.location

    print(json.dumps(tagged))


if __name__ == "__main__":
    # Force UTF-8 so claim text containing smart quotes or accented characters
    # is not mangled by Windows' default cp1252 stdout encoding.
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    main()
