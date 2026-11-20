"""Stage: build. Produce <work>/preview.html - the reader with word-by-word sync, search, English toggle,
click-a-word-to-seek, auto-scroll and a player bar. Audio is referenced (not embedded) so the page stays light."""
import bisect, json, re
from pathlib import Path
from .common import ROOT, Book, OUTPUTS, fold, token_folds, read_json, log, remote_name

_JUNK = re.compile(r'[:،\s.❖…]')
_FOOT = re.compile(r'\[\^\d+\]')


def _lis(known, key):
    tl, ti, pv = [], [], {}
    for i in known:
        v = key(i); p = bisect.bisect_right(tl, v)
        if p == len(tl): tl.append(v); ti.append(i)
        else: tl[p] = v; ti[p] = i
        pv[i] = ti[p - 1] if p else None
    keep = []; k = ti[-1] if ti else None
    while k is not None: keep.append(k); k = pv[k]
    return sorted(keep)


def words_for(raw_text, track, wm, gi):
    """Return (words, next global index). words = [text, start, end, estimated] per displayed word.
    Punctuation-only marks are glued to the neighbouring word; the sign for peace-be-upon-him counts as the 4 words spoken."""
    toks = []; pend = ''
    for w in raw_text.split():
        w = _FOOT.sub('', w)
        if not _JUNK.sub('', w):
            if toks: toks[-1][0] += ' ' + w
            else: pend = (pend + ' ' + w).strip()
            continue
        if pend: w = pend + ' ' + w; pend = ''
        folds = token_folds(w)
        if not folds: toks.append([w, '', None, 0]); continue
        ms = [wm.get(str(gi + k)) for k in range(len(folds))]
        ms = [m for m in ms if m and m[0] == track]
        timing = [ms[0][0], min(m[1] for m in ms), max(m[2] for m in ms)] if ms else None
        toks.append([w, ''.join(folds), timing, sum(len(f) for f in folds)]); gi += len(folds)
    out = [[w, None, None, 0] for w, *_ in toks]
    known = _lis([i for i, t in enumerate(toks) if t[2] and t[2][0] == track], lambda i: toks[i][2][1])
    for i in known: out[i] = [toks[i][0], toks[i][2][1], toks[i][2][2], 0]
    if known:
        a, b = known[0], known[-1]
        for i in range(a - 1, -1, -1): out[i] = [toks[i][0], max(0, out[i + 1][1] - 0.3), out[i + 1][1], 1]
        for i in range(b + 1, len(toks)): out[i] = [toks[i][0], out[i - 1][2], out[i - 1][2] + 0.3, 1]
        for x, y in zip(known, known[1:]):
            if y - x > 1:
                L = sum(max(1, toks[j][3]) for j in range(x + 1, y)); t0 = out[x][2]; t1 = max(out[y][1], t0); c = t0
                for j in range(x + 1, y):
                    d = (t1 - t0) * max(1, toks[j][3]) / L; out[j] = [toks[j][0], c, c + d, 1]; c += d
    return out, gi


