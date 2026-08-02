"""Phase 8 validation: the packaging checks, plus the media-cache
concurrency regression that Phase 8 fixed.

1. run_cache_concurrency_case() -- regression test for the media_cache.json
   write race. ground_message() runs inside decide_all()'s ThreadPoolExecutor,
   so several threads could call media._cached() at once; the pre-fix code
   mutated a module-global dict and rewrote the whole file with a
   non-atomic write_text(), which could raise "dictionary changed size
   during iteration" mid-serialize or interleave two writes into malformed
   JSON on disk. A malformed cache file is not self-healing -- _load_cache()
   raises on it for every later run -- so this is checked explicitly rather
   than left to chance. Fully deterministic: compute() is mocked, no API
   key and no network needed.
2. run_requirements_completeness_case() -- every third-party module imported
   anywhere under code/ must appear in requirements.txt. Written after the
   first draft of requirements.txt omitted pandas (the single most
   load-bearing dependency in the repo, imported by pipeline/data.py), which
   would have made the README's setup instructions fail on a clean machine.
3. run_packaging_files_case() -- the files a grader needs in order to run
   this at all actually exist.
4. validate_output_csv() -- reused as-is from spotcheck_phase5.py rather
   than reimplemented, since Phase 8's "final output.csv validated"
   checkpoint is the same structural contract Phase 5 already encodes.

Run: `python3 scripts/spotcheck_phase8.py` (from the code/ directory).
No OPENAI_API_KEY needed for checks 1-3; check 4 needs code/main.py to have
been run at least once (dataset/output.csv populated).
"""

import ast
import json
import os
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import media
from pipeline.data import load_all
from spotcheck_phase5 import validate_output_csv

CODE_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = CODE_DIR.parent
REQUIREMENTS_PATH = CODE_DIR / "requirements.txt"

# Import name -> distribution name, where they differ.
_DIST_NAME = {"PIL": "pillow", "dotenv": "python-dotenv"}

PASS = "PASS"
FAIL = "FAIL"
_results = []


def check(label: str, condition: bool, detail: str = ""):
    status = PASS if condition else FAIL
    _results.append(condition)
    print(f"[{status}] {label}" + (f" -- {detail}" if detail else ""))


def run_cache_concurrency_case():
    print("=" * 90)
    print("1. media cache survives concurrent writers (no corruption, no lost entries)")
    print("=" * 90)

    writers = 24
    barrier = threading.Barrier(writers)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_cache_dir = Path(tmpdir)
        original_dir, original_path, original_cache = (
            media.CACHE_DIR,
            media.CACHE_PATH,
            media._cache,
        )
        media.CACHE_DIR = tmp_cache_dir
        media.CACHE_PATH = tmp_cache_dir / "media_cache.json"
        media._cache = None

        errors = []

        def writer(i: int):
            try:
                # Release every thread into _cached() at the same instant, so
                # the serialize-and-write window actually overlaps instead of
                # the threads politely queueing behind thread-start latency.
                barrier.wait()
                media._cached(f"image:v1:img_{i:03d}", lambda: {"text": f"t{i}", "error": None})
            except Exception as e:
                errors.append(e)

        try:
            threads = [threading.Thread(target=writer, args=(i,)) for i in range(writers)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            check("no writer raised", not errors, f"{len(errors)} raised, first={errors[:1]}")

            on_disk = None
            try:
                on_disk = json.loads(media.CACHE_PATH.read_text())
                parsed = True
            except Exception as e:
                parsed = False
                check("cache file on disk is valid JSON", False, str(e))
            if parsed:
                check("cache file on disk is valid JSON", True)
                check(
                    "every concurrent write survived in the file",
                    len(on_disk) == writers,
                    f"expected {writers} entries, found {len(on_disk) if on_disk else 0}",
                )

            strays = list(tmp_cache_dir.glob("*.tmp"))
            check("atomic write left no stray .tmp files", not strays, f"found {strays}")
        finally:
            media.CACHE_DIR, media.CACHE_PATH, media._cache = (
                original_dir,
                original_path,
                original_cache,
            )


def _third_party_imports() -> set[str]:
    """Top-level non-stdlib, non-local module names imported anywhere in code/."""
    local = {p.stem for p in CODE_DIR.rglob("*.py")} | {
        p.name for p in CODE_DIR.iterdir() if p.is_dir()
    }
    found = set()
    for path in CODE_DIR.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                # level > 0 is a relative (in-package) import.
                names = [] if node.level else [(node.module or "").split(".")[0]]
            else:
                continue
            for name in names:
                if name and name not in sys.stdlib_module_names and name not in local:
                    found.add(name)
    return found


def run_requirements_completeness_case():
    print()
    print("=" * 90)
    print("2. requirements.txt covers every third-party import under code/")
    print("=" * 90)

    if not REQUIREMENTS_PATH.exists():
        check("requirements.txt exists", False, str(REQUIREMENTS_PATH))
        return
    check("requirements.txt exists", True)

    pinned = set()
    for line in REQUIREMENTS_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        for sep in ("==", ">=", "<=", "~=", ">", "<"):
            if sep in line:
                line = line.split(sep)[0]
                break
        pinned.add(line.strip().lower())

    imported = _third_party_imports()
    required = {_DIST_NAME.get(name, name).lower() for name in imported}
    missing = sorted(required - pinned)

    print(f"    imports found: {sorted(imported)}")
    print(f"    requirements.txt: {sorted(pinned)}")
    check("no third-party import is missing from requirements.txt", not missing, f"missing={missing}")


def run_packaging_files_case():
    print()
    print("=" * 90)
    print("3. files a grader needs to run this are present")
    print("=" * 90)

    for label, path in [
        ("code/README.md (setup + run instructions)", CODE_DIR / "README.md"),
        ("code/requirements.txt", REQUIREMENTS_PATH),
        ("code/main.py (entry point)", CODE_DIR / "main.py"),
        ("dataset/output.csv (predictions)", REPO_ROOT / "dataset" / "output.csv"),
    ]:
        check(label, path.exists(), f"expected at {path}")

    gitignore = REPO_ROOT / ".gitignore"
    ignored = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
    check(
        ".env is gitignored (no secrets can ride along in the zip)",
        any(line.strip() in (".env", "*.env", ".env*") for line in ignored.splitlines()),
    )


if __name__ == "__main__":
    run_cache_concurrency_case()
    run_requirements_completeness_case()
    run_packaging_files_case()

    print()
    output_ok = validate_output_csv(load_all())
    _results.append(bool(output_ok))
    print(f"[{PASS if output_ok else FAIL}] dataset/output.csv structural validation")

    print("\n" + "=" * 90)
    total, passed = len(_results), sum(_results)
    print(f"{passed}/{total} checks passed")
    print("=" * 90)
    if passed != total:
        sys.exit(1)
