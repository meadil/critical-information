"""
Transcribe audio/video. If the source looks like a YouTube URL, pull existing
captions first (fast, free, no local model).

ARCHIVED (2026-09-27, per user - YouTube mostly, no video download):
transcribe_file() local faster-whisper fallback is out of scope for now.
Kept in code but not maintained/tested. YouTube captions path is active.
Usage: python transcribe.py <youtube_url>
"""
import sys


try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def is_youtube_url(source: str) -> bool:
    return "youtube.com" in source or "youtu.be" in source


def transcribe_youtube(url: str) -> str:
    from youtube_transcript_api import YouTubeTranscriptApi
    video_id = url.split("v=")[-1].split("&")[0] if "v=" in url else url.rstrip("/").split("/")[-1]
    # v1.x API: fetch() returns iterable of snippets with .text (not dicts)
    fetched = YouTubeTranscriptApi().fetch(video_id)
    return "\n".join(snippet.text for snippet in fetched)


def transcribe_file(path: str) -> str:
    """ARCHIVED - not maintained (see module docstring)."""
    raise NotImplementedError("transcribe_file archived per user 2026-09-27: YouTube-only workflow, no local downloads.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python transcribe.py <youtube_url_or_file_path>", file=sys.stderr)
        sys.exit(1)
    source = sys.argv[1]
    try:
        if is_youtube_url(source):
            print(transcribe_youtube(source))
        else:
            print(transcribe_file(source))
    except Exception as e:
        print(f"Error transcribing: {e}", file=sys.stderr)
        sys.exit(1)
