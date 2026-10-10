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
import subprocess
import sys

# --- EDIT THESE ---
ATOMIZE_MODEL = "your-provider/muse-spark-1.3"
TAG_MODEL = "your-provider/nemotron-3-ultra"
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


def call_opencode(model: str, prompt: str) -> str:
    result = subprocess.run(
        ["opencode", "run", "-m", model, prompt],
        capture_output=True, text=True,
        shell=(os.name == "nt")  # Windows needs the shell to resolve opencode's .cmd wrapper;
                                  # Python still quotes each argument correctly when the list form
                                  # is combined with shell=True on Windows, so this is safe.
    )
    if result.returncode != 0:
        raise RuntimeError(f"opencode run failed: {result.stderr}")
    return result.stdout.strip()


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
    main()
