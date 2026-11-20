"""FastConformer worker (run with the asr-venv python).
usage: python -m hp.fc_worker BOOK TRACK [TRACK ...]      (1-based track numbers)
Resumable: every 60 s block is a json file; a finished track gets a DONE marker."""
import sys, os, json, time, subprocess
from importlib.util import module_from_spec, spec_from_file_location
import numpy as np
import onnxruntime as ort
from .common import Book, FFMPEG, FC_MODEL_DIR, write_json, log

sys.path.insert(0, str(FC_MODEL_DIR))
_spec = spec_from_file_location('fc_features', FC_MODEL_DIR / 'QuranKarim-audio_features.py')
_mod = module_from_spec(_spec); _spec.loader.exec_module(_mod)
extractor = _mod.LogMelExtractor(sample_rate=16000)
vocab = {int(k): v for k, v in json.loads((FC_MODEL_DIR / 'vocab.json').read_text(encoding='utf-8')).items()}
BLANK = max(vocab)


def decode(logprobs, offset, core_start, core_end):
    ids = np.argmax(logprobs, axis=1)
    rec, prev = [], None
    for frame, tid in enumerate(ids):
        tid = int(tid)
        if tid != prev and tid != BLANK:
            rec.append((vocab.get(tid, ''), offset + frame * .08))
        prev = tid
    words = []
    for sym, t in rec:
        if sym.startswith('▁'):
            if words: words[-1]['end'] = round(t, 3)
            words.append({'text': sym.lstrip('▁'), 'start': round(t, 3)})
        elif words:
            words[-1]['text'] += sym
    if words: words[-1]['end'] = round(offset + len(ids) * .08, 3)
    return [w for w in words if w['text'] and core_start <= w['start'] < core_end]


def claim(lock, stale=900):
    """Atomically claim a block. A lock older than `stale` seconds belongs to a dead worker and is stolen."""
    try:
        os.close(os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)); return True
    except FileExistsError:
        try:
            if time.time() - os.path.getmtime(lock) > stale: os.remove(lock)
        except OSError: pass
        return False


def pcm_of(path):
    raw = subprocess.run([FFMPEG, '-v', 'error', '-i', str(path), '-ac', '1', '-ar', '16000',
                          '-af', 'dynaudnorm=f=500:g=15:p=0.9', '-f', 's16le', '-'], capture_output=True).stdout
    if not raw:
        raise RuntimeError(f'ffmpeg returned no audio for {path}')
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768


def main():
    book = Book(sys.argv[1]); tracks = [int(x) for x in sys.argv[2:]]
    files = book.tracks()
    opt = ort.SessionOptions(); opt.intra_op_num_threads = int(os.environ.get('THREADS', '3'))
    sess = ort.InferenceSession(str(FC_MODEL_DIR / 'model.onnx'), sess_options=opt, providers=['CPUExecutionProvider'])
    for t in tracks:
        d = book.rec / f'{t:02}'; d.mkdir(parents=True, exist_ok=True)
        if (d / 'DONE').exists(): continue
        pcm = pcm_of(files[t - 1]); n = int(np.ceil(len(pcm) / 16000 / 60)); t0 = time.time()
        write_json(d / 'META.json', {'blocks': n, 'seconds': round(len(pcm) / 16000, 1)})
        while True:
            pending = [m for m in range(n) if not (d / f'{m:04}.json').exists()]
            if not pending: break
            did = False
            for m in pending:
                out = d / f'{m:04}.json'
                if out.exists() or not claim(d / f'{m:04}.lock'): continue
                try:
                    lo = max(0, m * 60 - 2); hi = min(len(pcm) / 16000, m * 60 + 62)
                    feat = extractor(pcm[int(lo * 16000):int(hi * 16000)])[None, :, :]
                    lg = sess.run(['logprobs'], {'audio_signal': feat, 'length': np.array([feat.shape[2]], dtype=np.int64)})[0][0]
                    write_json(out, {'track': t, 'minute': m, 'words': decode(lg, lo, m * 60, m * 60 + 60)})
                finally:
                    try: os.remove(d / f'{m:04}.lock')
                    except OSError: pass
                did = True
                if m % 10 == 0: log(book.slug, 'fc track', t, m, '/', n, round(time.time() - t0), 's')
            if not did: time.sleep(5)       # other workers hold the remaining blocks
        (d / 'DONE').write_text('ok')
    log('fc finished', tracks)


if __name__ == '__main__':
    main()
