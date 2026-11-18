import json
from urllib.request import Request, urlopen
from pathlib import Path
base = 'https://hetchyy-quranic-universal-aligner.hf.space/gradio_api/call/process_audio_session'
path, = json.loads(Path('work/aligner-upload.json').read_text())
for device in ['GPU', 'CPU']:
    data = {'data': [{'path': path, 'orig_name': 'aqidah-sample.wav', 'meta': {'_type': 'gradio.FileData'}}, 200, 1000, 100, 'Base', device]}
    req = Request(base, data=json.dumps(data).encode(), headers={'Content-Type': 'application/json'})
    with urlopen(req, timeout=45) as res:
        event = json.load(res)['event_id']
    print(device, event, flush=True)
    with urlopen(f'{base}/{event}', timeout=270) as res, Path(f'work/aligner-{device}.sse').open('wb') as out:
        for line in res:
            out.write(line)
            out.flush()
    raw = Path(f'work/aligner-{device}.sse').read_text(encoding='utf8')
    print(raw[-2500:].encode('ascii','backslashreplace').decode(), flush=True)
    if 'event: complete' in raw and not any(k in raw for k in ['gpu_quota', '\"error\":']):
        break

