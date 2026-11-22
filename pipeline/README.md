# Hadith audio pipeline

One command per book takes audio + printed text to a finished reader page
(word-by-word sync, search, English toggle, click-a-word-to-seek).

```
cd work/hadith-pipeline
python -m hp scaffold                         # make books/<slug>.json for each collection (once)
python -m hp list                             # audio hours, progress, stages done, text source per book
python -m hp run BOOK                         # all stages: prep, text, asr, align, qa, build
python -m hp run BOOK --stages align,qa,build # re-run only the cheap stages
python -m hp status --html                    # writes outputs/hadith-pipeline/status.html (live dashboard)
python -m hp textfind                        # find + validate a JSON text source for each book (sets text.url when found)
python -m hp textcheck BOOK                   # cut-off hadiths, numbering, empties, edition vs audio (also runs inside the text stage)
python -m hp serve                            # Range-capable server on :8767 for the previews
```

## Stages
| stage | what it does | output (in `outputs/hadith-pipeline/<slug>/`) |
|---|---|---|
| prep  | checks every track exists with the expected size, joins finished `.part` pieces, checks it decodes and the length matches the index | `audio-report.json` |
| text  | downloads / reads the printed text and normalises it | `text.json` |
| asr   | FastConformer (words) + Zipformer (sounds) over every 60 s block; N worker processes share blocks through lock files; safe to stop and re-run | `recognition/`, `zipformer/` |
| align | anchors + word matching, Zipformer edge check (moves edges at most 1.5 s), gap recovery with both models | `align.json`, `wordmatch.json` |
| qa    | coverage, absent blocks, order problems, weak and stretched matches | `qa.json` |
| build | the reader page | `preview.html` |

## Adding a book
Edit `books/<slug>.json`. Required: `audio_dir`. For the text stage add either
`"text": {"url": "https://hadithunlocked.com/<alias>.json"}` or `"text": {"path": "outputs/.../file.json"}`
(hadithunlocked structure, or a plain list of `{"number", "text"/"ar", "chain"?, "en"?}`).
Optional: `title_en`, `title_ar`, `work_dir` (use an existing folder instead of the default).

## Notes
* Everything is resumable: finished blocks and tracks are skipped. A crashed worker's lock expires after 15 min.
* `asr` uses the venv python (`work/asr-venv`) for FastConformer and node for Zipformer.
* Regression: on Sahih Ibn Khuzaymah the pipeline reproduces the hand-run result exactly (3,008 of 3,282 hadiths, 0 differences).
* Known limits: a recording that comments instead of reading a hadith leaves that hadith without audio (reported in `qa.json`); sunnah.com is behind a bot check, so fetch its text through the browser and save it as a `path` file.

## Setup notes (for a fresh checkout)
* Needs Python 3.10+, ffmpeg on PATH (or `FFMPEG=...`), node (Zipformer) and a venv with `numpy` + `onnxruntime` (set `HP_PYTHON`).
* The code expects to live at `<repo root>/work/hadith-pipeline` next to `work/model-cache/iam-muslim-fork` (FastConformer) and `work/tilawa-test/legacy.onnx` + `work/tilawa-legacy/node_modules/@tilawa/core` (Zipformer).
  `workers/hp-zip.mjs` is the Zipformer worker; copy it to `work/tilawa-legacy/hp-zip.mjs`.
* Internet Archive keys: `HP_IA_ACCESS` / `HP_IA_SECRET` or `~/.hp-ia.ini` - never commit them.
* Audio, model output and readers are written to `<repo root>/outputs/` and are not part of this repository.
