"""Compare a book's text against a second printed edition (Shamela) and, optionally, regroup the hadith.

    python tools/compare_editions.py BOOK SHAMELA_ID [--layout plain|span] [--apply]

What it tells you (this is what exposed the wrong Lulu text and al-Ilmam's wrongly split hadith):
  * how many hadith each edition has and how many words
  * how many of OUR entries belong together in the second edition (we split too much)
  * how many of OUR entries hold several of its hadith (we merged too much)
  * second-edition numbers that none of our entries covers
With --apply, entries are regrouped by the second edition's numbering (a backup source-text.before-regroup.json is kept).
Method: unique 5-word anchors shared by both texts, longest increasing subsequence, majority vote per entry.
"""
import argparse, bisect, collections, json, re, shutil, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hp.common import Book, ROOT
from hp import shamela

ap = argparse.ArgumentParser(); ap.add_argument('book'); ap.add_argument('shamela_id', type=int)
ap.add_argument('--layout', default='plain', choices=['plain', 'span']); ap.add_argument('--apply', action='store_true')
a = ap.parse_args()
b = Book(a.book); work = b.dir; b.dir = work / 'second-edition'; b.dir.mkdir(exist_ok=True)
shamela.fetch_pages(b, a.shamela_id)
ref = shamela.parse_plain(b) if a.layout == 'plain' else shamela.parse(b)
ours = json.load(open(work / 'text.json', encoding='utf8'))['items']
DIA = re.compile(r'[ؐ-ًؚ-ٰٟۖ-ۭـ]')
def fold(w):
    w = DIA.sub('', w); w = re.sub('[أإآٱ]', 'ا', w).replace('ى', 'ي').replace('ة', 'ه')
    return re.sub(r'[^ء-ي]', '', w)
def words(s): return [f for f in (fold(x) for x in s.split()) if f]
A, ownA = [], []
for h in ref:
    for w in words(h['content']): A.append(w); ownA.append(h['id'])
B, entB = [], []
for i, x in enumerate(ours):
    for w in words(x.get('chain', '') + ' ' + x['text']): B.append(w); entB.append(i)
print(f'second edition: {len(ref)} hadith, {len(A)} words | ours: {len(ours)} hadith, {len(B)} words')
K = 5
ca = collections.Counter(tuple(A[i:i + K]) for i in range(len(A) - K + 1)); cb = collections.Counter(tuple(B[i:i + K]) for i in range(len(B) - K + 1))
pa = {tuple(A[i:i + K]): i for i in range(len(A) - K + 1) if ca[tuple(A[i:i + K])] == 1}
anch = [(i, pa[tuple(B[i:i + K])]) for i in range(len(B) - K + 1) if cb[tuple(B[i:i + K])] == 1 and tuple(B[i:i + K]) in pa]
tails, tid, prev = [], [], [-1] * len(anch)
for j, (_, p) in enumerate(anch):
    k = bisect.bisect_left(tails, p)
    if k == len(tails): tails.append(p); tid.append(j)
    else: tails[k] = p; tid[k] = j
    prev[j] = tid[k - 1] if k else -1
seq = []; j = tid[-1] if tid else -1
while j != -1: seq.append(anch[j]); j = prev[j]
seq.reverse()
print(f'anchors {len(anch)}, kept {len(seq)} ({100 * len(seq) / max(1, len(anch)):.1f}%)  <- if low, the two texts are different books or editions')
votes = collections.defaultdict(collections.Counter)
for bi, ai in seq: votes[entB[bi]][ownA[ai]] += 1
owner, last = [], None
for i in range(len(ours)):
    if votes[i]: last = votes[i].most_common(1)[0][0]
    owner.append(last)
mx = 0
for i, o in enumerate(owner):
    if o is None: continue
    if o < mx: owner[i] = mx
    mx = max(mx, owner[i])
groups = collections.OrderedDict()
for x, o in zip(ours, owner): groups.setdefault(o, []).append(x)
merged = {o: g for o, g in groups.items() if len(g) > 1}
span = collections.defaultdict(set)
for bi, ai in seq: span[entB[bi]].add(ownA[ai])
print(f'our {len(ours)} entries -> {len(groups)} hadith in the second edition; {len(merged)} groups merge {sum(len(g) for g in merged.values())} of our entries')
print('our entries that hold 2+ of its hadith:', sum(1 for s in span.values() if len(s) > 1))
print('its numbers covered by none of our entries:', len(set(h['id'] for h in ref) - set(o for o in owner if o is not None)))
if a.apply:
    src = Path(b.cfg['text']['path']); src = src if src.is_absolute() else ROOT / src
    shutil.copyfile(src, src.with_name('source-text.before-regroup.json'))
    out = [{'id': o, 'kitab_name': g[0].get('kitab_name', ''), 'bab_name': g[0].get('bab_name', ''), 'rawi': '',
            'content': ' '.join(x['text'] for x in g), 'hint': ' '.join(g[0]['text'].split()[:6])} for o, g in groups.items() if o is not None]
    src.write_text(json.dumps(out, ensure_ascii=False), encoding='utf8'); print('regrouped ->', src, len(out), 'hadith')
