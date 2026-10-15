#!/usr/bin/env python3
"""Branch-only GitHub Actions test of the hppro30 weekly MusicBrainz dump reports.

Produces the SAME data shapes as TopEditors and EditDiscussions, in a separate
output directory. Never commits or pushes and never changes the checked-out
report files. No PostgreSQL or third-party Python packages are required.

Design: stream edit_note once to count *all* notes, then stream edit once to
count *all* applied edits and resolve the most-commented edit metadata.
The note counts live in a compact 32-bit array, not a Python dict per edit.
"""
from __future__ import annotations

import argparse
from array import array
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import heapq
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request

BASE = "https://data.metabrainz.org/pub/musicbrainz/data/fullexport"
EDIT_NAME = "mbdump-edit.tar.bz2"
EDITOR_NAME = "mbdump-editor.tar.bz2"
DUMP_PATTERN = re.compile(r"^[0-9]{8}-[0-9]{6}$")
BUFFER = 4 * 1024 * 1024
TOP_EDITORS = 500
TOP_DISCUSSIONS = 20_000


def log(message: str) -> None:
    print(message, flush=True)


def url_text(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "YoGo9-mb-weekly-actions-test/1.0"})
    with urllib.request.urlopen(request, timeout=90) as response:
        return response.read().decode("utf-8").strip()


def latest_dump() -> str:
    value = url_text(BASE + "/LATEST").strip("/")
    if not DUMP_PATTERN.fullmatch(value):
        raise ValueError(f"Unexpected MusicBrainz dump ID: {value!r}")
    return value


def expected_checksums(dump_id: str) -> dict[str, str]:
    result = {}
    for line in url_text(f"{BASE}/{dump_id}/SHA256SUMS").splitlines():
        bits = line.split()
        if len(bits) == 2:
            result[bits[1].lstrip("*").removeprefix("./")] = bits[0].lower()
    for required in (EDIT_NAME, EDITOR_NAME):
        if required not in result:
            raise ValueError(f"Checksum missing from MusicBrainz SHA256SUMS: {required}")
    return result