def export_sync(book: Book, identifier=None):
    """<work>/<slug>-sync.json: everything a player needs - track list, per-hadith start/end and word timings."""
    text = read_json(book.path('text.json')); rows = read_json(book.path('align.json')); wm = read_json(book.path('wordmatch.json'))
    ok = lambda r: 'start' in r and r['track'] == r['track_end']
    idx = read_json(ROOT / 'outputs' / 'rawy-hadith-collections' / 'index.json', [])
    secs = {Path(t['file']).name: t.get('seconds') for e in idx for t in e.get('tracks', [])}
    secof = {}
    for ci, c in enumerate(text['toc']):
        for si, sec in enumerate(c['s']):
            for k in range(sec['a'], sec['b']): secof[k] = (ci, si)
    out = []; gi = 0
    for k, (it, r) in enumerate(zip(text['items'], rows)):
        has = ok(r)
        ws, gi = words_for((it['chain'] + ' ' + it['text']).strip(), r.get('track') if has else -1, wm, gi)
        h = {'n': it['n'], 'chapter': secof.get(k, (None, None))[0], 'section': secof.get(k, (None, None))[1]}
        if has:
            ts = [x for x in ws if x[1] is not None]
            s0 = min([r['start']] + [x[1] for x in ts]); e0 = max([r['end']] + [x[2] for x in ts])
            h.update(audio=True, track=r['track'], start=round(s0, 2), end=round(e0, 2),
                     words=[[x[0], round(x[1], 2), round(x[2], 2), x[3]] if x[1] is not None else [x[0]] for x in ws])
        else:
            h.update(audio=False, words=[[x[0]] for x in ws])
        if it.get('en'): h['en'] = it['en'].lstrip('✧ ').strip()
        out.append(h)
    doc = {'format': 'hadith-sync/1', 'book': book.slug, 'title_ar': book.cfg.get('title_ar', ''), 'title_en': book.title,
           'identifier': identifier,
           'tracks': [{'n': i + 1, 'file': remote_name(book, i + 1, p), 'seconds': secs.get(p.name)} for i, p in enumerate(book.tracks())],
           'toc': [{'t': c['t'], 'e': c['e'], 's': [{'t': s['t'], 'a': s['a'], 'b': s['b']} for s in c['s']]} for c in text['toc']],
           'hadiths': out,
           'notes': 'start/end/word times are seconds inside the track file. word = [text, start, end, estimated(1) or matched(0)]; '
                    'estimated words sit between matched neighbours. audio=false means the recording does not read that hadith.'}
    dest = book.path(f'{book.slug}-sync.json'); dest.write_text(json.dumps(doc, ensure_ascii=False, separators=(',', ':')), encoding='utf8')
    log(f'{book.slug}: {dest.name} written ({dest.stat().st_size // 1024} KB)')
    return dest


def run(book: Book):
    text = read_json(book.path('text.json')); rows = read_json(book.path('align.json')); wm = read_json(book.path('wordmatch.json'))
    items, toc = text['items'], text['toc']
    files = [p.name for p in book.tracks()]
    ok = lambda r: 'start' in r and r['track'] == r['track_end']
    out_rows = []; gi = 0; nospan = 0
    for it, r in zip(items, rows):
        has = ok(r)
        ws, gi = words_for((it['chain'] + ' ' + it['text']).strip(), r.get('track') if has else -1, wm, gi)
        s = r.get('start') if has else None; e = r.get('end') if has else None
        if has:
            if all(x[1] is None for x in ws) and ws:            # no matched words: spread evenly
                L = sum(max(1, len(fold(x[0]))) for x in ws); c = s; tot = e - s; nw = []
                for x in ws:
                    d = tot * max(1, len(fold(x[0]))) / L; nw.append([x[0], c, c + d, 1]); c += d
                ws = nw; nospan += 1
            ts = [x for x in ws if x[1] is not None]
            if ts: s = min(s, min(x[1] for x in ts)); e = max(e, max(x[2] for x in ts))
            ws = [[x[0], round(x[1], 2), round(x[2], 2), x[3]] if x[1] is not None else [x[0]] for x in ws]
        else:
            ws = [[x[0]] for x in ws]
        out_rows.append({'n': it['n'], 'w': ws, 'tr': r.get('track') if has else None,
                         's': round(s, 2) if has else None, 'e': round(e, 2) if has else None,
                         'en': (it.get('en') or '').lstrip('✧ ').strip(), 'g': it.get('grade', '')})
    tpl = (Path(__file__).parent / 'templates' / 'reader.html').read_text(encoding='utf8')
    base = '/' + book.audio_dir.relative_to(OUTPUTS).as_posix() + '/'
    page = (tpl.replace('__TITLE_EN__', book.title).replace('__TITLE_AR__', book.cfg.get('title_ar', ''))
            .replace('__BASE__', base).replace('__FILES__', json.dumps(files))
            .replace('__TOC__', json.dumps(toc, ensure_ascii=False)).replace('__ROWS__', json.dumps(out_rows, ensure_ascii=False)))
    dest = book.path('preview.html'); dest.write_text(page, encoding='utf8')
    log(f'{book.slug}: preview.html written ({len(page) // 1024} KB) -> /' + dest.relative_to(OUTPUTS).as_posix())
    return dest
