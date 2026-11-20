"""Stage: prep. Verify the audio is complete and usable before spending hours of CPU on it.
 - every track in the download index exists with the expected byte size
 - leftover .part pieces are joined when they are complete and contiguous
 - the file decodes (ffmpeg reads the first seconds) and its length matches the index
Writes <work>/audio-report.json. Returns True when the book is ready for ASR."""
import re, subprocess
from pathlib import Path
from .common import Book, COLLECTIONS, FFMPEG, read_json, write_json, log


def _index_tracks(book: Book):
    idx = read_json(COLLECTIONS / 'index.json', [])
    want = Path(book.cfg['audio_dir']).name       # e.g. rawy-10028
    out = []
    for e in idx:
        if e['collection'] == book.cfg.get('collection') and f"rawy-{e['id']}" == want:
            out = e['tracks']
    return out


def _join_parts(target: Path, total: int):
    parts = []
    for p in target.parent.glob(target.name + '.*.part'):
        m = re.search(r'\.(\d+)\.part$', p.name)
        if m: parts.append((int(m.group(1)), p))
    parts.sort()
    pos = 0
    for start, p in parts:
        if start != pos: return False
        pos += p.stat().st_size
    if pos != total or not parts: return False
    with open(target, 'wb') as out:
        for _, p in parts:
            out.write(p.read_bytes())
    for _, p in parts: p.unlink()
    return True


def _duration(path: Path):
    r = subprocess.run([FFMPEG, '-hide_banner', '-i', str(path)], capture_output=True, text=True)
    m = re.search(r'Duration:\s*(\d+):(\d+):(\d+\.\d+)', r.stderr)
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else None


def run(book: Book, deep=True):
    tracks = _index_tracks(book); issues = []; rows = []
    if not tracks:
        files = book.tracks()
        tracks = [{'file': str(f.relative_to(COLLECTIONS)).replace('\\', '/'), 'bytes': f.stat().st_size, 'seconds': None} for f in files]
    for i, t in enumerate(tracks, 1):
        p = COLLECTIONS / t['file']; row = {'track': i, 'file': p.name}
        if not p.exists() or (t.get('bytes') and p.stat().st_size != t['bytes']):
            if t.get('bytes') and _join_parts(p, t['bytes']):
                log(f'{book.slug}: joined parts for {p.name}')
            else:
                have = p.stat().st_size if p.exists() else 0
                parts = sum(q.stat().st_size for q in p.parent.glob(p.name + '.*.part'))
                row['problem'] = f"incomplete: {have + parts} of {t.get('bytes')} bytes"
                issues.append(f'track {i}: {row["problem"]}'); rows.append(row); continue
        if deep:
            d = _duration(p); row['seconds'] = d
            if d is None:
                row['problem'] = 'cannot read duration'; issues.append(f'track {i}: unreadable')
            elif t.get('seconds') and abs(d - t['seconds']) > max(30, 0.02 * t['seconds']):
                row['warning'] = f'length {d:.0f}s differs from index {t["seconds"]}s'
        rows.append(row)
    ready = not issues
    rep = {'book': book.slug, 'ready': ready, 'tracks': rows, 'issues': issues,
           'hours': round(sum((r.get('seconds') or 0) for r in rows) / 3600, 2)}
    write_json(book.path('audio-report.json'), rep)
    log(f'{book.slug}: audio {"OK" if ready else "NOT READY"} - {len(rows)} tracks, {rep["hours"]} h' + (f', {len(issues)} issue(s)' if issues else ''))
    return ready
