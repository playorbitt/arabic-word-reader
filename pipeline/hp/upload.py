"""Internet Archive uploader.  python -m hp ia-check | ia-plan BOOK | upload BOOK [--go] [--mbps N]

Safety rules built in:
  * nothing is sent unless you pass --go (the default is a plan / dry run that only READS archive.org)
  * bandwidth is capped (default 3 MB/s) so it never jams another upload running on the same connection
  * one upload at a time (lock file), resumable: files already on the item with the same size are skipped
  * books marked "ia": {"hold": true} in their config (already being uploaded by hand) are refused unless --force-hold
Keys: env HP_IA_ACCESS / HP_IA_SECRET, or %USERPROFILE%/.hp-ia.ini  ([s3] access=..., secret=...). Never stored in the repo.
Uploads keep the original streamable files (mp3 / ogg / m4a / opus); anything else (mkv, wav, flac) is converted to mp3.
"""
import configparser, html, json, mimetypes, os, re, subprocess, sys, time, urllib.parse, urllib.request, urllib.error
from pathlib import Path
from .common import Book, FFMPEG, OUTPUTS, read_json, write_json, log, remote_name
from . import build

S3 = 'https://s3.us.archive.org'
STREAMABLE = {'.mp3', '.ogg', '.opus', '.m4a'}
UA = {'User-Agent': 'hadith-pipeline/1.0'}


def keys():
    a, s = os.environ.get('HP_IA_ACCESS'), os.environ.get('HP_IA_SECRET')
    if a and s: return a, s
    c = configparser.ConfigParser(); c.read(os.path.expanduser('~/.hp-ia.ini'))
    if 's3' in c: return c['s3']['access'], c['s3']['secret']
    raise SystemExit('No Internet Archive keys: set HP_IA_ACCESS / HP_IA_SECRET or create ~/.hp-ia.ini')


def _auth(): a, s = keys(); return {'authorization': f'LOW {a}:{s}', **UA}


def check():
    req = urllib.request.Request(S3 + '/', headers=_auth())
    body = urllib.request.urlopen(req, timeout=60).read().decode('utf8', 'replace')
    names = re.findall(r'<Name>(.*?)</Name>', body)
    owner = re.search(r'<DisplayName>(.*?)</DisplayName>', body)
    log(f'archive.org login OK as {owner.group(1) if owner else "?"}; {len(names)} item(s): {", ".join(names)}')
    return names


def check_quiet():
    req = urllib.request.Request(S3 + '/', headers=_auth())
    return re.findall(r'<Name>(.*?)</Name>', urllib.request.urlopen(req, timeout=60).read().decode('utf8', 'replace'))


def _uri(s): return 'uri(' + urllib.parse.quote(s, safe='') + ')'


def remote_files(identifier):
    try:
        m = json.loads(urllib.request.urlopen(urllib.request.Request(f'https://archive.org/metadata/{identifier}', headers=UA), timeout=60).read())
    except Exception:
        return None
    if not m: return None
    return {f['name']: int(f.get('size') or 0) for f in m.get('files', [])} if m.get('files') is not None else {}


