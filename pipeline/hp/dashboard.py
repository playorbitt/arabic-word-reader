"""Full status page: what is running, what is queued, what needs attention, what is done.
Written to outputs/hadith-pipeline/status.html (auto-refresh 20 s). Safe to run repeatedly."""
import html, json, math, subprocess, time
from pathlib import Path
from .common import Book, BOOKS, OUTPUTS, COLLECTIONS, read_json, write_json
from . import asr

STAGES = ['prep', 'text', 'asr', 'align', 'qa', 'build']
STAGE_NAMES = {'prep': 'Audio', 'text': 'Text', 'asr': 'Models', 'align': 'Match', 'qa': 'QA', 'build': 'Reader'}


def _sys():
    try:
        out = subprocess.run(['powershell', '-NoProfile', '-Command',
            "$o=Get-CimInstance Win32_OperatingSystem;$c=(Get-CimInstance Win32_Processor|Measure-Object LoadPercentage -Average).Average;"
            "$w=(Get-CimInstance Win32_Process|?{($_.Name -match 'python|node') -and ($_.CommandLine -match 'hp\\.fc_worker|hp-zip')}).Count;"
            "'{0:N1}|{1:N1}|{2}|{3}' -f (($o.TotalVisibleMemorySize-$o.FreePhysicalMemory)/1MB),($o.TotalVisibleMemorySize/1MB),$c,$w"],
            capture_output=True, text=True, timeout=20).stdout.strip().split('|')
        return {'ram_used': float(out[0]), 'ram_total': float(out[1]), 'cpu': out[2], 'workers': int(out[3])}
    except Exception:
        return {}


def _audio_progress(book, idx):
    """(bytes present, bytes expected, tracks complete, tracks total) from the download index, counting .part pieces."""
    want = Path(book.cfg['audio_dir']).name; tracks = []
    for e in idx:
        if e['collection'] == book.cfg.get('collection') and f"rawy-{e['id']}" == want: tracks = e['tracks']
    have = exp = full = 0
    for t in tracks:
        p = COLLECTIONS / t['file']; exp += t.get('bytes') or 0
        if p.exists() and p.stat().st_size == t.get('bytes'): have += t['bytes']; full += 1
        else:
            have += sum(q.stat().st_size for q in p.parent.glob(p.name + '.*.part')) if p.parent.exists() else 0
            if p.exists(): have += p.stat().st_size
    return have, exp, full, len(tracks)


def _recent(root, secs=180):
    now = time.time(); n = 0
    if not root.exists(): return 0
    for p in root.glob('*/*.lock'):
        try:
            if now - p.stat().st_mtime < secs: n += 1
        except OSError: pass
    return n


def _ia_items():
    f = OUTPUTS / 'hadith-pipeline' / 'ia-items.json'; c = read_json(f, {})
    if c and time.time() - c.get('at', 0) < 600: return c.get('items', {})
    items = {}
    try:
        from . import upload
        import urllib.request
        for name in upload.check_quiet():
            try:
                m = json.loads(urllib.request.urlopen(urllib.request.Request(f'https://archive.org/metadata/{name}', headers={'User-Agent': 'hadith-pipeline'}), timeout=30).read())
                items[name] = {'files': len(m.get('files', [])), 'mb': round(int(m.get('item_size', 0)) / 1048576)}
            except Exception: items[name] = {'files': 0, 'mb': 0}
    except Exception:
        return c.get('items', {})
    write_json(f, {'at': time.time(), 'items': items}); return items


def collect():
    idx = read_json(COLLECTIONS / 'index.json', [])
    srcs = read_json(OUTPUTS / 'hadith-pipeline' / 'text-sources.json', {})
    iaitems = _ia_items()
    books = []
    for p in sorted(BOOKS.glob('*.json')):
        try: b = Book(p.stem)
        except SystemExit: continue
        st = read_json(b.path('state.json'), {}); q = read_json(b.path('qa.json'), {}); chk = read_json(b.path('text-check.json'), {})
        have, exp, full, nt = _audio_progress(b, idx)
        files = b.tracks()
        info = {'slug': b.slug, 'title': b.title, 'ar': b.cfg.get('title_ar', ''), 'cfg': b.cfg, 'stages': [k for k in STAGES if st.get(k, {}).get('done')],
                'audio_pct': round(100 * have / exp, 1) if exp else (100.0 if files else 0.0), 'audio_full': full, 'audio_total': nt or len(files),
                'tracks': len(files), 'coverage': q.get('coverage_pct'), 'hadiths': q.get('hadiths'), 'qa': q, 'check': chk,
                'preview': b.path('preview.html').relative_to(OUTPUTS).as_posix() if b.path('preview.html').exists() else None,
                'text_ok': bool(b.cfg.get('text')), 'text_src': srcs.get(b.slug, []), 'ia_state': read_json(b.path('ia-state.json'), {}), 'ia_cfg': b.cfg.get('ia') or {},
                'ia_item': iaitems.get((b.cfg.get('ia') or {}).get('identifier') or f'{b.slug}-audio'),
                'pkg': (b.path('site') / ((b.cfg.get('site') or {}).get('slug') or b.slug) / 'index.json').exists()}
        if files:
            a = asr.status(b); info.update(hours=a['hours'], blocks=a['blocks_total'], fc=a['fc_blocks'], zip=a['zip_blocks'])
            info['active'] = _recent(b.rec) + _recent(b.zip)
        else:
            info.update(hours=0, blocks=0, fc=0, zip=0, active=0)
        books.append(info)
    return books


