"""Memory-bounded seven-metric MusicBrainz vote rankings.

Faithful to the hppro30 weekly report: only nonsuperseded votes count; preserve
20,000 top edits INDEPENDENTLY per metric, then union and sort by total/yes/no/id.
Counters use a sparse on-disk mmap (7 uint32 per edit ID) to stay within a
GitHub runner's memory budget.
"""
from __future__ import annotations

from heapq import heappush, heapreplace
from mmap import mmap, ACCESS_WRITE
from pathlib import Path
import struct
import tempfile

METRICS = ('total_votes', 'yes_votes', 'no_votes', 'abstain_votes',
           'approve_votes', 'admin_approve_votes', 'admin_reject_votes')
RECORD = struct.Struct('<7I')
VOTE_INDEX = {1: 1, 0: 2, -1: 3, 2: 4, 3: 5, 4: 6}
MAX_COUNT = 0xffffffff
MAX_EDIT_ID = 250_000_000


def top_vote_counts(edit_dump: Path, lines_for_member, *, top_n: int = 20_000):
    """Return (ordered unique IDs, selected counts, stats), without retaining all votes."""
    if top_n < 1:
        raise ValueError('top_n must be positive')
    processed = current = superseded = 0
    capacity = 4096
    with tempfile.TemporaryDirectory(prefix='mb-vote-mmap-') as td:
        path = Path(td) / 'vote-counters.bin'
        with path.open('w+b') as fp:
            fp.truncate(capacity * RECORD.size)
            with mmap(fp.fileno(), 0, access=ACCESS_WRITE) as mm:
                # COPY schema: id, editor, edit, vote, vote_time, superseded.
                for line in lines_for_member(edit_dump, 'mbdump/vote'):
                    processed += 1
                    parts = line.split(b'\t', 6)
                    if len(parts) < 6:
                        continue
                    if parts[5].lower() not in (b'f', b'false', b'0'):
                        superseded += 1
                        continue
                    try:
                        edit_id = int(parts[2])
                        vote = int(parts[3])
                    except ValueError:
                        continue
                    if not (0 <= edit_id <= MAX_EDIT_ID):
                        continue
                    if edit_id >= capacity:
                        capacity = ((edit_id // 8_000_000) + 1) * 8_000_000
                        mm.resize(capacity * RECORD.size)
                    offset = edit_id * RECORD.size
                    values = list(RECORD.unpack_from(mm, offset))
                    if values[0] == MAX_COUNT:
                        raise OverflowError(f'vote total overflow for edit {edit_id}')
                    values[0] += 1
                    index = VOTE_INDEX.get(vote)
                    if index is not None:
                        if values[index] == MAX_COUNT:
                            raise OverflowError(f'vote bucket overflow for edit {edit_id}')
                        values[index] += 1
                    RECORD.pack_into(mm, offset, *values)
                    current += 1
                    if processed % 5_000_000 == 0:
                        print(f'  scanned {processed:,} vote rows ({current:,} current)', flush=True)

                print(f'  votes: {processed:,} scanned, {current:,} current, '
                      f'{superseded:,} superseded ignored; scanning top {top_n:,} for each metric', flush=True)
                heaps = [[] for _ in range(len(METRICS))]
                for edit_id, values in enumerate(RECORD.iter_unpack(mm)):
                    if not values[0]:
                        continue
                    for index, value in enumerate(values):
                        if value == 0:
                            continue
                        candidate = (value, edit_id)
                        heap = heaps[index]
                        if len(heap) < top_n:
                            heappush(heap, candidate)
                        elif candidate > heap[0]:
                            heapreplace(heap, candidate)

                selected_ids = {eid for heap in heaps for _count, eid in heap}
                selected = {eid: RECORD.unpack_from(mm, eid * RECORD.size)
                            for eid in selected_ids}
                ids = sorted(selected_ids,
                             key=lambda eid: (selected[eid][0], selected[eid][1],
                                              selected[eid][2], eid),
                             reverse=True)
                print(f'  selected {len(ids):,} unique edits from all seven rankings', flush=True)
                return ids, selected, {'votes_scanned': processed,
                                       'current_votes': current,
                                       'superseded_votes': superseded}


def build_voted_rows(ids: list[int], counts: dict, details: dict) -> list[dict]:
    rows = []
    for rank, edit_id in enumerate(ids, 1):
        values = counts[edit_id]
        info = details.get(edit_id, {})
        status = info.get('edit_status')
        row = {'rank': rank, 'edit_id': edit_id}
        row.update(zip(METRICS, values))
        row.update({
            'editor_id': info.get('editor_id'),
            'edit_type': info.get('edit_type'),
            'edit_status': status,
            'still_open': (status == 1) if status is not None else None,
            'open_time': info.get('open_time'),
            'close_time': info.get('close_time'),
            'url': f'https://musicbrainz.org/edit/{edit_id}',
        })
        rows.append(row)
    return rows
