import { readFile, mkdir, writeFile, access, rename } from 'node:fs/promises';
import { join } from 'node:path';
import { randomUUID } from 'node:crypto';
import * as ort from 'onnxruntime-node';
import { ZipformerRunner } from '../node_modules/@tilawa/core/dist/recitation/zipformerRunner.js';
import { KaldiFbank } from '../node_modules/@tilawa/core/dist/recitation/fbank.js';
import { GreedyCtcDecoder } from '../node_modules/@tilawa/core/dist/recitation/ctcDecoder.js';
import { TOKENS, BLANK_ID } from '../node_modules/@tilawa/core/dist/recitation/tokens.js';
import io from '../node_modules/@tilawa/core/dist/recitation/zipformer-io.json' with { type: 'json' };

const sampleRate = 16000;
const blockSeconds = 60;
const overlapSeconds = 2;

function readWav(buffer) {
  const invalid = 'Input must be a 16 kHz mono 16-bit PCM WAV';
  if (buffer.toString('ascii', 0, 4) !== 'RIFF' || buffer.toString('ascii', 8, 12) !== 'WAVE') throw new Error(invalid);
  let validFormat = false;
  let data;
  for (let offset = 12; offset + 8 <= buffer.length;) {
    const id = buffer.toString('ascii', offset, offset + 4);
    const size = buffer.readUInt32LE(offset + 4);
    const start = offset + 8;
    const end = start + size;
    if (end > buffer.length) throw new Error(invalid);
    if (id === 'fmt ' && size >= 16) {
      validFormat = buffer.readUInt16LE(start) === 1 && buffer.readUInt16LE(start + 2) === 1 &&
        buffer.readUInt32LE(start + 4) === sampleRate && buffer.readUInt16LE(start + 14) === 16;
    }
    if (id === 'data') data = buffer.subarray(start, end);
    offset = end + (size % 2);
  }
  if (!validFormat || !data?.length || data.length % 2) throw new Error(invalid);
  const samples = new Float32Array(data.length / 2);
  for (let i = 0; i < samples.length; i++) samples[i] = data.readInt16LE(i * 2) / 32768;
  return samples;
}

const [model, audioPath, outputDir, firstBlock = '0'] = process.argv.slice(2);
const index = Number(firstBlock);
if (!model || !audioPath || !outputDir || !/^\d+$/.test(firstBlock) || !Number.isSafeInteger(index)) {
  console.error('Usage: node scripts/extract-tokens.mjs model.onnx audio.wav output-dir [first-block]');
  process.exit(2);
}
const samples = readWav(await readFile(audioPath));
await mkdir(outputDir, { recursive: true });
const session = await ort.InferenceSession.create(model, {
  executionProviders: ['cpu'], graphOptimizationLevel: 'all', intraOpNumThreads: 2, interOpNumThreads: 1,
});
try {
  for (let block = index; block * blockSeconds * sampleRate < samples.length; block++) {
    const path = join(outputDir, `${block}.json`);
    if (await access(path).then(() => true, () => false)) continue;
    const start = block * blockSeconds;
    const end = start + blockSeconds;
    const origin = Math.max(0, start - overlapSeconds);
    const to = Math.min(samples.length, (end + overlapSeconds) * sampleRate);
    const fbank = new KaldiFbank();
    const runner = ZipformerRunner.fromSession(session, { ...io, encoderFrames: undefined }, ort.Tensor);
    const decoder = new GreedyCtcDecoder(TOKENS, BLANK_ID);
    const tokens = [];
    async function consume(features) {
      const output = await runner.accept(features);
      tokens.push(...decoder.consume(output.logProbs, output.frames, io.vocabSize));
    }
    for (let pos = origin * sampleRate; pos < to; pos += sampleRate) {
      await consume(fbank.acceptWaveform(samples.subarray(pos, Math.min(to, pos + sampleRate))));
    }
    await consume(fbank.acceptWaveform(new Float32Array(sampleRate)));
    await consume(fbank.inputFinished());
    tokens.push(...decoder.flush());
    const rows = tokens.map(({ sym, frame }) => ({ sym, time: Math.round((origin + frame * 0.04) * 100) / 100 }))
      .filter(({ time }) => time >= start && time < end);
    const temporaryPath = `${path}.${randomUUID()}.tmp`;
    await writeFile(temporaryPath, JSON.stringify(rows));
    await rename(temporaryPath, path);
    console.log('Completed block', block);
  }
} finally {
  await session.release();
}
