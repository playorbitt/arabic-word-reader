"""Export a finished book in the hadith.to "main reader" layout (same as public/darimi):

  <slug>/index.json            {"books":[{book,title,section,count,from,to}], "reportBook":{"<n>": book}}
  <slug>/book-<N>.json         {"book":N, "hadith":[{"n":n, "clip":n, "tokens":[{text,start,end,estimated?}]}]}
                               (token times are seconds INSIDE the clip; a hadith with no audio has no "clip" and untimed tokens)
  <slug>/clips/<clip>.mp3      one short mono mp3 per hadith, cut from the recording
  <slug>/manifest.json         counts, sizes, sha256 of every file
  <slug>/registry-entry.json   the object to add to the site's collection list

python -m hp export-site BOOK [--workers 4] [--no-audio] [--zip] [--bitrate 48k]
"""
import hashlib, json, os, subprocess, zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from .common import Book, FFMPEG, read_json, write_json, log, remote_name
from .build import words_for

PAD_BEFORE = 0.15


def _numbers(items):
    nums = [str(i['n']).strip() for i in items]
    if all(n.isdigit() for n in nums) and len(set(nums)) == len(nums):
        return [int(n) for n in nums]
    log('   hadith numbers are not unique integers - using 1..N in book order')
    return list(range(1, len(items) + 1))


def run(book: Book, workers=4, audio=False, make_zip=False, bitrate='48k'):
    text = read_json(book.path('text.json')); rows = read_json(book.path('align.json')); wm = read_json(book.path('wordmatch.json'))
    if not (text and rows and wm): raise SystemExit(f'{book.slug}: run text, align first')
    items, toc = text['items'], text['toc']
    slug = (book.cfg.get('site') or {}).get('slug') or book.slug
    out = book.path('site') / slug; out.mkdir(parents=True, exist_ok=True)
    if audio: (out / 'clips').mkdir(exist_ok=True)
    files = book.tracks(); nums = _numbers(items)
    ok = lambda r: 'start' in r and r['track'] == r['track_end']

    # ---- books (one per section) and the token files -------------------------------------------
    sec_of = {}; books = []
    for c in toc:
        for s in c['s']:
            bk = len(books) + 1
            books.append({'book': bk, 'title': s['t'] or c['t'], 'section': c['t'], 'count': s['b'] - s['a'], 'a': s['a'], 'b': s['b']})
            for k in range(s['a'], s['b']): sec_of[k] = bk
    per_book = {b['book']: [] for b in books}; jobs = []; gi = 0; with_audio = 0
    for k, (it, r) in enumerate(zip(items, rows)):
        n = nums[k]; has = ok(r)
        ws, gi = words_for((it['chain'] + ' ' + it['text']).strip(), r.get('track') if has else -1, wm, gi)
        h = {'n': n}
        if has:
            ts = [x for x in ws if x[1] is not None]
            s0 = min([r['start']] + [x[1] for x in ts]); e0 = max([r['end']] + [x[2] for x in ts])
            c0 = max(0.0, s0 - PAD_BEFORE)
            if all(x[1] is None for x in ws) and ws:                          # no matched words: spread evenly
                L = sum(max(1, len(x[0])) for x in ws); t = c0 + PAD_BEFORE; tot = e0 - t; nw = []
                for x in ws:
                    d = tot * max(1, len(x[0])) / L; nw.append([x[0], t, t + d, 1]); t += d
                ws = nw
            toks = []
            for x in ws:
                if x[1] is None: toks.append({'text': x[0]}); continue
                t = {'text': x[0], 'start': round(max(0.0, x[1] - c0), 2), 'end': round(max(0.0, x[2] - c0), 2)}
                if x[3]: t['estimated'] = True
                toks.append(t)
            if audio:
                h.update(clip=n, tokens=toks)                       # cut-clip mode (--clips)
                jobs.append((n, str(files[r['track'] - 1]), c0, e0 + 0.25 - c0))
            else:
                h.update(src={'f': remote_name(book, r['track'], files[r['track'] - 1]), 's': round(c0, 2), 'e': round(e0 + 0.25, 2)}, tokens=toks)   # stream mode: play the full recording from s to e
            with_audio += 1
        else:
            h['tokens'] = [{'text': x[0]} for x in ws]
        per_book[sec_of[k]].append(h)
    for b in books: write_json(out / f'book-{b["book"]}.json', {'book': b['book'], 'hadith': per_book[b['book']]})
    index = {'books': [{'book': b['book'], 'title': b['title'], 'section': b['section'], 'count': b['count'],
                        'from': min(h['n'] for h in per_book[b['book']]), 'to': max(h['n'] for h in per_book[b['book']])}
                       for b in books if per_book[b['book']]],
             'reportBook': {str(h['n']): bk for bk, hs in per_book.items() for h in hs}}
    if not audio:
        ia = (book.cfg.get('ia') or {}).get('identifier') or f'{book.slug}-audio'
        index['audioBase'] = (book.cfg.get('site') or {}).get('audio_base') or f'https://archive.org/download/{ia}/'
    write_json(out / 'index.json', index)
    reg = {'slug': slug, 'corpus': slug, 'ar': book.cfg.get('title_ar', ''), 'en': book.title, 'count': len(items), 'audio': True, 'books': True,
           'synthetic': False, 'audioSource': book.cfg.get('source')}
    write_json(out / 'registry-entry.json', reg)
    log(f'{book.slug}: {len(books)} books, {len(items)} hadith, {with_audio} with audio -> {out}')

    # ---- clips ----------------------------------------------------------------------------------
    if audio:
        todo = [j for j in jobs if not (out / 'clips' / f'{j[0]}.mp3').exists()]
        log(f'   cutting {len(todo)} clip(s) ({len(jobs) - len(todo)} already done) with {workers} worker(s)')
        def cut(j):
            n, src, ss, dur = j; dst = out / 'clips' / f'{n}.mp3'; tmp = dst.with_suffix('.part.mp3')
            subprocess.run([FFMPEG, '-v', 'error', '-y', '-ss', f'{ss:.3f}', '-t', f'{dur:.3f}', '-i', src, '-vn', '-ac', '1', '-ar', '24000',
                            '-b:a', bitrate, str(tmp)], check=True, creationflags=0x00004000 if os.name == 'nt' else 0)
            os.replace(tmp, dst)
        with ThreadPoolExecutor(workers) as ex:
            for i, _ in enumerate(ex.map(cut, todo), 1):
                if i % 200 == 0: log(f'   {i}/{len(todo)} clips')
    # ---- manifest ---------------------------------------------------------------------------------
    man = {'slug': slug, 'hadith': len(items), 'with_audio': with_audio, 'books': len(index['books']), 'files': {}}
    for p in sorted(out.rglob('*')):
        if p.is_file() and p.name != 'manifest.json':
            man['files'][p.relative_to(out).as_posix()] = {'size': p.stat().st_size, 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
    write_json(out / 'manifest.json', man)
    mb = sum(v['size'] for v in man['files'].values()) / 1048576
    log(f'   package: {len(man["files"])} files, {mb:,.0f} MB')
    if make_zip:
        z = book.path('site') / f'{slug}-main-reader.zip'
        with zipfile.ZipFile(z, 'w', zipfile.ZIP_STORED) as zf:
            for p in sorted(out.rglob('*')):
                if p.is_file(): zf.write(p, Path(slug) / p.relative_to(out))
        log(f'   zip: {z} ({z.stat().st_size / 1048576:,.0f} MB)')
    return out
