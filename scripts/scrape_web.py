"""
Extract clean article text from a web page URL, stripping ads/nav/etc.
Usage: python scrape_web.py <url>
"""
import sys
import trafilatura

try:
    # Windows console/pipe defaults to cp1252 which crashes on Unicode
    # chars like ↑ present in Wikipedia refs. Force UTF-8.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scrape_web.py <url>", file=sys.stderr)
        sys.exit(1)

    url = sys.argv[1]
    downloaded = trafilatura.fetch_url(url)
    if downloaded is None:
        print(f"Error: could not fetch {url}", file=sys.stderr)
        sys.exit(1)

    text = trafilatura.extract(downloaded)
    if text is None:
        print(f"Error: could not extract readable text from {url}", file=sys.stderr)
        sys.exit(1)

    print(text)