def download_verified(dump_id: str, name: str, folder: Path, checksum: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / name
    url = f"{BASE}/{dump_id}/{name}"

    # Each curl invocation resumes from the *current* size, including after a
    # connection that was alive but stopped transferring data.
    for attempt in range(1, 11):
        log(f"Downloading {name}, attempt {attempt}/10, existing {dest.stat().st_size if dest.exists() else 0:,} bytes")
        result = subprocess.run([
            "curl", "--fail", "--location", "--continue-at", "-",
            "--speed-limit", "1024", "--speed-time", "120",
            "--output", str(dest), url,
        ], check=False)
        if result.returncode == 0:
            break
        if attempt == 10:
            raise RuntimeError(f"{name}: download failed (curl {result.returncode})")
        time.sleep(min(30 * attempt, 300))

    log(f"Checking SHA256 for {name}")
    h = hashlib.sha256()
    with dest.open("rb") as source:
        for chunk in iter(lambda: source.read(16 * 1024 * 1024), b""):
            h.update(chunk)
    if h.hexdigest() != checksum:
        raise RuntimeError(f"{name}: SHA256 mismatch; refusing to process corrupt dump")
    log(f"Verified {name}: {dest.stat().st_size:,} bytes")
    return dest


def iter_member_lines(tar_path: Path, wanted: str):
    """Yield bytes of a selected COPY member; never extract to filesystem.

    Killing lbzip2 when the wanted member finishes avoids decompressing huge
    unrelated members after it. Tar is sequential: skip-to-member still costs
    decompression, but no large files are ever written.
    """
    decompressor = shutil.which("lbzip2") or shutil.which("bzip2")
    if decompressor is None:
        raise RuntimeError("lbzip2 or bzip2 is required")
    process = subprocess.Popen([decompressor, "-dc", str(tar_path)], stdout=subprocess.PIPE, bufsize=BUFFER)
    assert process.stdout is not None
    found = False
    try:
        with tarfile.open(fileobj=process.stdout, mode="r|") as archive:
            for member in archive:
                if not member.isfile() or member.name.lstrip("./") != wanted:
                    continue
                found = True
                extracted = archive.extractfile(member)
                if extracted is None:
                    raise RuntimeError(f"Cannot read {wanted}")
                # A buffered iterator avoids read() syscall overhead on each line.
                with io.BufferedReader(extracted, buffer_size=BUFFER) as rows:
                    for line in rows:
                        yield line.rstrip(b"\n")
                break
        if not found:
            raise RuntimeError(f"Missing {wanted} in {tar_path}")
    finally:
        if process.poll() is None:
            process.kill()  # Early termination is intentional.
        process.stdout.close()
        process.wait()


def note_top_counts(tar_path: Path, n: int) -> tuple[list[tuple[int, int]], int]:
    log("Pass 1: counting every edit note using a compact uint32 array")
    counts = array("I")
    scanned = 0
    for line in iter_member_lines(tar_path, "mbdump/edit_note"):
        first = line.find(b"\t")
        second = line.find(b"\t", first + 1)
        third = line.find(b"\t", second + 1)
        if first < 0 or second < 0 or third < 0:
            continue
        try:
            edit_id = int(line[second + 1:third])
        except ValueError:
            continue
        if edit_id < 0:
            continue
        if edit_id >= len(counts):
            new_size = max(edit_id + 1, max(2_000_000, len(counts) * 2))
            counts.frombytes(b"\x00" * (4 * (new_size - len(counts))))
        if counts[edit_id] == 4294967295:
            raise OverflowError(f"Note count overflow for edit {edit_id}")
        counts[edit_id] += 1
        scanned += 1
        if scanned % 5_000_000 == 0:
            log(f"  scanned {scanned:,} notes; {len(counts):,} possible edit IDs")
    top = heapq.nlargest(n, ((count, edit_id) for edit_id, count in enumerate(counts) if count),
                         key=lambda value: (value[0], value[1]))
    del counts
    log(f"  counted {scanned:,} notes; selected {len(top):,} edits")
    return top, scanned


def scan_edits(tar_path: Path, target_ids: set[int]) -> tuple[dict[int, int], dict[int, dict], int]:
    log("Pass 2: counting all applied edits and resolving discussion edit details")
    applied: dict[int, int] = defaultdict(int)
    details: dict[int, dict] = {}
    pending = target_ids.copy()
    scanned = 0
    for line in iter_member_lines(tar_path, "mbdump/edit"):
        fields = line.split(b"\t", 7)
        if len(fields) < 7:
            continue
        try:
            edit_id = int(fields[0])
            editor_id = int(fields[1])
            status = int(fields[3])
        except ValueError:
            continue
        scanned += 1
        if status == 2:
            applied[editor_id] += 1
        if edit_id in pending:
            try:
                edit_type = int(fields[2])
            except ValueError:
                edit_type = None
            details[edit_id] = {
                "editor_id": editor_id, "edit_type": edit_type,
                "edit_status": status,
                "open_time": None if fields[5] == b"\\N" else fields[5].decode("utf-8", "replace"),
                "close_time": None if fields[6] == b"\\N" else fields[6].decode("utf-8", "replace"),
            }
            pending.remove(edit_id)
        if scanned % 5_000_000 == 0:
            log(f"  scanned {scanned:,} edits; {len(details):,}/{len(target_ids):,} discussion rows found")
    log(f"  counted {scanned:,} edit rows, applied edits for {len(applied):,} editors")
    if pending:
        log(f"  warning: {len(pending):,} note-bearing edits had no edit row")
    return applied, details, scanned


def copy_unescape(value: bytes) -> str:
    """Postgres COPY text decode for usernames, mirroring the original job."""
    text = value.decode("utf-8", "replace")
    if "\\" not in text:
        return text
    out = []
    index = 0
    while index < len(text):
        c = text[index]
        if c != "\\" or index + 1 == len(text):
            out.append(c)
            index += 1
            continue
        next_char = text[index + 1]
        index += 2
        if next_char in ("n", "r", "t", "\\"):
            out.append({"n": "\n", "r": "\r", "t": "\t", "\\": "\\"}[next_char])
        elif next_char in "01234567":
            digits = next_char
            for _ in range(2):
                if index < len(text) and text[index] in "01234567":
                    digits += text[index]
                    index += 1
            out.append(chr(int(digits, 8)))
        else:
            out.append(next_char)
    return "".join(out)


def lookup_editors(tar_path: Path, target: set[int]) -> dict[int, str]:
    log(f"Resolving {len(target):,} editor names")
    names = {}
    pending = target.copy()
    for line in iter_member_lines(tar_path, "mbdump/editor_sanitized"):
        fields = line.split(b"\t", 2)
        if len(fields) < 2:
            continue
        try:
            editor_id = int(fields[0])
        except ValueError:
            continue
        if editor_id in pending:
            names[editor_id] = copy_unescape(fields[1])
            pending.remove(editor_id)
        # Still consume member? Early stop is safe: generator finally kills the decompressor.
        if not pending:
            break
    if pending:
        log(f"  warning: {len(pending)} editor names not found")
    return names


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def generate(edit_dump: Path, editor_dump: Path, dump_id: str, out: Path, repo_root: Path,
             discussions_top: int = TOP_DISCUSSIONS):
    date = f"{dump_id[:4]}-{dump_id[4:6]}-{dump_id[6:8]}"
    start = time.monotonic()
    top_notes, total_notes = note_top_counts(edit_dump, discussions_top)
    counts, edit_details, total_edits = scan_edits(edit_dump, {edit for _, edit in top_notes})
    top_editors = heapq.nlargest(TOP_EDITORS, counts.items(), key=lambda item: (item[1], item[0]))
    needed_names = {eid for eid, _ in top_editors}
    needed_names.update(record["editor_id"] for record in edit_details.values()
                        if record.get("editor_id") is not None)
    names = lookup_editors(editor_dump, needed_names)

    top_rows = [
        {"rank": rank, "editor_id": editor_id,
         "editor_name": names.get(editor_id, f"(editor #{editor_id})"),
         "applied_edit_count": count}
        for rank, (editor_id, count) in enumerate(top_editors, 1)
    ]
    discussions = []
    for rank, (count, edit_id) in enumerate(top_notes, 1):
        record = edit_details.get(edit_id, {})
        status = record.get("edit_status")
        discussions.append({
            "rank": rank, "edit_id": edit_id, "comment_count": count,
            "editor_id": record.get("editor_id"),
            "edit_type": record.get("edit_type"), "edit_status": status,
            "still_open": (status == 1) if status is not None else None,
            "open_time": record.get("open_time"), "close_time": record.get("close_time"),
            "url": f"https://musicbrainz.org/edit/{edit_id}",
        })

    write_json(out / "TopEditors" / "json" / f"{date}.json", top_rows)
    current_manifest = repo_root / "TopEditors" / "manifest.json"
    old_manifest = json.loads(current_manifest.read_text("utf-8")) if current_manifest.exists() else []
    manifest = [entry for entry in old_manifest if entry.get("date") != date]
    manifest.append({"date": date, "file": f"json/{date}.json"})
    manifest.sort(key=lambda entry: entry["date"], reverse=True)
    write_json(out / "TopEditors" / "manifest.json", manifest)
    write_json(out / "EditDiscussions" / "edits.json", discussions)
    write_json(out / "EditDiscussions" / "editors.json", {
        str(editor_id): names[editor_id] for editor_id in sorted({
            record["editor_id"] for record in edit_details.values()
            if record.get("editor_id") in names
        })
    })
    meta = {
        "dump_id": dump_id, "snapshot_date": date,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "notes_scanned": total_notes, "edits_scanned": total_edits,
        "top_editor_rows": len(top_rows), "discussion_rows": len(discussions),
        "seconds": round(time.monotonic() - start, 2),
        "publishing": "artifact only; main and GitHub Pages unchanged",
    }
    write_json(out / "test-summary.json", meta)
    log(f"Done: {meta}")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dump-id", help="Pin to one full export; default LATEST")
    p.add_argument("--edit-dump", type=Path, help="Local input (for synthetic tests)")
    p.add_argument("--editor-dump", type=Path, help="Local input (for synthetic tests)")
    p.add_argument("--output-dir", type=Path, default=Path("weekly-actions-test-output"))
    p.add_argument("--download-dir", type=Path, default=Path("/tmp/mb-weekly-dumps"))
    p.add_argument("--discussions-top", type=int, default=TOP_DISCUSSIONS)
    args = p.parse_args()
    if bool(args.edit_dump) != bool(args.editor_dump):
        p.error("Must supply both --edit-dump and --editor-dump, or neither")
    if args.discussions_top < 1:
        p.error("--discussions-top must be positive")

    dump_id = args.dump_id or latest_dump()
    if not DUMP_PATTERN.fullmatch(dump_id):
        p.error("Invalid dump ID")
    if args.edit_dump:
        edit_dump, editor_dump = args.edit_dump, args.editor_dump
    else:
        checksums = expected_checksums(dump_id)
        edit_dump = download_verified(dump_id, EDIT_NAME, args.download_dir, checksums[EDIT_NAME])
        editor_dump = download_verified(dump_id, EDITOR_NAME, args.download_dir, checksums[EDITOR_NAME])

    root = Path(__file__).resolve().parents[1]
    generate(edit_dump, editor_dump, dump_id, args.output_dir, root, args.discussions_top)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr, flush=True)
        raise