def meta_for(book: Book, with_sync=True):
    c = book.cfg; ia = c.get('ia') or {}
    q = read_json(book.path('qa.json'), {}) or {}
    title_ar = c.get('title_ar', ''); title_en = c.get('title_en') or book.title
    ident = ia.get('identifier') or f'{book.slug}-audio'
    title = ia.get('title') or (f'{title_en} – Audio ({title_ar})' if title_ar else f'{title_en} – Audio')
    ar_desc = ia.get('description_ar') or (f'تسجيل صوتي لكتاب <strong>{html.escape(title_ar)}</strong>.' if title_ar else '')
    en = ia.get('description_en') or f'Arabic audio recording of <em>{html.escape(title_en)}</em>' + (f' ({html.escape(title_ar)})' if title_ar else '') + '.'
    extra = []
    if c.get('reciter'): extra.append(f'Reciter: {html.escape(str(c["reciter"]))}.')
    if q.get('hadiths') and with_sync:
        extra.append(f'Word-by-word timing data is published alongside the audio ({q["hadiths"]:,} hadiths in the text, {q["coverage_pct"]}% matched to the recording).')
    if 'hadithunlocked' in json.dumps(c.get('text') or {}): extra.append('Arabic text: Hadith Unlocked (hadithunlocked.com).')
    if with_sync: extra.append('Timings were produced automatically (FastConformer and Zipformer speech models) and may be slightly off in places.')
    if c.get('source'): extra.append(f'Original audio source: {html.escape(c["source"])}')
    desc = (f'<p>{ar_desc}</p>' if ar_desc else '') + f'<p>{en}</p><p>' + ' '.join(extra) + '</p>'
    subjects = ia.get('subjects') or ['Arabic', 'Hadith', 'Audio', title_en]
    return {'identifier': ident, 'title': title, 'description': desc, 'subjects': subjects, 'language': ia.get('language', 'ara'),
            'creator': ia.get('creator'), 'collection': ia.get('collection', 'opensource_audio'), 'mediatype': 'audio'}


def files_for(book: Book):
    out = []
    for i, p in enumerate(book.tracks(), 1):
        out.append({'src': str(p), 'name': remote_name(book, i, p),
                    'convert': p.suffix.lower() not in STREAMABLE, 'size': p.stat().st_size})
    return out


class Throttled:
    def __init__(self, path, mbps):
        self.f = open(path, 'rb'); self.rate = mbps * 1048576 if mbps else 0; self.t0 = time.time(); self.sent = 0
    def read(self, n=-1):
        d = self.f.read(n if n and n > 0 else 65536)
        self.sent += len(d)
        if self.rate and d:
            ahead = self.sent / self.rate - (time.time() - self.t0)
            if ahead > 0: time.sleep(ahead)
        return d
    def close(self): self.f.close()


def put(identifier, name, path, headers, mbps, retries=6):
    size = os.path.getsize(path); url = f'{S3}/{identifier}/{urllib.parse.quote(name)}'
    for attempt in range(retries):
        body = Throttled(path, mbps)
        h = {**_auth(), **headers, 'Content-Length': str(size), 'Content-Type': mimetypes.guess_type(name)[0] or 'application/octet-stream'}
        try:
            req = urllib.request.Request(url, data=body, headers=h, method='PUT')
            urllib.request.urlopen(req, timeout=3600).read(); return True
        except urllib.error.HTTPError as e:
            msg = e.read()[:200]
            wait = int(e.headers.get('Retry-After', 30 * (attempt + 1))) if e.code in (429, 503) else 20 * (attempt + 1)
            log(f'   {name}: HTTP {e.code} {msg!r}; retry {attempt + 1}/{retries} in {wait}s')
            if e.code in (400, 401, 403, 404): raise SystemExit(f'archive.org refused {name}: HTTP {e.code} {msg!r}')
            time.sleep(wait)
        except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
            log(f'   {name}: {e}; retry {attempt + 1}/{retries}'); time.sleep(20 * (attempt + 1))
        finally: body.close()
    raise SystemExit(f'giving up on {name} after {retries} tries - re-run to resume')


