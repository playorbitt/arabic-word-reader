// Zipformer worker for the hadith pipeline.
// usage (cwd = repo root): node pipeline/workers/hp-zip.mjs FILES_JSON OUT_DIR TMP_DIR TRACK [TRACK ...]
// FILES_JSON = JSON array of absolute audio paths, track N = element N-1. Resumable per 60 s block; DONE marker per track.
import { mkdir, writeFile, access, readFile, rm, rename, open, stat } from "node:fs/promises";
import { execFileSync } from "node:child_process";
import * as ort from "onnxruntime-node";
import { readWav } from "./node_modules/@tilawa/core/dist/node.js";
import { ZipformerRunner } from "./node_modules/@tilawa/core/dist/recitation/zipformerRunner.js";
import { KaldiFbank } from "./node_modules/@tilawa/core/dist/recitation/fbank.js";
import { GreedyCtcDecoder } from "./node_modules/@tilawa/core/dist/recitation/ctcDecoder.js";
import { TOKENS, BLANK_ID } from "./node_modules/@tilawa/core/dist/recitation/tokens.js";
import io from "./node_modules/@tilawa/core/dist/recitation/zipformer-io.json" with {type:"json"};
const FF = process.env.FFMPEG || "ffmpeg";
const [filesJson, outDir, tmp, ...tr] = process.argv.slice(2);
const files = JSON.parse(await readFile(filesJson, "utf8"));
const exists = (p) => access(p).then(() => true, () => false);
const session = await ort.InferenceSession.create(process.env.HP_ZIP_MODEL || "legacy.onnx", {executionProviders:["cpu"], graphOptimizationLevel:"all", intraOpNumThreads:Number(process.env.THREADS||2), interOpNumThreads:1});
const stamp = () => new Date().toTimeString().slice(0, 8);
for (const t of tr.map(Number)) {
  const dir = `${outDir}/${String(t).padStart(2, "0")}`;
  await mkdir(dir, {recursive:true});
  if (await exists(dir + "/DONE")) continue;
  const wav = `${tmp}/zip-${t}-${process.pid}.wav`;
  execFileSync(FF, ["-v","error","-y","-i",files[t-1],"-ac","1","-ar","16000","-af","dynaudnorm=f=500:g=15:p=0.9",wav], {maxBuffer: 1<<28});
  const {samples} = await readWav(wav);
  const nblocks = Math.ceil(samples.length / 16000 / 60);
  await writeFile(dir + "/META.json", JSON.stringify({blocks:nblocks, seconds:Math.round(samples.length/160)/100}));
  const lockOf = (b) => `${dir}/${String(b).padStart(4, "0")}.lock`;
  async function claim(b) {
    try { const h = await open(lockOf(b), "wx"); await h.close(); return true; }
    catch { try { const st = await stat(lockOf(b)); if (Date.now() - st.mtimeMs > 900000) await rm(lockOf(b), {force:true}); } catch {} return false; }
  }
  while (true) {
    const pending = [];
    for (let b = 0; b < nblocks; b++) if (!(await exists(`${dir}/${String(b).padStart(4, "0")}.json`))) pending.push(b);
    if (!pending.length) break;
    let did = false;
    for (const block of pending) {
      const path = `${dir}/${String(block).padStart(4, "0")}.json`;
      if (await exists(path) || !(await claim(block))) continue;
      try {
        const origin = Math.max(0, block * 60 - 2), from = origin * 16000;
        const to = Math.min(samples.length, ((block + 1) * 60 + 2) * 16000);
        const fbank = new KaldiFbank();
        const runner = ZipformerRunner.fromSession(session, {...io, encoderFrames:undefined}, ort.Tensor);
        const decoder = new GreedyCtcDecoder(TOKENS, BLANK_ID);
        const tokens = [];
        async function consume(f) { const out = await runner.accept(f); tokens.push(...decoder.consume(out.logProbs, out.frames, io.vocabSize)); }
        for (let pos = from; pos < to; pos += 16000) await consume(fbank.acceptWaveform(samples.subarray(pos, Math.min(to, pos + 16000))));
        await consume(fbank.acceptWaveform(new Float32Array(16000)));
        await consume(fbank.inputFinished());
        tokens.push(...decoder.flush());
        const body = JSON.stringify(tokens.map(x => ({sym:x.sym, time:Math.round((origin + x.frame * .04) * 100) / 100})).filter(x => x.time >= block * 60 && x.time < (block + 1) * 60));
        await writeFile(path + ".tmp", body); await rename(path + ".tmp", path);
      } finally { await rm(lockOf(block), {force:true}); }
      did = true;
      if (block % 10 === 0) console.log(stamp(), "zip track", t, block, "/", nblocks);
    }
    if (!did) await new Promise(r => setTimeout(r, 5000));
  }
  await writeFile(dir + "/DONE", "ok"); await rm(wav, {force:true});
}
console.log("zip finished", tr.join(","));
