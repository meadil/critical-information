# Notes — deferred decisions (do not implement yet)

## 1. Predictable Revenue run was volume test, not quality check
- The 2026-09-29 ingest (26 chunks) was to test volume/throughput, not claim quality.
- Do not judge atomizer quality from it.

## 2. Claim style: verbatim vs paraphrase (KEEP AS-IS for now)
- Current behavior: claims are cut verbatim from the source (sentence/paragraph slice), NOT paraphrased.
- Note: `atomize_and_tag.py` ATOMIZE_PROMPT currently says "Paraphrase each claim in your own words" — this contradicts the decided keep-verbatim behavior. Reconcile when this is revisited.
- Open question: paraphrase vs keep-as-is?
  - Verbatim pros: faithful, traceable. Cons: single sentence without surrounding context is confusing.
  - Paraphrase pros: could pack context in. Cons: risks drift/hallucination, harder to trace.
- Constraint discussed: claim should stay ~one sentence / one line, which makes adding context hard.
- Decision for now: keep current behavior unchanged. Test web + YouTube first, revisit later.

## 3. Source location is too coarse (FIX LATER)
- Current: `source_location` = PDF filename only (see `batch_ingest.py` — sets it to `Path(pdf).name` for every claim). Page info exists in chunks (`[p.N]` markers from `extract_pdf.py` page splits) but is NOT propagated per-claim.
- Needed per-claim granularity:
  - Books: book name + exact page number.
  - Websites: paragraph / section anchor.
  - YouTube: timestamp.
- Why: without it, a claim cannot be traced back to its exact source later.
- Decision: do not implement now. Revisit after web + YouTube tests.

## Next focus
- Move to testing web and YouTube ingest. Leave book page-number work deferred.
