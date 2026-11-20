"""Stage: align. Match the printed text to what FastConformer heard, then verify/repair with Zipformer.

Outputs (in <work>):
  align.json      one row per hadith: n, words, matched, track, track_end, start, end, flags
  wordmatch.json  {global word index: [track, start, end]} for every printed word heard by FastConformer
Passes:
  1 anchors   unique 4-word sequences shared by text and speech, kept in order (longest increasing run)
  2 words     word-by-word matching between anchors
  3 hadiths   a hadith gets audio when >=30% of its words matched; start/end from first/last matched word
  4 zipcheck  Zipformer sounds confirm start/end; edges move at most 1.5 s, only on a strong match
  5 recover   hadiths still without audio are searched for inside the gap between their neighbours
              (FastConformer words first, then Zipformer sounds)
"""
import glob, json, re, bisect
from difflib import SequenceMatcher
from .common import Book, fold, sound_norm, token_folds, write_json, read_json, log

K = 4


def load_text(book):
    t = read_json(book.path('text.json'))
    if not t: raise SystemExit(f'{book.slug}: run the text stage first')
    return t


def toks_of(item):
    out = []
    for w in (item['chain'] + ' ' + item['text']).split():
        out += token_folds(w)
    return out


def load_heard(book):
    R = []
    for d in sorted(p for p in book.rec.iterdir() if p.is_dir()):
        t = int(d.name)
        for f in sorted(d.glob('[0-9][0-9][0-9][0-9].json')):
            for w in json.loads(f.read_text(encoding='utf8'))['words']:
                x = fold(w['text'])
                if x: R.append((x, t, w['start'], w['end']))
    return R


def _anchor_match(H, R):
    def grams(seq):
        d = {}
        for i in range(len(seq) - K + 1):
            d.setdefault(tuple(seq[i:i + K]), []).append(i)
        return d
    hs = [x[0] for x in H]; rs = [x[0] for x in R]
    gh, gr = grams(hs), grams(rs)
    pairs = sorted((gh[g][0], gr[g][0]) for g in gh if len(gh[g]) == 1 and len(gr.get(g, [])) == 1)
    tails, idx, prev = [], [], [-1] * len(pairs)
    for i, (_, r) in enumerate(pairs):
        p = bisect.bisect_left(tails, r)
        if p == len(tails): tails.append(r); idx.append(i)
        else: tails[p] = r; idx[p] = i
        prev[i] = idx[p - 1] if p else -1
    chain = []
    k = idx[-1] if idx else -1
    while k != -1: chain.append(pairs[k]); k = prev[k]
    chain.reverse()
    match = {}; segs = []; ph = pr = 0
    for h, r in chain:
        segs.append((ph, h, pr, r))
        for j in range(K): match[h + j] = r + j
        ph, pr = h + K, r + K
    segs.append((ph, len(H), pr, len(R)))
    for h0, h1, r0, r1 in segs:
        if h1 <= h0 or r1 <= r0 or (h1 - h0) > 400 or (r1 - r0) > 600: continue
        sm = SequenceMatcher(None, hs[h0:h1], rs[r0:r1], autojunk=False)
        for a, b, n in sm.get_matching_blocks():
            for j in range(n): match[h0 + a + j] = r0 + b + j
    return match, len(chain)


def _fuzzy_fill(H, R, match, max_h=12, max_r=15, thr=0.72):
    """Between two matched words, pair up the remaining printed/heard words that sound almost the same
    (the recognizer often mishears one letter). Order-preserving; returns how many words were added."""
    hs = [x[0] for x in H]; rs = [x[0] for x in R]
    keys = sorted(match); added = 0
    pairs = list(zip(keys, keys[1:]))
    for h0, h1 in pairs:
        r0, r1 = match[h0], match[h1]
        gh, gr = h1 - h0 - 1, r1 - r0 - 1
        if gh < 1 or gr < 1 or gh > max_h or gr > max_r or r1 <= r0: continue
        A = hs[h0 + 1:h1]; B = rs[r0 + 1:r1]
        n, m = len(A), len(B)
        sc = [[0.0] * (m + 1) for _ in range(n + 1)]; back = [[0] * (m + 1) for _ in range(n + 1)]
        def sim(a, b):
            if len(a) < 3 or len(b) < 3 or abs(len(a) - len(b)) > 2: return 0.0
            r = SequenceMatcher(None, a, b).ratio(); return r if r >= thr else 0.0
        for i in range(1, n + 1):
            for j in range(1, m + 1):
                best, mv = sc[i - 1][j], 1
                if sc[i][j - 1] > best: best, mv = sc[i][j - 1], 2
                x = sim(A[i - 1], B[j - 1])
                if x and sc[i - 1][j - 1] + x > best: best, mv = sc[i - 1][j - 1] + x, 3
                sc[i][j] = best; back[i][j] = mv
        i, j = n, m
        while i > 0 and j > 0:
            mv = back[i][j]
            if mv == 3: match[h0 + i] = r0 + j; added += 1; i -= 1; j -= 1
            elif mv == 1: i -= 1
            else: j -= 1
    return added