def classify(b):
    c = b['cfg']
    if c.get('skip'): return 'done'
    if b['active']: return 'running'
    if b['audio_pct'] < 99.9 or not b['tracks']: return 'blocked_audio'
    models = b['blocks'] and b['fc'] >= b['blocks'] and b['zip'] >= b['blocks']
    if 'build' in b['stages']: return 'done'
    if models: return 'needs_text' if not b['text_ok'] else 'ready_to_match'
    if b['fc'] or b['zip']: return 'paused'
    return 'queued'


def _eta(books, sysinfo):
    """blocks/min from samples kept between refreshes."""
    f = OUTPUTS / 'hadith-pipeline' / 'rate.json'; s = read_json(f, [])
    now = time.time(); total = sum(b['fc'] + b['zip'] for b in books)
    s = [x for x in s if now - x[0] < 1800] + [[now, total]]; write_json(f, s[-200:])
    if len(s) < 2 or now - s[0][0] < 120: return None
    rate = (s[-1][1] - s[0][1]) / ((s[-1][0] - s[0][0]) / 60)
    return rate if rate > 0.05 else None


def _bar(p, cls=''):
    p = max(0, min(100, p or 0)); return f'<div class="bar {cls}"><i style="width:{p}%"></i></div>'


def _fmt_h(h):
    if h is None: return '—'
    return f'{int(h)} h {int((h % 1) * 60)} min' if h >= 1 else f'{int(h * 60)} min'


