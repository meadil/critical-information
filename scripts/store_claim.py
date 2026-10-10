"""
Store a claim: check for a near-duplicate first, then either bump
corroboration_count on the existing match or insert a new row.

Requires environment variables:
  SUPABASE_URL, SUPABASE_KEY  (use the service_role key -- this script writes)
"""
import argparse
import os
import sys
from supabase import create_client
from sentence_transformers import SentenceTransformer

MODEL = SentenceTransformer("all-MiniLM-L6-v2")
SIMILARITY_THRESHOLD = 0.85


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--claim", required=True)
    parser.add_argument("--claim-type", default="")
    parser.add_argument("--tags", default="")
    parser.add_argument("--verifiability", default="")
    parser.add_argument("--source-title", default="")
    parser.add_argument("--source-author", default="")
    parser.add_argument("--source-type", default="")
    parser.add_argument("--source-location", default="")
    args = parser.parse_args()

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    if not url or not key:
        print("Error: SUPABASE_URL and SUPABASE_KEY must be set as environment variables", file=sys.stderr)
        sys.exit(1)

    supabase = create_client(url, key)
    embedding = MODEL.encode(args.claim).tolist()

    match = supabase.rpc("match_claims", {
        "query_embedding": embedding,
        "match_threshold": SIMILARITY_THRESHOLD,
        "match_count": 1
    }).execute()

    if match.data:
        existing = match.data[0]
        new_count = existing["corroboration_count"] + 1
        supabase.table("claims").update({"corroboration_count": new_count}).eq("id", existing["id"]).execute()
        print(f"Duplicate found (similarity {existing['similarity']:.2f}). Bumped corroboration_count to {new_count} on claim {existing['id']}.")
    else:
        tags_list = [t.strip() for t in args.tags.split(",") if t.strip()]
        result = supabase.table("claims").insert({
            "claim_text": args.claim,
            "claim_type": args.claim_type,
            "topic_tags": tags_list,
            "verifiability": args.verifiability,
            "source_title": args.source_title,
            "source_author": args.source_author,
            "source_type": args.source_type,
            "source_location": args.source_location,
            "embedding": embedding,
        }).execute()
        print(f"New claim stored: {result.data[0]['id']}")


if __name__ == "__main__":
    main()
