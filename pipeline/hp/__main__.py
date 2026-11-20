"""hadith pipeline CLI.   Run from work/hadith-pipeline:   python -m hp <command>

  scaffold               create books/<slug>.json for every collection in the download index
  list                   one line per book: audio hours, stage status
  status [--html]        progress of every book; --html writes outputs/hadith-pipeline/status.html
  run BOOK [options]     run stages: prep, text, asr, align, qa, build  (default: all, in order)
        --stages a,b     only these stages           --force   redo prep/text even if marked done
        --fc-workers N   --zip-workers N             --fc-threads N  --zip-threads N
  textfind [BOOK..]      find + validate a machine-readable text source for each book (sets text.url when one is found)
  textcheck BOOK         sanity-check text.json (cut-off hadiths, numbering, empties, edition vs audio)
  shamela BOOK --id N    download a Shamela book page by page and build source-text.json (resumable)
  export-site BOOK       package a finished book for hadith.to: index.json + book-N.json with start/stop times into the full recording (add --clips to cut mp3s)
  ia-check               test the archive.org login (read only)
  ia-plan BOOK           show what would be uploaded (read only)
  upload BOOK [--go]     upload audio + sync json; without --go it is a dry run. --mbps N caps speed (default 3)
  serve [PORT]           static server with Range support (default 8767) for the previews
"""
import argparse, sys, time, json
from .common import Book, BOOKS, OUTPUTS, COLLECTIONS, read_json, write_json, log
from . import prep, text, asr, align, qa, build, textfind, textcheck, upload, shamela, export_site

STAGES = ['prep', 'text', 'asr', 'align', 'qa', 'build']


def scaffold():
    idx = read_json(COLLECTIONS / 'index.json', [])
    by = {}
    for e in idx: by.setdefault(e['collection'], []).append(e)
    BOOKS.mkdir(parents=True, exist_ok=True); made = 0
    for coll, es in by.items():
        for e in es:
            slug = coll if len(es) == 1 else f'{coll}-{e["id"]}'
            p = BOOKS / f'{slug}.json'
            if p.exists(): continue
            write_json(p, {'slug': slug, 'collection': coll, 'title_ar': e['title'], 'title_en': coll.replace('-', ' ').title(),
                           'reciter': e.get('narrator'), 'source': e['source'],
                           'audio_dir': f'outputs/rawy-hadith-collections/{coll}/rawy-{e["id"]}',
                           'text': {}})
            made += 1
    log(f'scaffolded {made} new book config(s) in {BOOKS}')


def all_books():
    return sorted(p.stem for p in BOOKS.glob('*.json'))


def state(book): return read_json(book.path('state.json'), {})
def mark(book, stage, **kw):
    s = state(book); s[stage] = dict(done=True, at=time.strftime('%Y-%m-%d %H:%M:%S'), **kw); write_json(book.path('state.json'), s)


def run(slug, stages, force, a):
    book = Book(slug); st = state(book)
    for s in stages:
        log(f'=== {slug}: {s} ===')
        if s == 'prep':
            if st.get('prep', {}).get('done') and not force and 'prep' not in a.stages_given: continue
            if not prep.run(book): raise SystemExit(f'{slug}: audio is not ready; finish the download first')
            mark(book, 'prep')
        elif s == 'text':
            if st.get('text', {}).get('done') and not force and 'text' not in a.stages_given: continue
            if not book.cfg.get('text'): raise SystemExit(f'{slug}: no text source in books/{slug}.json ("text": {{"url":...}} or {{"path":...}})')
            out = text.normalize(book); textcheck.run(book); mark(book, 'text', hadiths=len(out['items']))
        elif s == 'asr':
            asr.run(book, fc_workers=a.fc_workers, zip_workers=a.zip_workers, fc_threads=a.fc_threads, zip_threads=a.zip_threads)
            mark(book, 'asr')
        elif s == 'align': mark(book, 'align', **align.run(book))
        elif s == 'qa': rep = qa.run(book); mark(book, 'qa', coverage_pct=rep['coverage_pct'])
        elif s == 'build': build.run(book); mark(book, 'build')
    log(f'{slug}: finished stages {",".join(stages)}')


