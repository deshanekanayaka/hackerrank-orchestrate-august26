"""Media grounding: OCR for images, ASR for voice notes (FR-5).

02-approach.md's Phase 2 fallback applies here: neither ffmpeg nor tesseract
is installed locally, so this calls OpenAI directly (vision for images,
audio transcription for voice notes) rather than standing up dedicated
OCR/ASR libraries. Only 19 unique images and 13 unique voice notes exist
across the whole dataset, so per-call LLM cost/latency is a non-issue.

Results are cached to disk keyed by media_id (code/cache/media_cache.json)
so a re-run of the pipeline never re-calls the API for media already
processed, and messages that share a media_id (some do) aren't billed
twice.
"""

import base64
import io
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI
from PIL import Image

load_dotenv()

_THIS_DIR = Path(__file__).resolve().parent
DATASET_DIR = (_THIS_DIR / ".." / ".." / "dataset").resolve()
CACHE_DIR = (_THIS_DIR / ".." / "cache").resolve()
CACHE_PATH = CACHE_DIR / "media_cache.json"

VISION_MODEL = os.environ.get("OCR_MODEL", "gpt-4o-mini")
ASR_MODEL = os.environ.get("ASR_MODEL", "whisper-1")

OCR_PROMPT = (
    "This image was sent as a WhatsApp message. Transcribe any visible text "
    "verbatim (poster text, screenshot content, prices, dates, links), then "
    "add one short sentence describing what the image depicts. If there is "
    "no legible text, say so and just describe the image. Be factual, do "
    "not speculate about sender intent."
)

_client = None
_cache = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "OPENAI_API_KEY not set. Add it to .env or export it before "
                "running media grounding."
            )
        _client = OpenAI(api_key=api_key)
    return _client


# Keyed by media_id only, not a content hash -- if a media file is ever
# replaced under the same ID, the stale cached result is served silently.
# Clear the relevant "image:<id>" / "voice:<id>" entry from media_cache.json
# by hand if that happens.
def _load_cache() -> dict:
    global _cache
    if _cache is None:
        _cache = json.loads(CACHE_PATH.read_text()) if CACHE_PATH.exists() else {}
    return _cache


def _save_cache() -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(_cache, indent=2, sort_keys=True))


def _cached(cache_key: str, compute) -> dict:
    cache = _load_cache()
    if cache_key in cache:
        return cache[cache_key]
    result = compute()
    cache[cache_key] = result
    _save_cache()
    return result


def _lookup_file(df, id_col: str, media_id: str) -> Path | None:
    row = df[df[id_col] == media_id]
    if row.empty:
        return None
    return DATASET_DIR / row.iloc[0]["file_path"]


def _normalize_to_png(raw: bytes) -> bytes:
    """Files in this dataset are all named *.jpg but some are actually PNG/
    WebP/AVIF (image_id img_020 is AVIF, which OpenAI's vision endpoint
    rejects outright -- it isn't in the API's supported-format list even
    though Pillow can decode it fine). Decoding with Pillow and always
    re-encoding as PNG sidesteps format detection and misnamed extensions
    entirely, for any format Pillow supports now or in the future."""
    img = Image.open(io.BytesIO(raw))
    img.load()
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGBA" if "A" in img.mode else "RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def ocr_image(image_id: str, images_df) -> dict:
    """Returns {"text": str, "error": str|None} for image_id, cached by media_id."""

    def compute() -> dict:
        file_path = _lookup_file(images_df, "image_id", image_id)
        if file_path is None:
            return {"text": "", "error": f"no images.csv row for {image_id}"}
        if not file_path.exists():
            return {"text": "", "error": f"media file missing: {file_path}"}

        try:
            png_bytes = _normalize_to_png(file_path.read_bytes())
        except Exception as e:
            return {"text": "", "error": f"could not decode image: {e}"}

        b64 = base64.b64encode(png_bytes).decode("utf-8")
        try:
            resp = _get_client().chat.completions.create(
                model=VISION_MODEL,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": OCR_PROMPT},
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/png;base64,{b64}"},
                            },
                        ],
                    }
                ],
                max_tokens=1000,
                temperature=0,
            )
            return {"text": resp.choices[0].message.content.strip(), "error": None}
        except Exception as e:
            return {"text": "", "error": str(e)}

    return _cached(f"image:{image_id}", compute)


def _sniff_audio_ext(data: bytes, fallback_ext: str) -> str:
    """Voice notes in this dataset are all named *.mp3 but several are
    actually M4A/WAV/MP4 containers -- the transcription API infers format
    from the filename it's given, not the bytes, so a wrong extension gets
    rejected outright. Sniff the real container from magic bytes and upload
    under a filename with the correct extension."""
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "wav"
    if data[4:8] == b"ftyp":
        return "m4a" if data[8:11] == b"M4A" else "mp4"
    if data[:3] == b"ID3" or data[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return "mp3"
    return fallback_ext


def asr_audio(voice_note_id: str, voice_notes_df) -> dict:
    """Returns {"text": str, "error": str|None} for voice_note_id, cached by media_id."""

    def compute() -> dict:
        file_path = _lookup_file(voice_notes_df, "voice_note_id", voice_note_id)
        if file_path is None:
            return {"text": "", "error": f"no voice_notes.csv row for {voice_note_id}"}
        if not file_path.exists():
            return {"text": "", "error": f"media file missing: {file_path}"}

        raw = file_path.read_bytes()
        ext = _sniff_audio_ext(raw, file_path.suffix.lstrip("."))
        upload_name = f"{voice_note_id}.{ext}"
        try:
            resp = _get_client().audio.transcriptions.create(
                model=ASR_MODEL, file=(upload_name, raw)
            )
            return {"text": resp.text.strip(), "error": None}
        except Exception as e:
            return {"text": "", "error": str(e)}

    return _cached(f"voice:{voice_note_id}", compute)


# Incremented on every media_error, so a systemic failure (e.g. a revoked
# API key) can be surfaced as one loud count at the end of a batch run
# instead of degrading silently into N identical per-item errors.
_media_fail_count = 0


def media_fail_count() -> int:
    return _media_fail_count


def ground_message(message: dict, data: dict) -> dict:
    """Turns a message row's media (if any) into plain text (FR-5).

    Returns {"media_text": str, "media_error": str|None}. media_error set
    means OCR/ASR failed or the media file was missing -- Stage 3/4 should
    lower confidence rather than crash (FR-8, Phase 6 robustness).
    """
    global _media_fail_count
    media_type = (message.get("media_type") or "").strip().lower()
    media_id = (message.get("media_id") or "").strip()

    if media_type == "image" and media_id:
        result = ocr_image(media_id, data["images"])
    elif media_type == "voice" and media_id:
        result = asr_audio(media_id, data["voice_notes"])
    else:
        return {"media_text": "", "media_error": None}

    if result["error"]:
        _media_fail_count += 1

    return {"media_text": result["text"], "media_error": result["error"]}