def put_multipart(identifier, name, path, headers, workers=8, part_mb=24, retries=6, state_file=None):
    """S3 multipart upload over several connections at once (archive.org throttles each connection, not the whole line).
    Resumable: the upload id and finished parts are kept in state_file."""
    import hashlib, threading, math
    from concurrent.futures import ThreadPoolExecutor
    size = os.path.getsize(path); psize = part_mb * 1048576; nparts = max(1, math.ceil(size / psize))
    base = f'{S3}/{identifier}/{urllib.parse.quote(name)}'
    ctype = mimetypes.guess_type(name)[0] or 'application/octet-stream'
    st = read_json(state_file, {}) if state_file else {}
    if st.get('size') != size or st.get('name') != name: st = {}
    def call(method, url, data=None, extra=None, tries=retries):
        for attempt in range(tries):
            req = urllib.request.Request(url, data=data, headers={**_auth(), **(extra or {})}, method=method)
            try:
                r = urllib.request.urlopen(req, timeout=600); return r.read(), r.headers
            except urllib.error.HTTPError as e:
                body = e.read()[:200]
                if e.code in (400, 401, 403, 404) and attempt >= 1: raise SystemExit(f'archive.org refused {method} {name}: HTTP {e.code} {body!r}')
                time.sleep(int(e.headers.get('Retry-After', 15 * (attempt + 1))) if e.code in (429, 503) else 10 * (attempt + 1))
            except (urllib.error.URLError, ConnectionError, TimeoutError):
                time.sleep(10 * (attempt + 1))
        raise SystemExit(f'giving up on {method} {url[-60:]}')
    if not st.get('uploadId'):
        body, _ = call('POST', base + '?uploads', b'', {**headers, 'Content-Type': ctype})
        m = re.search(rb'<UploadId>(.*?)</UploadId>', body)
        if not m: raise SystemExit(f'multipart start failed: {body[:200]!r}')
        st = {'uploadId': m.group(1).decode(), 'size': size, 'name': name, 'parts': {}}
        if state_file: write_json(state_file, st)
    uid = st['uploadId']; done = st['parts']; lock = threading.Lock(); t0 = time.time(); sent = [sum(psize for _ in done)]
    log(f'   multipart: {nparts} part(s) of {part_mb} MB, {workers} connections, {len(done)} already done')
    def part(i):
        if str(i) in done: return
        off = (i - 1) * psize; n = min(psize, size - off)
        with open(path, 'rb') as f: f.seek(off); data = f.read(n)
        _, h = call('PUT', f'{base}?partNumber={i}&uploadId={uid}', data, {'Content-Length': str(n), 'Content-Type': 'application/octet-stream'})
        etag = (h.get('ETag') or '').strip('"') or hashlib.md5(data).hexdigest()
        with lock:
            done[str(i)] = etag; sent[0] += n
            if state_file: write_json(state_file, st)
            el = time.time() - t0
            log(f'   part {i}/{nparts} ok  ({len(done)}/{nparts} done, {sent[0] / 1048576:,.0f} MB sent, {sent[0] / 1048576 / max(el, 1):.2f} MB/s average)')
    with ThreadPoolExecutor(workers) as ex: list(ex.map(part, range(1, nparts + 1)))
    xml = '<CompleteMultipartUpload>' + ''.join(f'<Part><PartNumber>{i}</PartNumber><ETag>"{done[str(i)]}"</ETag></Part>' for i in range(1, nparts + 1)) + '</CompleteMultipartUpload>'
    call('POST', f'{base}?uploadId={uid}', xml.encode(), {'Content-Type': 'application/xml'})
    if state_file: Path(state_file).unlink(missing_ok=True)
    return True


def plan(book: Book, with_sync=True):
    ia = book.cfg.get('ia') or {}
    m = meta_for(book, with_sync); files = files_for(book)
    if not files: raise SystemExit(f'{book.slug}: no audio files')
    remote = remote_files(m['identifier'])
    sync_name = f'{book.slug}-sync.json'
    have_sync = book.path('align.json').exists() and book.path('text.json').exists()
    rows = []
    for f in files:
        done = remote is not None and remote.get(f['name']) == f['size'] and not f['convert']
        rows.append({**f, 'state': 'on archive.org' if done else 'to upload'})
    state = {'identifier': m['identifier'], 'exists': remote is not None, 'hold': bool(ia.get('hold')), 'meta': m, 'files': rows,
             'sync': 'will be generated and uploaded' if have_sync else 'skipped (run text, align, qa, build first)'}
    write_json(book.path('ia-plan.json'), state)
    mb = sum(r['size'] for r in rows if r['state'] == 'to upload') / 1048576
    log(f'{book.slug}: item "{m["identifier"]}" ({"exists" if remote is not None else "new"}), {len(rows)} audio file(s), {mb:,.0f} MB to upload'
        + (f', sync data: {state["sync"]}' if with_sync else ', audio only'))
    if ia.get('hold'): log(f'   HOLD: {ia.get("hold_note") or "marked hold - already being uploaded by hand"}')
    log(f'   title: {m["title"]}'); log(f'   subjects: {m["subjects"]}  language: {m["language"]}')
    return state


