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
        votes = (
            "1\t7\t100\t1\t2026-10-05\tf\n"
            "2\t7\t100\t0\t2026-10-05\tf\n"
            "3\t7\t100\t-1\t2026-10-05\tf\n"
            "4\t7\t100\t2\t2026-10-05\tf\n"
            "5\t7\t100\t3\t2026-10-05\tf\n"
            "6\t7\t100\t4\t2026-10-05\tf\n"
            "7\t7\t100\t1\t2026-10-05\tt\n"
            "8\t8\t101\t0\t2026-10-05\tf\n"
            "9\t8\t101\t0\t2026-10-05\tf\n"
            "10\t8\t102\t2\t2026-10-05\tf\n"
            "11\t8\t999\t1\t2026-10-05\tf\n"
            "12\t8\t8000\t0\t2026-10-05\tf\n"
            "13\t8\t8000\t0\t2026-10-05\tf\n"
        )
        make_tar(edit_tar, [
            ("mbdump/edit", edits),
            ("mbdump/edit_data", "enormous JSON is not needed\n"),
            ("mbdump/edit_note", notes),
            ("mbdump/vote", votes),
        ])
        make_tar(editor_tar, [("mbdump/editor_sanitized", "7\tEditor Seven\n8\tEditor Eight\n")])
        out = root / "out"
        subprocess.run([sys.executable, str(script),
                        "--dump-id", "20261014-150514",
                        "--edit-dump", str(edit_tar),
                        "--editor-dump", str(editor_tar),
                        "--output-dir", str(out),
                        "--discussions-top", "3", "--votes-top", "2"], check=True)
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
        expected_snapshot = {"date": "2026-10-14", "file": "json/2026-10-14.json"}
        # The repository can contain newer real snapshots; the synthetic
        # snapshot is valid even when it is not the first manifest entry.
        assert manifest.count(expected_snapshot) == 1
        dates = [entry["date"] for entry in manifest]
        assert dates == sorted(dates, reverse=True)
        assert len(dates) == len(set(dates))
        summary = json.loads((out / "test-summary.json").read_text())
        voted = json.loads((out / "TopVotedEdits" / "votes.json").read_text())
        assert [(r["edit_id"], r["total_votes"]) for r in voted] == [
            (100, 6), (8000, 2), (101, 2), (999, 1), (102, 1)
        ]
        assert voted[0]["yes_votes"] == 1 and voted[0]["no_votes"] == 1
        assert voted[0]["abstain_votes"] == 1 and voted[0]["approve_votes"] == 1
        assert voted[0]["admin_approve_votes"] == 1 and voted[0]["admin_reject_votes"] == 1
        assert voted[1]["editor_id"] is None  # missing edit row
        assert voted[2]["still_open"] is True
        assert voted[4]["editor_id"] == 8
        vote_names = json.loads((out / "TopVotedEdits" / "editors.json").read_text())
        assert vote_names == {"7": "Editor Seven", "8": "Editor Eight"}
        assert json.loads((out / "TopVotedEdits" / "meta.json").read_text()) == {
            "snapshot_date": "2026-10-14"
        }
        assert json.loads((out / "EditDiscussions" / "meta.json").read_text()) == {
            "snapshot_date": "2026-10-14"
        }
        assert summary["votes_scanned"] == 13
        assert summary["current_votes"] == 12
        assert summary["superseded_votes"] == 1
        assert summary["top_voted_rows"] == 5
        assert summary["notes_scanned"] == 7
        assert summary["edits_scanned"] == 3
        assert summary["publishing"].startswith("caller controlled")
        print("SELF-TEST PASSED: all 3 reports, seven vote metrics, superseded votes, tie-breaks and metadata")


if __name__ == "__main__":
    main()
