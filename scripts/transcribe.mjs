import { readFile, writeFile } from "node:fs/promises";
import * as ort from "onnxruntime-node";
import { createRecognitionSession } from "@tilawa/core";
import { readWav } from "@tilawa/core/node";

const [audioPath, textPath, outputPath] = process.argv.slice(2);
if (!audioPath || !textPath || !outputPath) {
  console.error("Usage: node scripts/transcribe.mjs recording.wav text.json output.json");
  process.exit(2);
}

const text = JSON.parse(await readFile(textPath, "utf8"));
const { samples, sampleRate } = await readWav(audioPath);
const events = [];
const session = await createRecognitionSession({
  ort,
  model: () => readFile(process.env.TILAWA_MODEL ?? "zipformer_a0w_ep1_a05.int8.onnx"),
  corpus: async () => [],
  onEvent: (event) => events.push(event),
});

for (let offset = 0; offset < samples.length; offset += 4800) {
  await session.feed(samples.subarray(offset, offset + 4800));
}
await session.stop();

const transcript = events.filter((event) => event.type === "raw_transcript").at(-1)?.text ?? "";
const result = {
  ...text,
  transcript,
  events,
  words: text.words.map((word) => ({ ...word, start: null, end: null, confidence: null })),
};
await writeFile(outputPath, JSON.stringify(result, null, 2) + "\n", "utf8");

