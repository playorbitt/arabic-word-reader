import argparse
import json
import re
from pathlib import Path

from rapidfuzz.distance import Levenshtein


def letters(text):
    text = text.translate(str.maketrans('أإآٱىةؤئ', 'اااايهوي'))
    return re.sub(r'(.)\1+', r'\1', ''.join(c for c in text if 'ا' <= c <= 'ي' and c != 'ـ'))


parser = argparse.ArgumentParser(description='Reproduce the opening Bulugh al-Maram alignment sample.')
parser.add_argument('book', type=Path)
parser.add_argument('tokens', type=Path, help='Token block 2 from Part 1')
parser.add_argument('output', type=Path)
args = parser.parse_args()
source = json.loads(args.book.read_text(encoding='utf8'))
words = re.findall(r'[\u0621-\u065f\u0670-\u0671]+', source['hadiths'][0]['arabic'].split('}')[0])
ref = [(i, c) for i, word in enumerate(words) for c in letters(word)]
heard = []
for token in json.loads(args.tokens.read_text(encoding='utf8')):
    if not 153 <= token['time'] < 174:
        continue
    for c in letters(token['sym']):
        if not heard or heard[-1][1] != c or token['time'] - heard[-1][0] >= .24:
            heard.append((token['time'], c))
hits = [[] for _ in words]
for op in Levenshtein.opcodes(''.join(c for _, c in ref), ''.join(c for _, c in heard)):
    if op.tag == 'equal':
        for i, j in zip(range(op.src_start, op.src_end), range(op.dest_start, op.dest_end)):
            hits[ref[i][0]].append(heard[j][0])
result = []
for word, times in zip(words, hits):
    if len(times) >= max(2, (len(letters(word))*3+3)//4) and times[-1]-times[0] < 1.4:
        result.append({'sym': word, 'time': round(times[0]-150, 2), 'end': round(times[-1]-150+.24, 2)})
    else:
        result.append({'sym': word, 'time': None})
args.output.write_text(json.dumps(result, ensure_ascii=False), encoding='utf8')
print(sum(w['time'] is not None for w in result), len(result))
