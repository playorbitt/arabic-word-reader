# Arabic Word-Sync Reader

An offline-first reader that aligns a recorded Arabic reading with a supplied Arabic text and highlights the text word by word during playback.

The repository contains the reader pipeline and its export format. It does not contain recordings, model weights, or copied book editions. Supply those locally and keep their separate terms with the source materials.

## What it does

- accepts 16 kHz mono WAV audio through the Tilawa streaming engine
- emits decoded Arabic tokens with audio-clock positions
- aligns those tokens to supplied Arabic words
- exports a JSON timing file for a browser reader
- preserves uncertain words so the UI can mark them for review

## Local setup

```powershell
npm install
node scripts/transcribe.mjs path/to/recording.wav path/to/text.json output/timings.json
```

`text.json` should contain an object with a `words` array. Each item needs a `text` field. The output adds `start`, `end`, and `confidence` fields where the audio provides a usable match.

The current browser previews were generated from the Alfiyyah and al-ʿAqīdah al-Ṭaḥāwiyyah experiments. They remain local because the recordings and editions have separate redistribution terms.

## Third-party components

The reader code is MIT-licensed. The tested Zipformer model and its derived artifacts are under Quran-Lab NPL-1.2. That license permits free use and free hosting, requires derivatives to retain NPL-1.2, and prohibits charging for the model or a feature it powers. See `THIRD_PARTY_NOTICES.md`.


