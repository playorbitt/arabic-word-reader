"""Stage helper: check a book's text.json before it is used, and (when recognition exists) against the audio.
Writes <work>/text-check.json and prints warnings. Never blocks the run - it tells you what to look at."""
import re
from .common import Book, read_json, write_json, fold, log

ELLIPSIS = ('…', '...')


def run(book: Book):
    t = read_json(book.path('text.json'))
    if not t: raise SystemExit(f'{book.slug}: no text.json yet')
    items = t['items']; warn = []
    n = len(items)
    empty = [i['n'] for i in items if len((i['chain'] + i['text']).split()) < 3]
    cut = [i['n'] for i in items if (i.get('en') or '').rstrip().endswith(ELLIPSIS) or i['text'].rstrip().endswith(ELLIPSIS)]
    nums = [int(i['n']) for i in items if str(i['n']).isdigit()]
    back = sum(1 for a, b in zip(nums, nums[1:]) if b < a)
    dup = n - len({i['n'] for i in items})
    short = [i['n'] for i in items if 3 <= len((i['chain'] + i['text']).split()) < 6]
    marks = sum(1 for i in items if re.search(r'\[\^\d+\]', i['chain'] + i['text']))
    if n < 20: warn.append(f'only {n} hadiths')
    if empty: warn.append(f'{len(empty)} hadiths with almost no text (e.g. {empty[:5]})')
    if cut: warn.append(f'{len(cut)} hadiths look cut off with "…" (e.g. {cut[:5]}) - ending may be missing from the source')
    if back > 0.02 * n: warn.append(f'hadith numbers go backwards {back} times - numbering may differ from the audio edition')
    if dup: warn.append(f'{dup} duplicate hadith numbers')
    rep = {'book': book.slug, 'hadiths': n, 'empty': empty[:50], 'cut_off': cut, 'short': len(short), 'footnote_marks': marks,
           'numbers_backwards': back, 'duplicate_numbers': dup, 'warnings': warn}
    # ---- against the audio (only when FastConformer output exists) -------------------------------
    blocks = sorted(book.rec.glob('*/[0-9][0-9][0-9][0-9].json')) if book.rec.exists() else []
    if len(blocks) >= 5:
        heard = []
        for f in blocks[:400]:
            heard += [fold(w['text']) for w in (read_json(f) or {}).get('words', [])]
        heard = [h for h in heard if h]
        printed = set()
        for i in items: printed.update(fold(w) for w in (i['chain'] + ' ' + i['text']).split())
        hit = sum(1 for h in heard if h in printed) / max(1, len(heard))
        rep['heard_words_in_text_pct'] = round(100 * hit, 1)
        if hit < 0.55: warn.append(f'only {100 * hit:.0f}% of the words heard in the first audio appear anywhere in this text - probably a different edition or book')
    write_json(book.path('text-check.json'), rep)
    log(f'{book.slug}: text check - {n} hadiths, ' + ('no warnings' if not warn else f'{len(warn)} warning(s)'))
    for w in warn: log('   WARNING:', w)
    return rep
