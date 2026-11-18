import json, re, sys
from pathlib import Path
sys.path.insert(0,'work/rapid-deps')
from rapidfuzz.distance import Levenshtein
pages=json.loads(Path('work/kutub-pages-data.json').read_text(encoding='utf8'))
assert [p['index'] for p in pages] == list(range(1,246))
assert [p['current_page'] for p in pages] == list(range(23,268))
assert len({p['page_key'] for p in pages}) == 245
table=str.maketrans('أإآٱىةؤئءڇ','اااايهوياب')
def normalize(text):
 return ''.join(c for c in text.translate(table) if 'ا'<=c<='ي' and c!='ـ')
def squash(text):
 return re.sub(r'(.)\1+',r'\1',text)
entries=[]
for p in pages:
 for word in p['matn'].split():
  entries.append(dict(page=p['index'],text=word, ref=normalize(word),hits=[]))
first=next(i for i,w in enumerate(entries) if w['text']=='هذا')
for i,w in enumerate(entries):
 if i<first: w['ref']=''
for i,w in enumerate(entries[:50]):
 if w['text']=='عقيدة': w['ref']=normalize('اعتقاد')
 if w['text']=='رضوان': w['ref']=normalize('رحمة')
 if w['text']=='أجمعين،': w['ref']=''
ref=[]; lookup=[]
for i,w in enumerate(entries):
 for c in squash(w['ref']):
  ref.append(c); lookup.append(i)
hyp=[]; times=[]
for block in range(30):
 for t in json.loads(Path(f'work/full-tokens/{block}.json').read_text(encoding='utf8')):
  if t['time']<20.6 or t['time']>1740.35: continue
  for c in normalize(t['sym']):
   if hyp and hyp[-1]==c and t['time']-times[-1]<.24: continue
   hyp.append(c); times.append(t['time'])
a=''.join(ref); b=''.join(hyp)
print('reference letters',len(a),'detected letters',len(b),'distance',Levenshtein.distance(a,b))
print('heard ending',b[-350:]); print('text ending',a[-350:])
for op in Levenshtein.opcodes(a,b):
 if op.tag!='equal': continue
 for i,j in zip(range(op.src_start,op.src_end),range(op.dest_start,op.dest_end),strict=True):
  entries[lookup[i]]['hits'].append(times[j])
for w in entries:
 hits=w['hits']
 size=len(squash(w['ref']))
 w['coverage']=round(len(hits)/size,3) if size else 0
 # Discard weak matches and alignments spread across multiple unrelated utterances.
 if hits and len(hits)>=max(1,(size+1)//2) and hits[-1]-hits[0]<=max(.65,.34*size):
  w['start']=hits[0]
 else: w['start']=None
for i,w in enumerate(entries):
 if w['start'] is None:
  w['end']=None
 else:
  following=next((x['start'] for x in entries[i+1:] if x['start'] is not None),1740.35)
  if following<=w['start']:
   w['start']=w['end']=None
  else: w['end']=round(min(following,w['hits'][-1]+.28,1740.35),2)
 del w['hits'], w['ref']
timed=[w for w in entries if w['start'] is not None]
print('timed',len(timed),'of',len(entries),'last',timed[-12:])
for n in [1,6,25,50,75,100,125,150,175,200,225,245]:
 w=[w for w in entries if w['page']==n]
 print(n,len(w),[(a['text'],a['start']) for a in w[:5]])
Path('work/full-word-timings.json').write_text(json.dumps(entries,ensure_ascii=False),encoding='utf8')

