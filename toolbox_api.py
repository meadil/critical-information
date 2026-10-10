"""
Local API wrapping the critical-info pipeline scripts.
Run with: uvicorn toolbox_api:app --port 8000

Each endpoint just shells out to the matching script for now.
The scripts themselves (extract_pdf.py, transcribe.py, etc.) still need
to be written — this file is the connective layer n8n's HTTP Request Tool
nodes will call, so we only ever need ONE node type in n8n.
"""

from fastapi import FastAPI
from pydantic import BaseModel
from pathlib import Path
from dotenv import load_dotenv
import subprocess
import sys

load_dotenv()  # reads .env in this folder, sets SUPABASE_URL / SUPABASE_KEY etc.
               # subprocess.run() below inherits these automatically -- store_claim.py
               # needs no changes to see them.

app = FastAPI()

# Resolves to a "scripts" folder next to this file, wherever it lives —
# no reliance on ~ expansion or the folder you happen to launch uvicorn from.
SCRIPTS_DIR = Path(__file__).parent / "scripts"


def run_script(script_name: str, args: list[str]) -> dict:
    result = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / script_name), *args],
        capture_output=True, text=True
    )
    return {"stdout": result.stdout, "stderr": result.stderr, "returncode": result.returncode}


class PdfRequest(BaseModel):
    file_path: str

@app.post("/extract_pdf")
def extract_pdf(req: PdfRequest):
    return run_script("extract_pdf.py", [req.file_path])


class TranscribeRequest(BaseModel):
    source: str  # file path or URL

@app.post("/transcribe")
def transcribe(req: TranscribeRequest):
    return run_script("transcribe.py", [req.source])


class ScrapeRequest(BaseModel):
    url: str

@app.post("/scrape_web")
def scrape_web(req: ScrapeRequest):
    return run_script("scrape_web.py", [req.url])


class AtomizeRequest(BaseModel):
    text: str
    source_title: str = ""
    source_author: str = ""
    source_type: str = ""
    source_location: str = ""

@app.post("/atomize_and_tag")
def atomize_and_tag(req: AtomizeRequest):
    # Text goes through stdin instead of argv — avoids shell arg-length limits
    result = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "atomize_and_tag.py"),
         "--title", req.source_title, "--author", req.source_author,
         "--type", req.source_type, "--location", req.source_location],
        input=req.text, capture_output=True, text=True
    )
    return {"stdout": result.stdout, "stderr": result.stderr, "returncode": result.returncode}


class StoreClaimRequest(BaseModel):
    claim_text: str
    claim_type: str
    topic_tags: str
    verifiability: str
    source_title: str = ""
    source_author: str = ""
    source_type: str = ""
    source_location: str = ""

@app.post("/store_claim")
def store_claim(req: StoreClaimRequest):
    return run_script("store_claim.py", [
        "--claim", req.claim_text, "--claim-type", req.claim_type,
        "--tags", req.topic_tags, "--verifiability", req.verifiability,
        "--source-title", req.source_title, "--source-author", req.source_author,
        "--source-type", req.source_type, "--source-location", req.source_location,
    ])