def render():
    books = collect(); sysinfo = _sys(); rate = _eta(books, sysinfo)
    for b in books: b['cls'] = classify(b)
    order = sorted([b for b in books if b['cls'] in ('queued', 'running', 'paused')], key=lambda b: b['hours'])
    remaining = sum((b['blocks'] - b['fc']) + (b['blocks'] - b['zip']) for b in order)
    eta_h = (remaining / rate / 60) if rate else None
    tot_h = sum(b['hours'] for b in books if not b['cfg'].get('skip')); done_h = sum(b['hours'] for b in books if b['cls'] == 'done' and not b['cfg'].get('skip'))

    def chips(b):
        out = ''
        for s in STAGES:
            on = s in b['stages']
            if s == 'asr' and not on and b['blocks'] and b['fc'] >= b['blocks'] and b['zip'] >= b['blocks']: on = True
            if s == 'prep' and b['audio_pct'] >= 99.9 and b['tracks']: on = on or True
            out += f'<span class="chip {"on" if on else ""}">{STAGE_NAMES[s]}</span>'
        return out

    def next_step(b):
        c = b['cls']; cfg = b['cfg']
        if cfg.get('skip'): return f'Skipped: {cfg.get("note") or "marked skip"}'
        if c == 'running': return 'Models running now'
        if c == 'blocked_audio': return f'Finish downloading the audio ({b["audio_full"]}/{b["audio_total"]} files complete, {b["audio_pct"]}% of bytes)'
        if c == 'queued': return 'Waiting in the queue for the models'
        if c == 'paused': return 'Paused - progress saved, resumes with python -m hp asr-all'
        if c == 'needs_text': return 'Models done. Needs a text source (no machine-readable JSON found yet)'
        if c == 'ready_to_match': return 'Models done and text set: run  python -m hp run ' + b['slug'] + ' --stages text,align,qa,build'
        if c == 'done': return 'Reader built' + (f' · {b["coverage"]}% of hadiths have audio' if b['coverage'] is not None else '')
        return ''

    def row(b):
        title = f'<b>{html.escape(b["title"])}</b> <span class="ar">{html.escape(b["ar"])}</span><br><small>{b["slug"]}</small>'
        prog = ''
        if b['tracks'] and not b['cfg'].get('skip'):
            prog = (f'<small>FastConformer {round(100 * b["fc"] / max(1, b["blocks"]))}%</small>{_bar(100 * b["fc"] / max(1, b["blocks"]))}'
                    f'<small>Zipformer {round(100 * b["zip"] / max(1, b["blocks"]))}%</small>{_bar(100 * b["zip"] / max(1, b["blocks"]), "z")}')
        link = f'<a href="/{b["preview"]}">open reader</a>' if b['preview'] else ''
        ia = b['ia_cfg']; st_ = b['ia_state']
        arch = ('held: ' + html.escape(ia.get('hold_note') or 'uploading by hand')) if ia.get('hold') else ('uploaded ✓' if st_.get('done') else (f'{len(st_.get("files", {}))} file(s) uploaded' if st_.get('files') else 'not uploaded yet'))
        it = b.get('ia_item')
        if not ia.get('hold') and it is not None: arch = f'on archive.org: {it["files"]} file(s), {it["mb"]} MB' + (' (page may still be processing)' if not it['files'] else '')
        elif ia.get('hold') and it: arch = f'on archive.org: {it["files"]} file(s), {it["mb"]} MB'
        link += f'<br><small>Internet Archive: {arch}</small>'
        link += '<br><small>Site data package: ' + ('built' if b.get('pkg') else 'not built') + '</small>'
        warn = ''
        for w in (b['check'] or {}).get('warnings', []): warn += f'<div class="warn">⚠ {html.escape(w)}</div>'
        ts = ''
        if b['text_src'] and not b['text_ok']:
            ts = '<br><small>text options: ' + ', '.join((c['source'] if c['source'] != 'none' else 'no known JSON') + (' ✓' if c.get('ok') else '') for c in b['text_src']) + '</small>'
        return (f'<tr><td>{title}</td><td>{b["tracks"] or "—"}<br><small>{_fmt_h(b["hours"]) if b["hours"] else ""}</small></td>'
                f'<td>{prog}</td><td class="chips">{chips(b)}</td><td>{html.escape(next_step(b))}{warn}{ts}<br>{link}</td></tr>')

    def section(title, sub, items, cls):
        if not items: return ''
        return (f'<section class="{cls}"><h2>{title} <span class="n">{len(items)}</span></h2><p class="sub">{sub}</p>'
                '<table><tr><th>Book</th><th>Tracks · length</th><th>Models progress</th><th>Pipeline steps</th><th>Status / next step</th></tr>'
                + ''.join(row(b) for b in items) + '</table></section>')

    running = [b for b in books if b['cls'] == 'running']
    queued = [b for b in order if b['cls'] == 'queued']
    paused = [b for b in books if b['cls'] == 'paused']
    s = sysinfo
    sysline = ''
    if s:
        sysline = (f'<div class="kpi"><b>{s.get("workers", 0)}</b><span>model processes running</span></div>'
                   f'<div class="kpi"><b>{s["ram_used"]:.1f} / {s["ram_total"]:.1f} GB</b><span>RAM in use</span></div>'
                   f'<div class="kpi"><b>{s.get("cpu", "?")}%</b><span>CPU load</span></div>')
    eta = '<div class="kpi"><b>paused</b><span>no model workers running</span></div>' if s and s.get('workers', 1) == 0 else \
          f'<div class="kpi"><b>{_fmt_h(eta_h)}</b><span>left for queued models (est.)</span></div>' if eta_h is not None else \
          '<div class="kpi"><b>measuring…</b><span>time left for the queue</span></div>'
    css = '''
:root{--bg:#f6f5f1;--panel:#fff;--ink:#1f2326;--mute:#7b7f84;--line:#e7e4dc;--acc:#1f6f5c;--acc2:#e6f2ee;--warn:#a65a00;--bad:#b3261e}
@media(prefers-color-scheme:dark){:root{--bg:#14161a;--panel:#1d2025;--ink:#e8e6e0;--mute:#9a9a94;--line:#2c3036;--acc:#5cc2a6;--acc2:#1f2f2b;--warn:#e0a24a;--bad:#ff8a80}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 system-ui,"Segoe UI",sans-serif;padding:20px 24px 60px}
h1{font-size:21px;margin:0 0 2px}h2{font-size:16px;margin:26px 0 2px}.sub{color:var(--mute);margin:0 0 8px;font-size:13px}.n{background:var(--acc2);color:var(--acc);border-radius:999px;padding:1px 9px;font-size:12px}
.kpis{display:flex;flex-wrap:wrap;gap:10px;margin:14px 0}.kpi{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:10px 16px;min-width:150px}.kpi b{display:block;font-size:19px}.kpi span{color:var(--mute);font-size:12px}
table{width:100%;border-collapse:collapse;background:var(--panel);border:1px solid var(--line);border-radius:12px;overflow:hidden}
th{font-size:11px;text-transform:uppercase;letter-spacing:.04em;color:var(--mute);text-align:left;padding:8px 10px;border-bottom:1px solid var(--line)}
td{padding:10px;border-bottom:1px solid var(--line);vertical-align:top}tr:last-child td{border-bottom:0}
.ar{font:17px "Amiri","Traditional Arabic",serif;color:var(--mute);margin-inline-start:6px}small{color:var(--mute)}
.bar{background:var(--line);border-radius:4px;height:7px;width:130px;margin:2px 0 6px}.bar i{display:block;height:7px;background:var(--acc);border-radius:4px}.bar.z i{background:#6b8cff}
.chips{white-space:nowrap}.chip{display:inline-block;border:1px solid var(--line);color:var(--mute);border-radius:999px;padding:1px 8px;font-size:11px;margin:1px 2px 1px 0}.chip.on{background:var(--acc2);color:var(--acc);border-color:var(--acc)}
.warn{color:var(--warn);font-size:12px;margin-top:3px}a{color:var(--acc);font-weight:600;text-decoration:none}
section.running h2{color:var(--acc)}section.blocked_audio h2,section.needs_text h2{color:var(--warn)}
.note{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:12px 16px;margin-top:22px;color:var(--mute);font-size:13px}
@media(max-width:800px){td:nth-child(2),th:nth-child(2),td:nth-child(4),th:nth-child(4){display:none}}
'''
    page = (f'<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="refresh" content="20"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>Hadith pipeline</title><style>{css}</style></head><body>'
            f'<h1>Hadith audio pipeline</h1><small>Updated {time.strftime("%H:%M:%S")} · refreshes every 20 s</small>'
            f'<div class="kpis">{sysline}{eta}'
            f'<div class="kpi"><b>{sum(1 for b in books if b["cls"] == "done")} / {len(books)}</b><span>books finished or skipped</span></div>'
            f'<div class="kpi"><b>{sum(b["hours"] for b in books if not b["cfg"].get("skip") and b["tracks"]):.0f} h</b><span>audio to process (not skipped)</span></div>'
            f'<div class="kpi"><b>{sum(1 for b in books if b["text_ok"])} / {len(books)}</b><span>books with a text source</span></div>'
            f'<div class="kpi"><b>{sum(1 for b in books if b["preview"])}</b><span>readers built</span></div>'
            f'<div class="kpi"><b>{sum(1 for b in books if b.get("pkg"))}</b><span>site data packages</span></div>'
            f'<div class="kpi"><b>{sum(1 for b in books if b.get("ia_item") and b["ia_item"]["files"])}</b><span>books with audio on archive.org</span></div></div>'
            + section('Running now', 'Models are processing these books right now.', running, 'running')
            + section('Paused (progress saved)', 'Started, then stopped. They continue where they left off.', paused, 'queued')
            + section('Queued', 'Next in line, shortest first. They start by themselves.', queued or [], 'queued')
            + section('Models done — needs text', 'Audio is transcribed. Each needs a text source before it can be matched.', [b for b in books if b['cls'] == 'needs_text'], 'needs_text')
            + section('Ready to match', 'Models and text are both ready.', [b for b in books if b['cls'] == 'ready_to_match'], 'ready')
            + section('Waiting for audio download', 'The audio is incomplete (partial downloads). Finish the download first.', [b for b in books if b['cls'] == 'blocked_audio'], 'blocked_audio')
            + section('Done or skipped', 'Finished readers, and books handled elsewhere.', [b for b in books if b['cls'] == 'done'], 'done')
            + '<div class="note">Steps: Audio check → Text → Models (FastConformer + Zipformer) → Match → QA → Reader. '
              'Commands: <code>python -m hp list</code> · <code>python -m hp run BOOK</code> · <code>python -m hp textfind</code>. '
              'Everything is resumable; finished blocks are never redone.</div></body></html>')
    out = OUTPUTS / 'hadith-pipeline' / 'status.html'; out.parent.mkdir(parents=True, exist_ok=True); out.write_text(page, encoding='utf8')
    return out
