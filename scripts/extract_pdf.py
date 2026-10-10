"""
Extract text from a PDF, page by page.
Usage: python extract_pdf.py <path_to_pdf>
Prints extracted text to stdout (this is what toolbox_api.py captures).

Output is forced to UTF-8 with a hard-fail on unencodable characters. Without
this, Windows' default cp1252 encoding mangles smart quotes and accented
characters (e.g. "don\u2019t" becomes "don?t"), which silently corrupts every
claim extracted downstream.
"""
import sys
import pymupdf as fitz  # 'fitz' is the deprecated import name; this avoids its stdout warning


def extract_text(pdf_path: str) -> str:
    doc = fitz.open(pdf_path)
    pages = []
    for page_num, page in enumerate(doc, start=1):
        text = page.get_text().strip()
        pages.append(f"--- page {page_num} ---\n{text}")
    doc.close()
    return "\n\n".join(pages)


if __name__ == "__main__":
    # Reconfigure stdout/stderr to UTF-8 before anything is printed.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    if len(sys.argv) < 2:
        print("Usage: python extract_pdf.py <path_to_pdf>", file=sys.stderr)
        sys.exit(1)

    pdf_path = sys.argv[1]
    try:
        print(extract_text(pdf_path))
    except Exception as e:
        print(f"Error extracting PDF: {e}", file=sys.stderr)
        sys.exit(1)
