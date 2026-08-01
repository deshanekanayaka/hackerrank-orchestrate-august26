"""Phase 2 spot-check: media grounding (OCR for images, ASR for voice notes).

Run from the code/ directory (or anywhere, paths are resolved relative to
this file): `python3 scripts/spotcheck_phase2.py`

Calls the real OpenAI API against a handful of actual files from
dataset/media/ (not mocked) so output quality can be eyeballed by hand.
Requires OPENAI_API_KEY set (via .env or exported). Second run of this
script should hit the cache and print instantly with no API calls.
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.data import load_all
from pipeline.media import ocr_image, asr_audio, ground_message


def spotcheck_images(data, n=3):
    print("=" * 90)
    print("IMAGES (OCR)")
    print("=" * 90)
    images = data["images"].to_dict("records")[:n]
    for row in images:
        t0 = time.time()
        result = ocr_image(row["image_id"], data["images"])
        dt = time.time() - t0
        print(f"\n--- {row['image_id']} ({row['file_path']}) [{dt:.2f}s] ---")
        if result["error"]:
            print("ERROR:", result["error"])
        else:
            print(result["text"])


def spotcheck_voice(data, n=None):
    print("\n" + "=" * 90)
    print("VOICE NOTES (ASR)")
    print("=" * 90)
    voice_notes = data["voice_notes"].to_dict("records")[:n]
    for row in voice_notes:
        t0 = time.time()
        result = asr_audio(row["voice_note_id"], data["voice_notes"])
        dt = time.time() - t0
        print(f"\n--- {row['voice_note_id']} ({row['file_path']}) [{dt:.2f}s] ---")
        if result["error"]:
            print("ERROR:", result["error"])
        else:
            print(result["text"])


def spotcheck_ground_message(data, n=3):
    print("\n" + "=" * 90)
    print("ground_message() on real messages.csv rows with media")
    print("=" * 90)
    media_rows = [r for r in data["messages"].to_dict("records") if r.get("media_type")][:n]
    for row in media_rows:
        grounding = ground_message(row, data)
        print(f"\n--- {row['message_id']} media_type={row['media_type']} media_id={row['media_id']} ---")
        print("media_error:", grounding["media_error"])
        print("media_text:", grounding["media_text"])


def full_media_grounding_run(data):
    """Stage 2 batch run: ground_message() over every media_type in
    (image, voice) row in messages.csv, then print one summary line --
    the failure-count signal a systemic outage (e.g. revoked API key)
    should surface as, instead of N silent per-item errors. Counted
    locally over just this loop so earlier calls elsewhere in the script
    (e.g. spotcheck_ground_message's 3 rows) can't leak into this total."""
    print("\n" + "=" * 90)
    print("FULL RUN: ground_message() over every messages.csv row with media")
    print("=" * 90)
    media_rows = [
        r for r in data["messages"].to_dict("records") if r.get("media_type") in ("image", "voice")
    ]
    fail_count = 0
    for row in media_rows:
        grounding = ground_message(row, data)
        if grounding["media_error"]:
            fail_count += 1
    print(f"{fail_count}/{len(media_rows)} media items failed")


def check_missing_media_handling(data):
    print("\n" + "=" * 90)
    print("Robustness: unknown media_id does not crash")
    print("=" * 90)
    result = ocr_image("img_does_not_exist", data["images"])
    print("ocr_image('img_does_not_exist') ->", result)
    result = asr_audio("vn_does_not_exist", data["voice_notes"])
    print("asr_audio('vn_does_not_exist') ->", result)


if __name__ == "__main__":
    if not os.environ.get("OPENAI_API_KEY"):
        from dotenv import load_dotenv

        load_dotenv()
    data = load_all()
    spotcheck_images(data)
    spotcheck_voice(data)
    spotcheck_ground_message(data)
    full_media_grounding_run(data)
    check_missing_media_handling(data)
