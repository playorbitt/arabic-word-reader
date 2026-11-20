"""Stage: qa. Quality report so problems are found by a script, not by ear.
Writes <work>/qa.json and returns a short summary dict."""
from .common import Book, read_json, write_json, log


def run(book: Book):
    rows = read_json(book.path('align.json')); wm = read_json(book.path('wordmatch.json'))
    text = read_json(book.path('text.json')); items = text['items']
    ok = lambda r: 'start' in r and r['track'] == r['track_end']
    n = len(rows); have = [i for i, r in enumerate(rows) if ok(r)]
    # runs of hadiths with no audio
    runs = []; i = 0
    while i < n:
        if not ok(rows[i]):
            j = i
            while j < n and not ok(rows[j]): j += 1
            runs.append((i, j)); i = j
        else: i += 1
    absent = [{'from': rows[a]['n'], 'to': rows[b - 1]['n'], 'count': b - a} for a, b in runs if b - a >= 10]
    # placed hadiths in wrong order / overlapping
    order = []; prev = None
    for i in have:
        r = rows[i]
        if prev and (r['track'], r['start']) < (prev['track'], prev['start'] - 0.01): order.append(r['n'])
        prev = r
    weak = [r['n'] for i, r in enumerate(rows) if ok(r) and r['matched'] < 0.4 * r['words'] and not r.get('recovered')]
    stretch = [r['n'] for r in rows if ok(r) and r['words'] and (r['end'] - r['start']) > r['words'] * 2 + 10]
    unver = sum(1 for r in rows if ok(r) and not (r.get('zmoved_start') or r.get('zmoved_end')))
    rep = {'book': book.slug, 'hadiths': n, 'with_audio': len(have), 'coverage_pct': round(100 * len(have) / max(1, n), 1),
           'no_audio_total': n - len(have), 'absent_blocks': absent,
           'recovered': sum(1 for r in rows if r.get('recovered')),
           'order_problems': order, 'weak_match': weak[:200], 'weak_match_count': len(weak),
           'stretched_count': len(stretch), 'stretched_sample': stretch[:50],
           'printed_words': sum(r['words'] for r in rows), 'matched_words': len(wm)}
    write_json(book.path('qa.json'), rep)
    log(f'{book.slug}: QA coverage {rep["coverage_pct"]}%, {len(absent)} absent block(s), {len(order)} order problem(s), '
        f'{rep["weak_match_count"]} weak, {rep["stretched_count"]} stretched')
    return rep
