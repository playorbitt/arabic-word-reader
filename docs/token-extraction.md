# Extract Arabic tokens and reproduce the Bulugh opening alignment

Use Node.js 22 or newer, Python and FFmpeg. Run `npm install` and `python -m pip install -r requirements-align.txt` from the repository root. Get `zipformer_a0w_ep1_a05.int8.onnx` from the [Tilawa model release](https://github.com/yazinsai/tilawa/releases/tag/zipformer-a0w-ep1-a0.5). The tested local model has SHA-256 `bfb5b712695634a6099b45a6a78003bd5103417c4eae6338277d54679254905a`.

```powershell
ffmpeg -i part-01.mp3 -ac 1 -ar 16000 -c:a pcm_s16le part-01.wav
node scripts/extract-tokens.mjs zipformer_a0w_ep1_a05.int8.onnx part-01.wav tokens
python scripts/align-bulugh-sample.py bulugh_almaram.json tokens/2.json opening-words.json
```

The extractor writes numbered 60-second blocks with absolute audio timestamps. Use a new output directory for each recording or model. To resume work on the same input, rerun the command. The extractor skips existing blocks and accepts an optional zero-based starting block. Its output contains phonetic tokens. Render the source Arabic matched to those tokens for readable Arabic text.

The sample expects the original Arabic of the first hadith in `hadiths[0].arabic` and matches against seconds 153–174 of the tested Part 1 recording. The output timestamps are relative to a preview starting at second 150. The sample preserves the source word spellings and emits `null` where the match is too weak. It produced provisional timings for 11 of 21 words and covers the opening sample only.
