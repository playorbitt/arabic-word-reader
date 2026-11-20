"""Stage helper: find a machine-readable text for each book and validate it before it is used.

`python -m hp textfind [BOOK ...]` probes the sources we know, writes outputs/hadith-pipeline/text-sources.json
and, for a book with a validated hadithunlocked JSON, sets "text": {"url": ...} in its config.

Sources
  hadithunlocked  https://hadithunlocked.com/<alias>.json  - structured JSON, probed automatically
  sunnah          https://sunnah.com/<key>                 - behind a bot check: must be fetched through the browser pane
  turath          https://app.turath.io (book id)          - page text only, needs hadith segmentation (not automatic yet)
"""
import json, re, urllib.request
from .common import Book, BOOKS, OUTPUTS, read_json, write_json, log

# slug -> candidate sources. Only entries whose alias really exists are probed; unknown books just get "none known".
HADITHUNLOCKED = {
    'sahih-ibn-khuzaymah': ['ibnkhuzaymah'],
    'al-lulu-wal-marjan': ['lulu-marjan'],
    'al-shamail-al-muhammadiyyah': ['shamail'],
    'nawawi-forty-with-ibn-rajab': ['ibnrajab50'],
    'sunan-al-darimi-3369': ['darimi'], 'sunan-al-darimi-8751': ['darimi'],
}
SUNNAH = {
    'bulugh-al-maram': 'bulugh', 'nawawi-forty-with-ibn-rajab': 'nawawi40', 'al-shamail-al-muhammadiyyah': 'shamail',
    'sahih-ibn-khuzaymah': 'ibnkhuzayma',
}
UA = {'User-Agent': 'Mozilla/5.0 hadith-pipeline'}


def _get(url, limit=None):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read() if limit is None else r.read(limit)


def probe_hadithunlocked(alias):
    url = f'https://hadithunlocked.com/{alias}.json'
    try:
        data = _get(url); raw = json.loads(data)
    except Exception as e:
        return {'source': 'hadithunlocked', 'url': url, 'ok': False, 'error': str(e)[:120]}
    n = 0; has_ar = 0; chain = 0
    def walk(x):
        nonlocal n, has_ar, chain
        if isinstance(x, dict):
            for it in x.get('items', []) or []:
                n += 1
                t = (it.get('text') or {}).get('ar') or ''
                has_ar += bool(t.strip()); chain += bool(((it.get('chain') or {}).get('ar') or '').strip())
            for k in ('sections', 'subsections', 'chapters'):
                for c in x.get(k, []) or []: walk(c)
    walk(raw)
    return {'source': 'hadithunlocked', 'url': url, 'ok': n > 0 and has_ar >= 0.95 * n, 'items': n,
            'with_arabic_pct': round(100 * has_ar / max(1, n), 1), 'with_chain_pct': round(100 * chain / max(1, n), 1),
            'title': (raw.get('book') or {}).get('alias') if isinstance(raw, dict) else None, 'bytes': len(data)}


def run(slugs=None):
    slugs = slugs or sorted(p.stem for p in BOOKS.glob('*.json'))
    out = {}
    for slug in slugs:
        cands = []
        for alias in HADITHUNLOCKED.get(slug, []):
            c = probe_hadithunlocked(alias); cands.append(c)
            log(f'{slug}: hadithunlocked/{alias} -> {"OK, %s hadiths" % c.get("items") if c["ok"] else "FAILED"}')
        if slug in SUNNAH:
            cands.append({'source': 'sunnah', 'url': f'https://sunnah.com/{SUNNAH[slug]}', 'ok': None,
                          'note': 'bot-protected; fetch through the browser pane, save as a list of {number,text,chain}'})
        if not cands:
            cands.append({'source': 'none', 'ok': False, 'note': 'no known machine-readable source; try turath.io (needs segmentation) or a local file'})
        out[slug] = cands
        good = next((c for c in cands if c.get('ok') and c['source'] == 'hadithunlocked'), None)
        cfgp = BOOKS / f'{slug}.json'
        cfg = read_json(cfgp)
        if good and cfg is not None and not cfg.get('text') and not cfg.get('skip'):
            cfg['text'] = {'url': good['url']}; write_json(cfgp, cfg); log(f'{slug}: text source set to {good["url"]}')
    dest = OUTPUTS / 'hadith-pipeline' / 'text-sources.json'; dest.parent.mkdir(parents=True, exist_ok=True)
    write_json(dest, out)
    return out
