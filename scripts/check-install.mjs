import { access } from "node:fs/promises";
import { constants } from "node:fs";
const model = process.env.TILAWA_MODEL || "zipformer_a0w_ep1_a05.int8.onnx";
try { await access(model, constants.R_OK); console.log(`Ready: model is readable at ${model}`); }
catch { console.error(`Missing TILAWA_MODEL: ${model}`); console.error("Download the licensed Tilawa Zipformer model and set TILAWA_MODEL to its .onnx path."); process.exit(1); }
console.log("Ready: install and model checks passed.");

