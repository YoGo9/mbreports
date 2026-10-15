# Weekly MusicBrainz reports — GitHub Actions trial

This is an **isolated experiment** on the `test-weekly-github-actions` branch.
It does **not** publish to GitHub Pages, update report files on any branch,
or disable the existing Sunday job on hppro30.

## Workflow

[Branch test workflow](../.github/workflows/test-weekly-dumps.yml)

The workflow starts when its configuration or test processor changes on this
branch. It has no cron schedule. It runs a synthetic regression test first,
reclaims preinstalled tools from the temporary GitHub runner, installs
parallel `lbzip2` decompression, downloads the official `mbdump-edit` and
`mbdump-editor` archives (with HTTP resumption and SHA256 verification),
and produces an artifact named **weekly-reports-test**.

The four report files inside the artifact mirror the originals:

- `TopEditors/json/YYYY-MM-DD.json` (top 500, applied edits)
- `TopEditors/manifest.json` (existing history plus the new date)
- `EditDiscussions/edits.json` (top 20,000, note counts)
- `EditDiscussions/editors.json` (only referenced usernames)

There is also `test-summary.json` with the dump ID, counts and runtime.

## Implementation differences

The previous job, kept in `YoGo9/mylocalscripts/mb-weekly`, extracts the
full `edit` and `edit_note` members to temporary disk before scanning them.
This trial streams directly from compressed archives and uses `lbzip2`.
For Top Discussions it still counts *every* note (not merely userscript
signatures). It uses a compact 32-bit indexed counter array for the
comment totals rather than a huge dictionary.

The tarball is scanned twice because the top discussion edit IDs are only
known after processing `edit_note`, which follows `edit` in the archive.
The second scan stops after the `edit` member. Nothing depends on the local
MusicBrainz PostgreSQL mirror.

## How to check a run

Go to the repository's **Actions** tab and select
**Test weekly MusicBrainz reports (isolated)**. Open the branch's run,
then download **weekly-reports-test** under Artifacts.

Before switching jobs:

1. Confirm the synthetic test passes and the full run completes.
2. Compare the generated files against the hppro30 job **using the same
   MusicBrainz dump ID**. Different snapshots will legitimately differ.
3. Confirm the Top Editors counts, discussion rankings, editor names and
   status fields agree, including tie order and missing edit rows.
4. Only then implement the publishing workflow on main. Disable the
   hppro30 weekly job *after* verifying a successfully published run.

**Top Voted Edits and daily database reports are outside the scope of this test.**

Reference for streaming/decompression approach:
[majkinetor's script-metrics](https://github.com/majkinetor/musicbrainz-userscripts/tree/main/dev/script-metrics).
