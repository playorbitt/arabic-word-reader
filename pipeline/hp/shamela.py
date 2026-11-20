"""Source: shamela.ws.   python -m hp shamela BOOK_SLUG [--id N] [--limit PAGES] [--delay S]

Downloads every page of a Shamela book (polite, resumable, cached), then builds <work>/source-text.json:
a list of {id, kitab_name, bab_name, rawi, content, hint} - the same flat format the text stage already reads.

Shamela layout used here (checked on book 5919):
  page url   https://shamela.ws/book/<id>/<n>         n = 1..last internal page
  text       <div class="nass" data-page-id data-page-num> <p>...</p> ... </div>
  hadith     paragraph starting  <span class="c2">(12)</span>   (the book's own hadith number)
  heading    paragraph that is only <span class="c2">(باب ...)</span> / (كتاب ...)
  extras     [bracketed] editor insertions, a lone "/" standing for the salawat sign
Paragraphs without a number continue the previous hadith (they are page breaks inside it).
"""
import html, json, re, time, urllib.request
from .common import Book, ROOT, fold, read_json, write_json, log

UA = {'User-Agent': 'Mozilla/5.0 hadith-pipeline (personal research; polite crawler)'}
AR_DIGITS = str.maketrans('٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹', '01234567890123456789')


def _get(url, tries=5):
    for i in range(tries):
        try:
            return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60).read().decode('utf8', 'replace')
        except Exception as e:
            if i == tries - 1: raise
            time.sleep(3 * (i + 1))


def fetch_pages(book: Book, sid, limit=None, delay=0.35):
    cache = book.dir / 'shamela-pages'; cache.mkdir(parents=True, exist_ok=True)
    first = _get(f'https://shamela.ws/book/{sid}')
    p1 = _get(f'https://shamela.ws/book/{sid}/1')
    m = re.search(rf'/book/{sid}/(\d+)#p1"[^>]*>(?:&nbsp;)?&gt;&gt;', p1)          # the ">>" (last page) button
    last = int(m.group(1)) if m else max(int(x) for x in re.findall(rf'/book/{sid}/(\d+)', first))
    meta = {'id': sid, 'last_page': last,
            'title': html.unescape(re.sub(r'\s+', ' ', (re.search(r'<title>(.*?)</title>', first, re.S) or [None, ''])[1])).strip(),
            'card': html.unescape(re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', first[first.find('بطاقة الكتاب'):first.find('فهرس الموضوعات')]))).strip()[:900]}
    write_json(book.path('shamela-meta.json'), meta)
    n = min(last, limit) if limit else last
    log(f'{book.slug}: shamela book {sid}, {last} pages ({n} to fetch)')
    got = 0
    for p in range(1, n + 1):
        f = cache / f'{p:04d}.json'
        if f.exists(): continue
        h = _get(f'https://shamela.ws/book/{sid}/{p}')
        i = h.find('class="nass'); j = h.find('id="appended_pages"', i)
        blk = h[i:j] if i >= 0 else ''
        m = re.search(r'data-page-num="(\d+)"', blk)
        part = re.search(r'id="fld_part_bottom" value="(\d+)"', h)
        write_json(f, {'id': p, 'num': int(m.group(1)) if m else None, 'part': int(part.group(1)) if part else None, 'html': blk})
        got += 1
        if got % 50 == 0: log(f'   {p}/{n} pages')
        time.sleep(delay)
    return n


def clean(raw_html, slash=True):
    """HTML of one paragraph body -> plain Arabic text with the salawat sign normalised."""
    t = re.sub(r'<a [^>]*btn_tag[^>]*>.*?</a>', '', raw_html, flags=re.S)
    t = re.sub(r'<span[^>]*class="anchor"[^>]*></span>', '', t)
    t = html.unescape(re.sub(r'<[^>]+>', '', t))
    def br(m):
        inner = m.group(1).strip()
        return 'ﷺ' if fold(inner) == 'صلياللهعليهوسلم' else inner
    t = re.sub(r'\[([^\[\]]*)\]', br, t)
    t = re.sub(r'(^|\s)/(?=\s|$|[:،.؛])', r'\1ﷺ', t)          # a lone "/" stands for the salawat sign
    return re.sub(r'\s+', ' ', t).strip()


def parse(book: Book):
    cache = book.dir / 'shamela-pages'
    files = sorted(cache.glob('[0-9][0-9][0-9][0-9].json'))
    items = []; kitab = 'المقدمة'; bab = ''; cur = None; pre = []
    for f in files:
        d = json.loads(f.read_text(encoding='utf8'))
        if d['id'] == 1: continue                                  # title / card page, not book text
        for para in re.findall(r'<p>(.*?)</p>', d['html'], re.S):
            body = re.sub(r'<a [^>]*btn_tag[^>]*>.*?</a>', '', para, flags=re.S)
            body = re.sub(r'<span[^>]*class="anchor"[^>]*></span>', '', body).strip()
            m = re.match(r'\s*<span class="c2">\(([^<>]+?)\)</span>(.*)$', body, re.S)
            if m:
                label, rest = html.unescape(m.group(1)).strip(), m.group(2)
                digits = label.translate(AR_DIGITS)
                if digits.isdigit():                                  # a hadith
                    cur = {'id': int(digits), 'kitab_name': kitab, 'bab_name': bab, 'rawi': '', 'content': clean(rest), 'page': d['num'], 'part': d['part']}
                    items.append(cur); continue
                fl = fold(label)                                       # a heading
                if not rest.strip() or not clean(rest):
                    if fl.startswith('كتاب'): kitab, bab = label, ''
                    else: bab = label
                    cur = None; continue
            txt = clean(body, slash=cur is not None)
            if not txt: continue
            if cur is not None: cur['content'] = (cur['content'] + ' ' + txt).strip()
            elif not items:
                pre.append(txt)                                         # introduction before the first numbered hadith
    out = []
    if pre:
        out.append({'id': 0, 'kitab_name': 'المقدمة', 'bab_name': '', 'rawi': '', 'content': ' '.join(pre), 'hint': ''})
    for x in items:
        x['hint'] = ' '.join(x['content'].split()[:6]); out.append(x)
    # repair typos in the printed numbers: an id that breaks the +1 sequence but whose neighbours agree on the missing number
    fixes = []; seq = [x for x in out if x['id']]
    for i in range(1, len(seq) - 1):
        prev, cur, nxt = seq[i - 1]['id'], seq[i]['id'], seq[i + 1]['id']
        if cur != prev + 1 and nxt == prev + 2:
            fixes.append({'printed': cur, 'repaired_to': prev + 1, 'page': seq[i].get('page')}); seq[i]['printed_id'] = cur; seq[i]['id'] = prev + 1
    if fixes: write_json(book.path('source-text.fixes.json'), fixes)
    write_json(book.path('source-text.json'), out)
    nums = [x['id'] for x in out if x['id']]
    log(f'{book.slug}: source-text.json - {len(out)} entries from {len(files)} pages; hadith numbers {min(nums)}..{max(nums)}')
    return out


def run(book: Book, sid=None, limit=None, delay=0.35):
    sid = sid or (book.cfg.get('shamela') or {}).get('id')
    if not sid: raise SystemExit(f'{book.slug}: give --id or set "shamela": {{"id": N}} in the book config')
    fetch_pages(book, sid, limit, delay)
    return parse(book)
