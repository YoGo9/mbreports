#!/usr/bin/env python3
"""Small end-to-end regression test for the GitHub-runner weekly dump processor."""
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import io


def make_tar(path: Path, members: list[tuple[str, str]]) -> None:
    with tarfile.open(path, "w:bz2") as archive:
        for name, text in members:
            data = text.encode("utf-8")
            header = tarfile.TarInfo(name)
            header.size = len(data)
            archive.addfile(header, io.BytesIO(data))


def main() -> None:
    script = Path(__file__).with_name("musicbrainz_weekly_actions.py")
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        edit_tar = root / "mbdump-edit.tar.bz2"
        editor_tar = root / "mbdump-editor.tar.bz2"
        edits = (
            "100\t7\t10\t2\tf\t2026-10-01 00:00:00+00\t2026-10-02 00:00:00+00\t\\N\t0\t\\N\n"
            "101\t8\t20\t1\tf\t2026-10-03 00:00:00+00\t\\N\t\\N\t0\t\\N\n"
            "102\t8\t30\t2\tf\t2026-10-04 00:00:00+00\t2026-10-05 00:00:00+00\t\\N\t0\t\\N\n"
        )
        notes = (
            "1\t7\t100\tHello\t2026-10-01\n"
            "2\t7\t100\tWorld\t2026-10-01\n"
            "3\t8\t101\tA\t2026-10-03\n"
            "4\t8\t101\tB\t2026-10-03\n"
            "5\t8\t101\tC\t2026-10-03\n"
            "6\t8\t102\tSingle\t2026-10-04\n"
            "7\t8\t999\tDeleted edit\t2026-10-04\n"
        )
        make_tar(edit_tar, [
            ("mbdump/edit", edits),
            ("mbdump/edit_data", "enormous JSON is not needed\n"),
            ("mbdump/edit_note", notes),
            ("mbdump/vote", "not needed\n"),
        ])
        make_tar(editor_tar, [("mbdump/editor_sanitized", "7\tEditor Seven\n8\tEditor Eight\n")])
        out = root / "out"
        subprocess.run([sys.executable, str(script),
                        "--dump-id", "20261014-150514",
                        "--edit-dump", str(edit_tar),
                        "--editor-dump", str(editor_tar),
                        "--output-dir", str(out),
                        "--discussions-top", "3"], check=True)
        top = json.loads((out / "TopEditors" / "json" / "2026-10-14.json").read_text())
        assert [(r["editor_id"], r["applied_edit_count"]) for r in top] == [(8, 1), (7, 1)]
        assert top[0]["editor_name"] == "Editor Eight"
        notes_out = json.loads((out / "EditDiscussions" / "edits.json").read_text())
        assert [(r["edit_id"], r["comment_count"]) for r in notes_out] == [(101, 3), (100, 2), (999, 1)]
        assert notes_out[0]["still_open"] is True
        assert notes_out[1]["still_open"] is False
        assert notes_out[2]["still_open"] is None and notes_out[2]["editor_id"] is None
        assert notes_out[1]["open_time"] == "2026-10-01 00:00:00+00"
        names = json.loads((out / "EditDiscussions" / "editors.json").read_text())
        assert names == {"7": "Editor Seven", "8": "Editor Eight"}
        manifest = json.loads((out / "TopEditors" / "manifest.json").read_text())
        assert manifest[0] == {"date": "2026-10-14", "file": "json/2026-10-14.json"}
        summary = json.loads((out / "test-summary.json").read_text())
        assert summary["notes_scanned"] == 7
        assert summary["edits_scanned"] == 3
        assert summary["publishing"].startswith("caller controlled")
        print("SELF-TEST PASSED: counts, ordering, statuses, missing edit, names and output structure")


if __name__ == "__main__":
    main()
