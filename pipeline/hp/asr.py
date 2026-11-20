"""Stage: asr. Run FastConformer (words) and Zipformer (sounds) over every audio track.
Resumable and idempotent: finished tracks (DONE) and finished 60 s blocks are skipped on re-run.
Tracks are split across worker processes by audio length (longest first) so workers finish together."""
import json, math, os, subprocess, sys, time
from pathlib import Path
from .common import Book, ROOT, COLLECTIONS, PY, NODE, PIPE, write_json, read_json, log


def weights(book: Book):
    """Seconds of audio per track (from the download index when possible)."""
    idx = read_json(COLLECTIONS / 'index.json', [])
    secs = {}
    for e in idx:
        for t in e.get('tracks', []):
            secs[Path(t['file']).name] = t.get('seconds') or 0
    out = []
    for f in book.tracks():
        out.append(secs.get(f.name) or f.stat().st_size / 9000)   # ~72 kbit/s fallback
    return out


def block_total(book: Book):
    return sum(math.ceil(w / 60) for w in weights(book))


def todo(book: Book, kind):
    root = book.rec if kind == 'fc' else book.zip
    return [i + 1 for i in range(len(book.tracks())) if not (root / f'{i + 1:02}' / 'DONE').exists()]


def split(tracks, wts, n):
    bins = [[0.0, []] for _ in range(max(1, n))]
    for t in sorted(tracks, key=lambda t: -wts[t - 1]):
        b = min(bins, key=lambda b: b[0]); b[0] += wts[t - 1]; b[1].append(t)
    return [sorted(b[1]) for b in bins if b[1]]


def launch(book: Book, kind, workers, threads):
    tracks = todo(book, kind)
    if not tracks:
        log(f'{book.slug}: {kind} already complete'); return []
    wts = weights(book); logs = book.dir / 'logs'; logs.mkdir(exist_ok=True)
    procs = []
    files_json = book.dir / 'files.json'
    write_json(files_json, [str(p) for p in book.tracks()])
    tmp = book.dir / 'tmp'; tmp.mkdir(exist_ok=True)
    groups = split(tracks, wts, workers)
    while len(groups) < workers: groups.append([])      # extra workers share blocks of the same tracks
    for k, group in enumerate(groups):
        rest = [t for t in tracks if t not in group]          # then help with everyone else's tracks (block locks prevent overlap)
        order = group + rest
        env = dict(os.environ, THREADS=str(threads), PYTHONIOENCODING='utf-8')
        if kind == 'fc':
            cmd = [PY, '-m', 'hp.fc_worker', book.slug] + [str(t) for t in order]
            cwd = PIPE
        else:
            cmd = [NODE, 'work/tilawa-legacy/hp-zip.mjs', str(files_json), str(book.zip), str(tmp)] + [str(t) for t in order]
            cwd = ROOT
        out = open(logs / f'{kind}-{k}.log', 'ab')
        procs.append(subprocess.Popen(cmd, cwd=cwd, env=env, stdout=out, stderr=subprocess.STDOUT,
                                      creationflags=0x00000040 if os.name == 'nt' else 0))   # IDLE priority: your own apps stay smooth
        log(f'{book.slug}: {kind} worker {k} -> first tracks {group or "(shared)"}, {sum(wts[t-1] for t in group)/3600:.1f} h of its own')
    return procs


def run(book: Book, which=('fc', 'zip'), fc_workers=3, zip_workers=4, fc_threads=3, zip_threads=2):
    procs = []
    if 'fc' in which: procs += launch(book, 'fc', fc_workers, fc_threads)
    if 'zip' in which: procs += launch(book, 'zip', zip_workers, zip_threads)
    bad = 0
    for p in procs:
        rc = p.wait(); bad += rc != 0
    if bad:
        raise SystemExit(f'{book.slug}: {bad} ASR worker(s) failed - see {book.dir / "logs"}; re-run to resume')
    return True


def status(book: Book):
    n = len(book.tracks()); total = block_total(book)
    def count(root):
        blocks = sum(1 for p in root.glob('*/[0-9][0-9][0-9][0-9].json')) if root.exists() else 0
        done = sum(1 for p in root.glob('*/DONE')) if root.exists() else 0
        return blocks, done
    fb, fd = count(book.rec); zb, zd = count(book.zip)
    return {'tracks': n, 'blocks_total': total, 'fc_blocks': fb, 'fc_tracks_done': fd, 'zip_blocks': zb, 'zip_tracks_done': zd,
            'hours': round(sum(weights(book)) / 3600, 2)}
