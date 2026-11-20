"""Stage: text. Get the printed hadith text and normalise it to <work>/text.json.

text.json = {"items":[{"n","chain","text","en","grade"}], "toc":[{"t","e","s":[{"t","a","b"}]}], "source":...}
items are in book order; toc sections point at item ranges [a, b).
Accepted raw formats:
  * hadithunlocked.com book JSON (chapters > sections > subsections > items)
  * a plain list of {"number"/"n", "text"/"ar", ["chain"], ["en"]}
"""
import json, re, urllib.request
from .common import Book, write_json, read_json, log


def fetch(book: Book):
    t = book.cfg.get('text') or {}
    raw = book.dir / 'raw-text.json'
    if t.get('source') == 'url' or t.get('url'):
        if raw.exists() and not t.get('refresh'):
            log('raw text already downloaded:', raw); return raw
        log('downloading', t['url'])
        req = urllib.request.Request(t['url'], headers={'User-Agent': 'hadith-pipeline/1.0'})
        data = urllib.request.urlopen(req, timeout=120).read()
        raw.write_bytes(data); return raw
    if t.get('path'):
        from pathlib import Path
        from .common import ROOT
        p = Path(t['path']); return p if p.is_absolute() else ROOT / p
    raise SystemExit(f'{book.slug}: config has no "text" source (url or path)')


def _walk(node, chapter_title, path, out_items, out_secs):
    if isinstance(node, dict):
        title = (node.get('title') or {})
        p = path + ([title.get('ar')] if isinstance(title, dict) and title.get('ar') else [])
        if node.get('items'):
            a = len(out_items)
            for it in node['items']:
                out_items.append(_item(it))
            out_secs.append({'t': ' › '.join(p), 'a': a, 'b': len(out_items)})
        for k in ('sections', 'subsections'):
            for c in node.get(k, []) or []:
                _walk(c, chapter_title, p, out_items, out_secs)


def _item(it):
    g = ((it.get('metadata') or {}).get('grades') or [{}])
    return {'n': str(it.get('number') or it.get('n') or ''),
            'chain': ((it.get('chain') or {}).get('ar') if isinstance(it.get('chain'), dict) else it.get('chain')) or '',
            'text': ((it.get('text') or {}).get('ar') if isinstance(it.get('text'), dict) else (it.get('text') or it.get('ar'))) or '',
            'en': ((it.get('text') or {}).get('en') if isinstance(it.get('text'), dict) else it.get('en')) or '',
            'grade': (g[0] or {}).get('grade_en', '') if g else ''}


def normalize(book: Book):
    raw_path = fetch(book)
    raw = json.loads(open(raw_path, encoding='utf8').read())
    items, toc = [], []
    if isinstance(raw, dict) and raw.get('chapters'):
        for ch in raw['chapters']:
            secs = []
            for s in ch.get('sections', []):
                _walk(s, ch['title'].get('ar', ''), [], items, secs)
            toc.append({'t': ch['title'].get('ar', ''), 'e': ch['title'].get('en', ''), 's': secs})
    elif isinstance(raw, list) and raw and isinstance(raw[0], dict) and 'kitab_name' in raw[0]:
        # flat list: {id, kitab_name, bab_name, rawi, content, hint}  (e.g. Umdat al-Ahkam)
        cur = None
        for r in raw:
            items.append({'n': str(r.get('id')), 'chain': '', 'text': r.get('content') or '', 'en': '', 'grade': ''})
            if cur is None or cur['t'] != r.get('kitab_name'):
                cur = {'t': r.get('kitab_name') or '', 'e': '', 's': []}; toc.append(cur)
            bab = r.get('bab_name') or ''
            if cur['s'] and cur['s'][-1]['t'] == bab: cur['s'][-1]['b'] = len(items)
            else: cur['s'].append({'t': bab, 'a': len(items) - 1, 'b': len(items)})
    elif isinstance(raw, list):
        for it in raw:
            items.append(_item(it))
        toc = [{'t': book.cfg.get('title_ar', ''), 'e': book.title, 's': [{'t': book.cfg.get('title_ar', ''), 'a': 0, 'b': len(items)}]}]
    else:
        raise SystemExit('unrecognised raw text format')
    out = {'items': items, 'toc': toc, 'source': str(raw_path.name)}
    write_json(book.path('text.json'), out)
    log(f'{book.slug}: text.json written, {len(items)} hadiths, {sum(len(c["s"]) for c in out["toc"])} sections')
    return out
