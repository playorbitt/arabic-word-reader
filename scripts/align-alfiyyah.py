import bisect,json,re,sys
from html import unescape
from pathlib import Path
sys.path.insert(0,'work/rapid-deps')
from rapidfuzz.distance import Levenshtein
data=json.loads(Path('outputs/alfiyyah-shamela-text.json').read_text(encoding='utf8'))
index_html=Path('work/shamela-pages/001.html').read_text(encoding='utf8')
nav=index_html.split('class="s-nav"',1)[1].split('<div class="col-md-8',1)[0]
titles={int(page):unescape(title) for page,title in re.findall(r'href="https://shamela\.ws/book/8522/(\d+)">([^<]+)</a>',nav)}
trans=str.maketrans('أإآٱىةؤئءڇ','اااايهوياب')
def normalize(text):
 return ''.join(c for c in text.translate(trans) if 'ا'<=c<='ي' and c!='ـ')
def squash(text):
 return re.sub(r'(.)\1+',r'\1',text)
def make_row(kind,text,page,printed):
 return dict(kind=kind,text=text,page=page,printed_page=printed,words=[])
parts=[]
rows=[]
for p in data['pages'][6:]:
 page,printed=p['page'],p['printed_page']
 if page in titles:
  chapter=make_row('chapter',titles[page],page,printed)
  rows.append(chapter)
  parts.append(dict(title=titles[page],page=page,rows=[chapter]))
 paras=[x['text'] for x in p['paragraphs']]
 verses=[]
 for j,text in enumerate(paras):
  if re.search(r'\S\s+\.\.\.\s+\S',text):
   verses.append(text)
  elif text.endswith(' ...') and j+1<len(paras):
   verses.append(text+' '+paras[j+1])
  elif j+1<len(paras) and paras[j+1].startswith('... '):
   verses.append(text+' '+paras[j+1])
 for text in verses:
  row=make_row('verse',text,page,printed)
  rows.append(row)
  parts[-1]['rows'].append(row)
# Shamela lists the first two verses of the numbers chapter after the rest.
# In the recitation these two verses lead into the material on source entries 107–108.
previous=next(p for p in parts if p['page']==106)
numbers=next(p for p in parts if p['page']==109)
carry=[r for r in previous['rows'] if r['page'] in (107,108)]
previous['rows']=[r for r in previous['rows'] if r['page'] not in (107,108)]
numbers['rows'].extend(carry)
rows=[r for part in parts for r in part['rows']]
assert all(len(p['rows'])>=2 for p in parts)
entries=[]; ref=[]; lookup=[]
for row in rows:
 for text in row['text'].split():
  i=len(entries)
  entry=dict(text=text,start=None,end=None,coverage=0,kind=row['kind'],page=row['page'])
  row['words'].append(entry); entries.append(entry)
  for c in squash(normalize(text)) if row is not rows[0] else '':
   ref.append(c); lookup.append(i)
paths=sorted(Path('work/alfiyyah-tokens').glob('*.json'),key=lambda p:int(p.stem))
limit=6271.20
if '--partial' not in sys.argv:
 assert [int(p.stem) for p in paths]==list(range(106))
else:
 end=next((i for i,p in enumerate(paths) if int(p.stem)!=i),len(paths))
 paths=paths[:end]
 limit=len(paths)*60
hyp=[]; times=[]
for path in paths:
 for token in json.loads(path.read_text(encoding='utf8')):
  t=token['time']
  if t<2 or t>=limit: continue
  for c in normalize(token['sym']):
   if hyp and hyp[-1]==c and t-times[-1]<.24: continue
   hyp.append(c); times.append(t)
a=''.join(ref); b=''.join(hyp)
if '--partial' in sys.argv:
 a=a[:int(len(b)*1.2)]
print('parts',len(parts),'verses',sum(r['kind']=='verse' for r in rows),'ref',len(a),'hyp',len(b),'distance',Levenshtein.distance(a,b))
print('start',b[:260]); print('audio end',b[-450:]); print('reference end',a[-450:])
hits=[[] for e in entries]
for op in Levenshtein.opcodes(a,b):
 if op.tag!='equal': continue
 for i,j in zip(range(op.src_start,op.src_end),range(op.dest_start,op.dest_end),strict=True):
  hits[lookup[i]].append(times[j])
next_start=limit
for entry,match in zip(reversed(entries),reversed(hits)):
 size=len(squash(normalize(entry['text'])))
 count=len(match)
 entry['coverage']=round(count/size,3) if size else 0
 if size and count>=max(1,(size+1)//2) and match[-1]-match[0]<=max(.7,.32*size) and match[0]<next_start:
  entry['start']=match[0]
  entry['end']=round(min(next_start,match[-1]+.24,limit),2)
  next_start=entry['start']
for part in parts:
 part['start']=next((w['start'] for r in part['rows'] for w in r['words'] if w['start'] is not None),None)
timed=[e for e in entries if e['start'] is not None]
assert all(0<=e['start']<e['end']<=limit for e in timed)
assert all(a['end']<=b['start'] for a,b in zip(timed,timed[1:]))
print('timed',len(timed),'overall',len(entries))
print('parts at',[(p['page'],p['start']) for p in parts])
print('end',[(w['text'],w['start']) for w in entries[-40:]])
if '--partial' not in sys.argv:
 Path('work/alfiyyah-sync.json').write_text(json.dumps(parts,ensure_ascii=False),encoding='utf8')