def status_rows():
    rows = []
    for slug in all_books():
        try: b = Book(slug)
        except SystemExit: continue
        n = len(b.tracks())
        if not n: rows.append({'slug': slug, 'title': b.title, 'tracks': 0, 'note': 'no audio files'}); continue
        a = asr.status(b); s = state(b); q = read_json(b.path('qa.json'), {})
        rows.append({'slug': slug, 'title': b.title, 'tracks': n, 'hours': a['hours'],
                     'fc_pct': round(100 * a['fc_blocks'] / max(1, a['blocks_total']), 1),
                     'zip_pct': round(100 * a['zip_blocks'] / max(1, a['blocks_total']), 1),
                     'stages': [k for k in STAGES if s.get(k, {}).get('done')],
                     'coverage': q.get('coverage_pct'), 'hadiths': q.get('hadiths'),
                     'preview': (b.path('preview.html').relative_to(OUTPUTS).as_posix() if b.path('preview.html').exists() else None),
                     'has_text': bool(b.cfg.get('text'))})
    return rows


def status_html():
    rows = status_rows(); tr = ''
    for r in rows:
        bar = lambda p: f'<div class="b"><i style="width:{p or 0}%"></i></div><small>{p or 0}%</small>'
        tr += (f'<tr><td><b>{r["slug"]}</b><br><small>{r.get("title","")}</small></td><td>{r.get("tracks","")}</td><td>{r.get("hours","")}</td>'
               f'<td>{bar(r.get("fc_pct")) if r.get("tracks") else ""}</td><td>{bar(r.get("zip_pct")) if r.get("tracks") else ""}</td>'
               f'<td>{" → ".join(r.get("stages", [])) or "—"}</td><td>{("%s%%" % r["coverage"]) if r.get("coverage") is not None else "—"}</td>'
               f'<td>{"yes" if r.get("has_text") else "<span style=color:#b00>no text source</span>"}</td>'
               f'<td>{("<a href=/" + r["preview"] + ">open</a>") if r.get("preview") else ""}</td></tr>')
    html = ('<!doctype html><meta charset=utf-8><meta http-equiv=refresh content=20><title>Hadith pipeline</title>'
            '<style>body{font:15px system-ui;margin:20px;background:#fafafa;color:#222}table{border-collapse:collapse;width:100%;background:#fff}'
            'td,th{border-bottom:1px solid #eee;padding:8px;text-align:left;vertical-align:top}.b{background:#eee;border-radius:4px;height:8px;width:110px}'
            '.b i{display:block;height:8px;background:#1f6f5c;border-radius:4px}small{color:#777}</style>'
            f'<h2>Hadith pipeline</h2><small>updated {time.strftime("%H:%M:%S")} · refreshes every 20 s</small>'
            '<table><tr><th>Book</th><th>Tracks</th><th>Hours</th><th>FastConformer</th><th>Zipformer</th><th>Stages done</th><th>Audio coverage</th><th>Text</th><th>Preview</th></tr>'
            + tr + '</table>')
    out = OUTPUTS / 'hadith-pipeline' / 'status.html'; out.parent.mkdir(parents=True, exist_ok=True); out.write_text(html, encoding='utf8')
    return out


def status_html():          # full dashboard (replaces the simple table above)
    from .dashboard import render
    return render()


