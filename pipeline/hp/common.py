"""Shared helpers for the hadith audio pipeline."""
import json, os, re, sys, time, tempfile
from pathlib import Path

PIPE = Path(__file__).resolve().parents[1]
# workspace root (holds outputs/, audio, models): HP_ROOT, else the old work/hadith-pipeline layout, else the folder above this one
ROOT = Path(os.environ['HP_ROOT']) if os.environ.get('HP_ROOT') else (PIPE.parents[1] if PIPE.parent.name == 'work' else PIPE.parent)
OUTPUTS = ROOT / 'outputs'
COLLECTIONS = OUTPUTS / 'rawy-hadith-collections'
BOOKS = PIPE / 'books'
import shutil
FFMPEG = os.environ.get('FFMPEG') or shutil.which('ffmpeg') or 'ffmpeg'      # set FFMPEG=... if it is not on PATH
FC_MODEL_DIR = Path(os.environ.get('HP_FC_MODEL') or ROOT / 'work' / 'model-cache' / 'iam-muslim-fork')
ZIP_MODEL = Path(os.environ.get('HP_ZIP_MODEL') or ROOT / 'work' / 'tilawa-test' / 'legacy.onnx')
PY = os.environ.get('HP_PYTHON') or str(ROOT / 'work' / 'asr-venv' / 'Scripts' / 'python.exe')
NODE = os.environ.get('HP_NODE') or 'node'


def atomic_write(path, data, binary=False):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix='.tmp')
    with os.fdopen(fd, 'wb' if binary else 'w', **({} if binary else {'encoding': 'utf8'})) as f:
        f.write(data)
    os.replace(tmp, path)


def write_json(path, obj):
    atomic_write(path, json.dumps(obj, ensure_ascii=False))


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding='utf8'))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


class Book:
    """One book = one config file in books/<slug>.json."""
    def __init__(self, slug):
        cfg = read_json(BOOKS / f'{slug}.json')
        if cfg is None:
            raise SystemExit(f'No config for "{slug}" (expected {BOOKS / (slug + ".json")})')
        self.cfg = cfg
        self.slug = slug
        self.title = cfg.get('title_en') or slug
        self.dir = Path(cfg['work_dir']) if cfg.get('work_dir') else OUTPUTS / 'hadith-pipeline' / slug
        if not self.dir.is_absolute():
            self.dir = ROOT / self.dir
        self.audio_dir = ROOT / cfg['audio_dir']
        self.rec = self.dir / 'recognition'      # FastConformer blocks
        self.zip = self.dir / 'zipformer'        # Zipformer blocks
        self.dir.mkdir(parents=True, exist_ok=True)

    # ---- audio -----------------------------------------------------
    def tracks(self):
        """Ordered list of audio files (Path), 1-based by position."""
        pats = self.cfg.get('audio_glob', '[0-9][0-9][0-9]-*.*')
        files = sorted(p for p in self.audio_dir.glob(pats)
                       if p.suffix.lower() in ('.ogg', '.mp3', '.m4a', '.opus', '.wav', '.flac'))
        return files

    def path(self, name):
        return self.dir / name


def fold(w):
    """Arabic letter folding for matching (no diacritics, unify alef/ya/ta marbuta)."""
    w = _DIA.sub('', w)
    w = re.sub('[أإآٱ]', 'ا', w).replace('ى', 'ي').replace('ة', 'ه')
    return re.sub(r'[^\u0621-\u064A]', '', w)


def sound_norm(s):
    """Consonant skeleton used to compare text against Zipformer phoneme symbols."""
    s = _DIA.sub('', s).translate(str.maketrans('ظضثذةۃأإآٱى', 'دطسزههااااي'))
    s = re.sub('[^\u0621-\u064A]', '', s)
    s = re.sub('[اءويى]', '', s)
    return re.sub(r'(.)\1+', r'\1', s)


_DIA = re.compile(r'[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06ED\u0640]')


def log(*a):
    print(time.strftime('%H:%M:%S'), *a, flush=True)


SYMBOLS = {'ﷺ': 'صلى الله عليه وسلم', '﷽': 'بسم الله الرحمن الرحيم'}   # ﷺ  ﷽  (spoken as several words)


def token_folds(raw):
    """Folded tokens a printed word is spoken as: normally one; the ﷺ sign is spoken as 4 words; punctuation-only gives none."""
    for sym, spoken in SYMBOLS.items():
        if sym in raw:
            return [fold(x) for x in spoken.split()]
    f = fold(raw)
    return [f] if f else []


STREAMABLE_EXT = {'.mp3', '.ogg', '.opus', '.m4a'}


def remote_name(book, i, path):
    """File name used on archive.org / in sync data: original if plain ASCII and streamable, else a clean numbered name;
    non-streamable containers (mkv, wav, flac) become .mp3."""
    path = Path(path); ext = path.suffix.lower()
    stem = path.stem if path.stem.isascii() else f'{i:03d}-{book.slug}'
    return stem + (ext if ext in STREAMABLE_EXT else '.mp3')