def upload(book: Book, go=False, mbps=3.0, force_hold=False, with_sync=True, parts=8, single=False):
    st = plan(book, with_sync)
    if not go:
        log('dry run only - add --go to upload'); return st
    if st['hold'] and not force_hold:
        raise SystemExit(f'{book.slug} is on hold (being uploaded by hand). Use --force-hold if you really want to.')
    lock = OUTPUTS / 'hadith-pipeline' / 'ia-upload.lock'
    if lock.exists() and time.time() - lock.stat().st_mtime < 6 * 3600:
        raise SystemExit(f'another upload is running (lock {lock}); delete it if that is wrong')
    lock.write_text(book.slug)
    try:
        m = st['meta']; ident = m['identifier']; exists = st['exists']
        queue = [r for r in st['files'] if r['state'] == 'to upload']
        tmp = book.dir / 'ia-tmp'; tmp.mkdir(exist_ok=True)
        extra = []
        if with_sync and book.path('align.json').exists():
            p = build.export_sync(book, ident); extra.append({'src': str(p), 'name': p.name, 'convert': False, 'size': p.stat().st_size, 'state': 'to upload'})
        for r in queue + extra:
            src = r['src']
            if r['convert']:
                dst = tmp / r['name']
                log(f'   converting {Path(src).name} -> {r["name"]}')
                subprocess.run([FFMPEG, '-v', 'error', '-y', '-i', src, '-vn', '-ac', '1', '-ar', '44100', '-b:a', '64k', str(dst)], check=True)
                src = str(dst)
            hdr = {'x-archive-size-hint': str(os.path.getsize(src)), 'x-archive-queue-derive': '1'}
            if not exists:
                hdr.update({'x-amz-auto-make-bucket': '1', 'x-archive-meta-mediatype': m['mediatype'], 'x-archive-meta01-collection': m['collection'],
                            'x-archive-meta-title': _uri(m['title']), 'x-archive-meta-description': _uri(m['description']),
                            'x-archive-meta-language': m['language']})
                for i, sub in enumerate(m['subjects'], 1): hdr[f'x-archive-meta{i:02d}-subject'] = _uri(sub)
                if m.get('creator'): hdr['x-archive-meta-creator'] = _uri(m['creator'])
            t0 = time.time(); log(f'   uploading {r["name"]} ({os.path.getsize(src) / 1048576:,.0f} MB) at <= {mbps} MB/s')
            if not single and os.path.getsize(src) >= 64 * 1048576:
                put_multipart(ident, r['name'], src, hdr, workers=parts, state_file=book.path(f'ia-multipart-{r["name"]}.json'))
            else:
                put(ident, r['name'], src, hdr, mbps)
            exists = True
            log(f'   done {r["name"]} in {time.time() - t0:,.0f}s')
            if r['convert']: Path(src).unlink(missing_ok=True)
            s = read_json(book.path('ia-state.json'), {'identifier': ident, 'files': {}}); s['files'][r['name']] = {'size': r['size'], 'at': time.strftime('%Y-%m-%d %H:%M:%S')}
            write_json(book.path('ia-state.json'), s)
        s = read_json(book.path('ia-state.json'), {'identifier': ident, 'files': {}}); s['done'] = True; write_json(book.path('ia-state.json'), s)
        log(f'{book.slug}: upload finished -> https://archive.org/details/{ident}')
    finally:
        lock.unlink(missing_ok=True)