def main():
    ap = argparse.ArgumentParser(prog='hp'); sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('scaffold'); sub.add_parser('list')
    s = sub.add_parser('status'); s.add_argument('--html', action='store_true')
    r = sub.add_parser('run'); r.add_argument('book'); r.add_argument('--stages'); r.add_argument('--force', action='store_true')
    for k, d in (('fc-workers', 3), ('zip-workers', 4), ('fc-threads', 3), ('zip-threads', 2)): r.add_argument('--' + k, type=int, default=d)
    aa = sub.add_parser('asr-all'); aa.add_argument('--books', help='comma list; default = every book with complete audio')
    for k, d in (('fc-workers', 3), ('zip-workers', 4), ('fc-threads', 3), ('zip-threads', 2)): aa.add_argument('--' + k, type=int, default=d)
    tf = sub.add_parser('textfind'); tf.add_argument('books', nargs='*')
    tc = sub.add_parser('textcheck'); tc.add_argument('book')
    sm = sub.add_parser('shamela'); sm.add_argument('book'); sm.add_argument('--id', type=int); sm.add_argument('--limit', type=int); sm.add_argument('--delay', type=float, default=0.35)
    es = sub.add_parser('export-site'); es.add_argument('book'); es.add_argument('--workers', type=int, default=4); es.add_argument('--clips', action='store_true', help='cut one mp3 per hadith (off by default: the site streams the full recording)')
    es.add_argument('--zip', action='store_true'); es.add_argument('--bitrate', default='48k')
    sub.add_parser('ia-check')
    ip = sub.add_parser('ia-plan'); ip.add_argument('book')
    up = sub.add_parser('upload'); up.add_argument('book'); up.add_argument('--go', action='store_true'); up.add_argument('--mbps', type=float, default=3.0)
    up.add_argument('--force-hold', action='store_true'); up.add_argument('--parts', type=int, default=8, help='parallel connections for big files'); up.add_argument('--single', action='store_true', help='one plain PUT, no multipart'); up.add_argument('--no-sync', action='store_true')
    sv = sub.add_parser('serve'); sv.add_argument('port', nargs='?', type=int, default=8767)
    a = ap.parse_args()
    if a.cmd == 'scaffold': scaffold()
    elif a.cmd in ('list', 'status'):
        rows = status_rows()
        for x in rows:
            print(f'{x["slug"]:34} {x.get("tracks", 0):>3} tracks {x.get("hours", 0):>6} h  fc {x.get("fc_pct", 0):>5}%  zip {x.get("zip_pct", 0):>5}%  '
                  f'done: {",".join(x.get("stages", [])) or "-"}  cov: {x.get("coverage")}  text: {"yes" if x.get("has_text") else "NO"}')
        if getattr(a, 'html', False): print('wrote', status_html())
    elif a.cmd == 'run':
        a.fc_workers, a.zip_workers, a.fc_threads, a.zip_threads = a.__dict__['fc_workers'], a.__dict__['zip_workers'], a.__dict__['fc_threads'], a.__dict__['zip_threads']
        a.stages_given = a.stages.split(',') if a.stages else []
        run(a.book, a.stages_given or STAGES, a.force, a)
    elif a.cmd == 'asr-all':
        names = a.books.split(',') if a.books else all_books()
        todo = []
        for n in names:
            b = Book(n)
            if b.cfg.get('skip'): continue
            if not b.tracks(): prep.run(b, deep=False)        # joins finished .part pieces, may make the book ready
            if not b.tracks(): continue
            todo.append((sum(asr.weights(b)), n))
        for _, n in sorted(todo):                      # shortest books first: results arrive early
            b = Book(n)
            if b.cfg.get('skip'): log(f'{n}: marked skip, not processing'); continue
            try:
                if not prep.run(b, deep=False): log(f'{n}: audio incomplete, skipped'); continue
                asr.run(b, fc_workers=a.fc_workers, zip_workers=a.zip_workers, fc_threads=a.fc_threads, zip_threads=a.zip_threads)
                mark(b, 'asr'); status_html()
            except SystemExit as e:
                log(f'{n}: FAILED - {e}')
        log('asr-all finished')
    elif a.cmd == 'textfind': textfind.run(a.books or None)
    elif a.cmd == 'textcheck': textcheck.run(Book(a.book))
    elif a.cmd == 'shamela': shamela.run(Book(a.book), a.id, a.limit, a.delay)
    elif a.cmd == 'export-site': export_site.run(Book(a.book), a.workers, a.clips, a.zip, a.bitrate)
    elif a.cmd == 'ia-check': upload.check()
    elif a.cmd == 'ia-plan': upload.plan(Book(a.book))
    elif a.cmd == 'upload': upload.upload(Book(a.book), go=a.go, mbps=a.mbps, force_hold=a.force_hold, with_sync=not a.no_sync, parts=a.parts, single=a.single)
    elif a.cmd == 'serve':
        from .serve import serve; serve(a.port)


if __name__ == '__main__':
    main()
