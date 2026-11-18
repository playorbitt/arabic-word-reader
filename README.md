# Arabic Word Reader

Portable Arabic text/audio synchronization assets for hadith.to and other readers.

## Catalog

The public catalog is [library.json](./library.json). It is intentionally small and stable so a website can load it directly from GitHub or jsDelivr:

`https://cdn.jsdelivr.net/gh/playorbitt/arabic-word-reader@main/library.json`

Each book entry may provide:

- `text`: Arabic text or structured token data
- `timings`: word-level timing JSON
- `audio`: an authorized MP3/OGG URL
- `chapters`: chapter/part navigation data
- `sync`: `word`, `sentence`, or `none`
- `license` and `attribution`

Audio should only be added when redistribution is authorized. Large audio files should use Git LFS, Releases, or an authorized CDN; keep alignment JSON and metadata in this repository.

## Timing shape

A timing sidecar should contain a list of tokens with stable IDs:

```json
{
  "schema": "arabic-word-sync.v1",
  "bookId": "example",
  "audio": "https://...",
  "tokens": [
    { "id": "w-0001", "text": "قال", "start": 0.12, "end": 0.48 }
  ]
}
```