def _zip_stream(book, t, cache):
    if t not in cache:
        d = book.zip / f'{t:02d}'
        if not (d / 'DONE').exists(): cache[t] = None
        else:
            ch, tm = [], []
            for f in sorted(d.glob('[0-9][0-9][0-9][0-9].json')):
                for e in json.loads(f.read_text(encoding='utf8')):
                    for c in sound_norm(e['sym']): ch.append(c); tm.append(e['time'])
            cache[t] = (''.join(ch), tm)
    return cache[t]


def _edge(ph, st, w0, w1, side):
    s, tm = st
    i0, i1 = bisect.bisect_left(tm, w0), bisect.bisect_right(tm, w1); seg = s[i0:i1]
    if len(ph) < 6 or len(seg) < 4: return None
    bl = [b for b in SequenceMatcher(None, ph, seg, autojunk=False).get_matching_blocks()[:-1] if b.size >= 2]
    if not bl: return (0.0, None)
    sc = sum(b.size for b in bl) / len(ph)
    k = bl[0] if side == 's' else bl[-1]
    return (sc, tm[i0 + k.b] if side == 's' else tm[i0 + k.b + k.size - 1] + 0.1)


def run(book: Book):
    text = load_text(book); items = text['items']
    H = []; own = []                      # global word list; own[h] = (first index, count)
    for hi, it in enumerate(items):
        ts = toks_of(it); own.append((len(H), len(ts)))
        H += [(x, hi) for x in ts]
    R = load_heard(book)
    log(f'{book.slug}: {len(items)} hadiths, {len(H)} printed words, {len(R)} heard words')
    match, nanch = _anchor_match(H, R)
    fz = _fuzzy_fill(H, R, match); log(f'{book.slug}: {fz} more words matched by sound-alike (mis-heard letters)')
    log(f'{book.slug}: {nanch} anchors, {len(match)} of {len(H)} words matched ({100 * len(match) / max(1, len(H)):.1f}%)')
    rows = []
    for hi, it in enumerate(items):
        a, n = own[hi]
        ms = [match[i] for i in range(a, a + n) if i in match]
        row = {'n': it['n'], 'words': n, 'matched': len(ms)}
        if ms and len(ms) >= max(3, 0.3 * n):
            r0, r1 = min(ms), max(ms)
            row.update(track=R[r0][1], track_end=R[r1][1], start=R[r0][2], end=R[r1][3])
        rows.append(row)
    wm = {str(i): [R[r][1], R[r][2], R[r][3]] for i, r in match.items()}
    ok = lambda r: 'start' in r and r['track'] == r['track_end']

    # ---- Zipformer edge check ------------------------------------------------
    zc = {}; stat = {'agree': 0, 'moved': 0, 'unverified': 0, 'skipped': 0}
    for it, row in zip(items, rows):
        if not ok(row): continue
        st = _zip_stream(book, row['track'], zc)
        if st is None: stat['skipped'] += 1; continue
        toks = (it['chain'] + ' ' + it['text']).split()
        head, tail = sound_norm(' '.join(toks[:5])), sound_norm(' '.join(toks[-5:]))
        rs = _edge(head, st, row['start'] - 3, row['start'] + 5, 's'); re_ = _edge(tail, st, row['end'] - 5, row['end'] + 3, 'e')
        moved = False
        for r, key in ((rs, 'start'), (re_, 'end')):
            if r and r[0] >= 0.7 and r[1] is not None and 0.5 < abs(r[1] - row[key]) <= 1.5:
                row[key] = round(r[1], 2); row['zmoved_' + key] = 1; moved = True
        good = (rs and rs[0] >= 0.6) or (re_ and re_[0] >= 0.6)
        stat['moved' if moved else 'agree' if good else 'unverified'] += 1
    log(f'{book.slug}: zipformer edge check {stat}')

    # ---- recovery in gaps ------------------------------------------------------
    cache_w = {}
    def words_of(t):
        if t not in cache_w: cache_w[t] = [w for w in R if w[1] == t]
        return cache_w[t]
    placed = [i for i, r in enumerate(rows) if ok(r)]
    rec1 = rec2 = 0
    for pa, pb in zip([-1] + placed, placed + [len(items)]):
        grp = list(range(pa + 1, pb))
        if not grp or len(grp) > 60: continue
        A = rows[pa] if pa >= 0 else None; B = rows[pb] if pb < len(items) else None
        if not (A or B): continue
        ta = A['track'] if A else B['track']; tb = B['track'] if B else ta
        if tb - ta > 1: continue
        lo = A['end'] if A else 0; hi = B['start'] if B else 1e9
        W = []
        for t in range(ta, tb + 1):
            for w in words_of(t):
                if t == ta and w[2] < lo - 0.3: continue
                if t == tb and w[3] > hi + 0.3: continue
                W.append(w)
        # (1) FastConformer words
        if len(W) >= 3:
            flat, owner = [], []
            for h in grp:
                a, n = own[h]
                for j in range(n): flat.append(H[a + j][0]); owner.append((h, a + j))
            sm = SequenceMatcher(None, flat, [w[0] for w in W], autojunk=False)
            got = {}
            for a, b, n in sm.get_matching_blocks():
                for k in range(n): got.setdefault(owner[a + k][0], []).append((owner[a + k][1], W[b + k]))
            for h in grp:
                m = got.get(h, []); n = own[h][1]
                if len(m) >= max(3, 0.3 * n):
                    if m[0][1][1] != m[-1][1][1]: continue
                    s, e = m[0][1][2], m[-1][1][3]
                    if e - s > max(6, n * 1.2): continue
                    rows[h].update(track=m[0][1][1], track_end=m[0][1][1], start=round(s, 2), end=round(e, 2), recovered=1)
                    for gi, w in m: wm[str(gi)] = [w[1], w[2], w[3]]
                    rec1 += 1
        # (2) Zipformer sounds for what is still missing, same-track gaps only
        if ta == tb:
            st = _zip_stream(book, ta, zc)
            if st:
                s_, tm = st; i0, i1 = bisect.bisect_left(tm, lo - .5), bisect.bisect_right(tm, hi + .5); seg = s_[i0:i1]
                still = [h for h in grp if not ok(rows[h])]
                if still and len(seg) >= 8:
                    ref, rng = '', {}
                    for h in grp:
                        t = sound_norm(items[h]['chain'] + ' ' + items[h]['text']); rng[h] = (len(ref), len(ref) + len(t)); ref += t
                    bl = [b for b in SequenceMatcher(None, ref, seg, autojunk=False).get_matching_blocks()[:-1] if b.size >= 3]
                    for h in still:
                        r0, r1 = rng[h]; n = r1 - r0
                        mine = [b for b in bl if b.a >= r0 and b.a < r1]
                        if n < 10 or not mine: continue
                        cov = sum(b.size for b in mine) / n; f, l = mine[0], mine[-1]
                        if cov < .5 or f.a - r0 > 8 or r1 - (l.a + l.size) > 8: continue
                        s, e = tm[i0 + f.b], tm[i0 + l.b + l.size - 1] + .1
                        if e <= s or e - s > n * .4 + 5: continue
                        rows[h].update(track=ta, track_end=ta, start=round(s, 2), end=round(e, 2), recovered=2, zcov=round(cov, 2)); rec2 += 1
    log(f'{book.slug}: recovered {rec1} from words, {rec2} from sounds')
    have = sum(1 for r in rows if ok(r))
    write_json(book.path('align.json'), rows); write_json(book.path('wordmatch.json'), wm)
    log(f'{book.slug}: {have} of {len(items)} hadiths have audio ({100 * have / max(1, len(items)):.1f}%)')
    return {'hadiths': len(items), 'with_audio': have, 'zip': stat, 'recovered_words': rec1, 'recovered_sounds': rec2}
