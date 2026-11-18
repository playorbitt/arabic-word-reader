import difflib, json, re
from pathlib import Path
from html import unescape
page=Path('work/kutub.html').read_text(encoding='utf8')
start=page.index('هذا ذكر')
text=unescape(page[start:page.index('\nــ',start)])
phrases=re.split(r'(?<=الملة:) |(?<=الكوفي،) |(?<=الأنصاري،) |(?<=الشيباني) |(?<=أجمعين،) ',text)
windows=[(20.6,29.5),(30.2,34.2),(35.2,40.1),(41,45.5),(45.5,47.6),(48.1,55.9)]
tokens=json.loads(Path('work/tilawa-tokens.json').read_text())['tokens']
table=str.maketrans('أإآٱىةؤئءڇ','اااايهوياب')
def normalized(items):
    out=[]
    for text, key in items:
        for c in text.translate(table):
            if 'ا' <= c <= 'ي' and c!='ـ' and (not out or c!=out[-1][0] or isinstance(key,int) and key!=out[-1][1]):
                out.append((c,key))
    return out
groups=[]
for index,(phrase,(start,end)) in enumerate(zip(phrases,windows)):
    words=phrase.split()
    alternate=words.copy()
    if index==0: alternate[3]='اعتقاد'
    if index==4:
        alternate[0]='رحمة'
        alternate[-1]=''
    ref=normalized((word,i) for i,word in enumerate(alternate))
    hyp=normalized((t['sym'], t['frame']*.04) for t in tokens if start<=t['frame']*.04<end)
    hits=[[] for word in words]
    for i,j,n in difflib.SequenceMatcher(None,''.join(c for c,k in ref), ''.join(c for c,k in hyp),autojunk=False).get_matching_blocks():
        for offset in range(n):
            hits[ref[i+offset][1]].append(hyp[j+offset][1])
    print('---', ''.join(c for c,k in hyp))
    for word, times in zip(words,hits): print(word, [round(t,2) for t in times])
    groups.append([dict(text=word, hits=times, variant=alt if word!=alt else None) for word,alt,times in zip(words,alternate,hits)])
Path('work/kutub-word-hits.json').write_text(json.dumps(groups,ensure_ascii=False),encoding='utf8')

