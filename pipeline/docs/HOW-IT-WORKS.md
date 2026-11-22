# How the audio-to-reader system works (full steps)

Goal: take a long recording of a hadith book plus the book's printed text and produce
(1) a reader page where every word lights up as it is spoken, click-a-word-to-seek, and
(2) a package for the hadith.to site, with the audio hosted on the Internet Archive.

Everything runs on one ordinary Windows PC (CPU only), book by book, and is resumable.

## 0. The two recognisers

| | FastConformer | Zipformer |
|---|---|---|
| gives | Arabic **words** with times (0.08 s frames) | **sounds / phonemes** with times (0.04 s frames) |
| runs in | Python, `onnxruntime` (`hp/fc_worker.py`) | Node, `@tilawa/core` + `onnxruntime-node` (`workers/hp-zip.mjs`) |
| model file | `model.onnx`, `vocab.json`, `QuranKarim-audio_features.py` in `HP_FC_MODEL` | `legacy.onnx` at `HP_ZIP_MODEL` |
| used for | main word timings | checking and moving hadith edges (consonant skeleton match, at most 1.5 s), filling gaps |

Weights are **not** in this repo (FastConformer is 126 MB; licences differ; the Zipformer is NPL-1.2, see `THIRD_PARTY_NOTICES.md`).
Both are optional in the sense that FastConformer alone is enough for highlighting; Zipformer sharpens hadith starts.

Both work on 60 s blocks (2 s margin each side), write one JSON per block, and skip blocks that exist, so a crash or reboot loses at most
the blocks being decoded. Several worker processes share one long track through `NNNN.lock` files (created with O_EXCL; stale after 15 min).
Workers run at IDLE priority so the PC stays usable. Zipformer decodes very long tracks in 4 h wav pieces (Node cannot read a >2 GB file).

## 1. One-time setup
```
python -m venv asr-venv ; asr-venv\Scripts\pip install onnxruntime numpy
cd workers ; npm install                      # @tilawa/core, onnxruntime-node
set HP_ROOT=<workspace with outputs/ and the audio>
set HP_FC_MODEL=<folder with model.onnx, vocab.json, QuranKarim-audio_features.py>
set HP_ZIP_MODEL=<path>\legacy.onnx
set HP_PYTHON=<path>\asr-venv\Scripts\python.exe        # optional
```
ffmpeg must be on PATH (or set `FFMPEG`). Internet Archive keys: `HP_IA_ACCESS` / `HP_IA_SECRET` or `~/.hp-ia.ini` (`[s3] access= secret=`); never commit them.

## 2. Per book, step by step
1. **Config** `books/<slug>.json`: `audio_dir`, optional `audio_glob`, `title_en/ar`, `text`, `ia`, `site`.
   Use only the full recording (a book folder often also has short excerpts or a video - exclude them with `audio_glob`).
2. **Text.** Sources, in order of preference:
   * hadithunlocked JSON (`textfind` looks for it) - but it is a *different edition* for some books (Lulu: it carries Bukhari chains the book omits). Always verify, step 5.
   * Shamela (`python -m hp shamela BOOK --id N`, polite 0.35 s delay, cached). Two page layouts are handled:
     `span` (number inside `<span class="c2">(N)</span>`, e.g. al-Ilmam) and `plain` (`"N - text"`, footnotes after `<hr>`, e.g. Bulugh, Lulu, Muntaqa;
     set `"shamela": {"id": N, "layout": "plain"}`). The parser strips page markers `⦗٦⦘`, joins `(أ)/(ب)` variants, handles "189 و 190 -" joint numbers and repairs misprinted numbers.
   * sunnah.com is behind a bot check; fetch it in a browser and save a JSON.
3. **Prep** (`hp prep`): checks the audio is complete and decodes; joins `.part` pieces.
4. **ASR** (`python -m hp run BOOK --stages asr --fc-workers 2 --zip-workers 2`): about 14-17 audio-minutes per wall-minute per model on a normal PC.
5. **Verify the text before trusting it** (this saved us twice):
   * `python -m hp textcheck BOOK` (cut-offs "…", numbering gaps, empties, heard words missing from the text).
   * `python tools/compare_editions.py BOOK SHAMELA_ID` against a second printed edition. If the 5-word anchors shared by both texts are low, it is the wrong text.
     If many of our entries belong together there, we split hadith too finely (add `--apply` to regroup).
   * Listen / read what the recogniser heard in the first minutes (`recognition/NN/0000.json`): a recording usually has an introduction and chapter headings that the text lacks.
6. **Align** (`--stages align,qa,build`): unique 4-gram anchors + longest increasing subsequence, difflib inside gaps, sound-alike fill (mis-heard letters, ratio >= 0.72),
   Zipformer edge check, gap recovery with both models; groups over 60 hadith with no match are treated as absent from the recording.
   The ﷺ sign is spoken as four words; punctuation-only tokens merge into the neighbouring word.
7. **QA** (`qa.json`): coverage, absent blocks, order problems, weak and stretched matches. Spot-check the weak ones in `preview.html`.
8. **Reader** `preview.html` (serve through `python -m hp serve`; seeking needs HTTP Range, plain `http.server` breaks it).
9. **Upload audio** (`python -m hp upload BOOK --go`): Internet Archive S3 multipart, 8 connections (the Archive throttles per connection, so this is ~15x faster than one PUT), resumable.
   MP3/M4A are streamable; the item gets title, subjects, descriptions. Then upload the sync data (text + word timings) the same way (no `--no-sync`).
10. **Site package** (`python -m hp export-site BOOK --zip`): `index.json`, `book-N.json` with `src:{f,s,e}` slices of the long recording (no clip cutting) and `audioBase`
    pointing at the Archive; `registry-entry.json`; `manifest.json`. The site needs the player patch that plays a slice of a long file (not included here).
11. **Dashboard** `python -m hp status --html`.

## 3. Lessons (each cost us time)
* The text is the risk, not the models. Check it against a second edition and against the audio before building anything.
* Editions number differently; keep the printed edition's numbering and merge variant readings ("وزاد أبو داود", "وفي رواية") into their hadith.
* The reciter often skips editor additions, takhrij lines, `(أ)/(ب)` variants, and may skip whole hadith; reads an introduction and chapter headings that the text lacks.
* A PC that sleeps kills background jobs; everything is resumable, just restart.
* Very long files: Zipformer pieces (4 h), upload multipart, keep temp wavs off a full disk.
* Results: Umdat 100%, Bulugh 99.9%, al-Ilmam 99.8% (after regrouping), Lulu 98.3% (after switching to the right text), Khuzayma 93.3% of hadith matched to audio.

## 4. Command reference
`scaffold, list, status [--html], run BOOK [--stages a,b] [--force] [--fc-workers N --zip-workers N], asr-all, textfind, textcheck, shamela BOOK --id N, ia-check, ia-plan, upload BOOK [--go --parts --single --no-sync], export-site BOOK [--zip --clips], serve`
