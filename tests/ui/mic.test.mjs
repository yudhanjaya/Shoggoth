// U7: the mic button. The WAV it builds from the worklet's samples, the words going into the composer, the download
// progress it shows on first use, and the button on the real page.
// Run: node tests/ui/mic.test.mjs
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const html = readFileSync(new URL("../../shoggoth/index.html", import.meta.url), "utf8");
const slice = (from, to) => {
  const a = html.indexOf(from), b = html.indexOf(to, a + 1);
  assert.ok(a > 0 && b > a, from + " ... " + to + " not found in index.html");
  return html.slice(a, b);
};

// ---- 16 kHz mono 16-bit WAV, so whisper-server needs no ffmpeg
const helpers = new Function("MIC", slice("function micWav", "function micPaint") + "; return {micWav, micMerge, micProgress, micTitle};");
const { micWav, micMerge, micProgress, micTitle } = helpers({ state: "idle", note: "" });
const tag = (wav, at) => String.fromCharCode(...wav.slice(at, at + 4));

{
  const samples = new Float32Array([0, 0.5, -0.5, 1, -1, 2, -2]);
  const wav = micWav([samples], 16000), v = new DataView(wav.buffer);
  assert.equal(wav.length, 44 + samples.length * 2, "a WAV is a 44-byte header and two bytes a sample");
  assert.equal(tag(wav, 0), "RIFF");
  assert.equal(tag(wav, 8), "WAVE");
  assert.equal(v.getUint32(4, true), 36 + samples.length * 2, "the RIFF size is wrong");
  assert.equal(v.getUint16(20, true), 1, "not PCM");
  assert.equal(v.getUint16(22, true), 1, "not mono");
  assert.equal(v.getUint32(24, true), 16000, "not 16 kHz");
  assert.equal(v.getUint32(28, true), 32000, "byte rate is wrong");
  assert.equal(v.getUint16(34, true), 16, "not 16-bit");
  assert.equal(tag(wav, 36), "data");
  assert.equal(v.getUint32(40, true), samples.length * 2, "the data size is wrong");
  const at = i => v.getInt16(44 + i * 2, true);
  assert.equal(at(0), 0);
  assert.equal(at(1), 16383, "0.5 is not half full scale");
  assert.equal(at(2), -16384, "-0.5 is not half negative full scale");
  assert.equal(at(3), 32767, "1 is not full scale");
  assert.equal(at(4), -32768, "-1 is not negative full scale");
  assert.equal(at(5), 32767, "a loud sample was not clamped");
  assert.equal(at(6), -32768, "a quiet sample was not clamped");
}
{
  // chunks follow one another, and an empty recording is still a valid WAV
  const wav = micWav([new Float32Array([0.25]), new Float32Array([0.25])], 8000), v = new DataView(wav.buffer);
  assert.equal(wav.length, 48);
  assert.equal(v.getUint32(24, true), 8000, "the rate the context reports is not used");
  assert.equal(v.getUint32(40, true), 4);
  assert.equal(v.getInt16(44, true), v.getInt16(46, true), "the second chunk was dropped");
  const empty = micWav([], 16000), e = new DataView(empty.buffer);
  assert.equal(empty.length, 44);
  assert.equal(e.getUint32(40, true), 0);
}

// ---- the words land after what is already typed, never instead of it
assert.equal(micMerge("", "Hello there."), "Hello there.");
assert.equal(micMerge("Hi", "there"), "Hi there");
assert.equal(micMerge("Hi ", "there"), "Hi there");
assert.equal(micMerge("Hi\n", "there"), "Hi\nthere");
assert.equal(micMerge("Hi", "  there \n"), "Hi there");
assert.equal(micMerge("Hi", "   "), "Hi", "an empty transcript must not add a space");

// ---- the progress shown in the button while the first use downloads
assert.equal(micProgress({ state: "downloading", what: "speech engine", bytes: 50, total: 200 }).label,
  "Downloading the speech engine 25%");
assert.equal(micProgress({ state: "downloading", what: "speech model", bytes: 1, total: 4 }).label,
  "Downloading the speech model 25%");
assert.deepEqual(micProgress({ state: "downloading", what: "speech engine", bytes: 9, total: 0 }),
  { label: "Downloading the speech engine 0%", pct: 0, done: false, failed: false });
assert.equal(micProgress({ state: "installing" }).label, "Installing the speech engine");
assert.deepEqual(micProgress({ state: "done" }), { label: "Speech is ready", pct: 100, done: true, failed: false });
assert.equal(micProgress({ state: "error", error: "No space left." }).label, "No space left.");
assert.equal(micProgress({}).label, "Getting speech ready");

// ---- the button says what it does, and what it is doing
const idle = micTitle();
assert.match(idle, /Hold to talk/);
assert.match(idle, /click to start and stop/);
assert.match(helpers({ state: "recording" }).micTitle(), /Listening\. Click to stop/);
assert.match(helpers({ state: "sending" }).micTitle(), /Writing down/);
assert.match(helpers({ state: "setup", note: "Downloading the speech model 40%" }).micTitle(), /40%/);
assert.match(helpers({ state: "starting" }).micTitle(), /Starting the microphone/);

// ---- the page wires it up: a worklet, the transcribe route, setup, and the composer
for (const part of ["id=\"cwmicbtn\"", "aria-pressed=\"false\"", "AudioWorkletProcessor", "audioWorklet.addModule",
  "getUserMedia", "new Blob([micWav(chunks, rate)]", "/ui/speech/transcribe", "/ui/speech/setup",
  "/ui/speech/status", "t.value = micMerge(t.value, text)", ".cwmic[aria-pressed=\"true\"]"])
  assert.ok(html.includes(part), "index.html is missing " + part);
assert.match(html, /micFinish/, "nothing stops the recording");
assert.match(html, /MIC_MAX_S/, "a recording has no upper limit");

// ---- the real page: the button is in the composer, and a browser without a microphone says so
const { createRequire } = await import("node:module");
const require = createRequire(import.meta.url);
const { boot, closeAll } = require("./page.cjs");
{
  const { w, wait, problems } = await boot();
  const btn = w.document.getElementById("cwmicbtn");
  assert.ok(btn, "the composer has no mic button");
  assert.equal(btn.getAttribute("aria-pressed"), "false");
  assert.match(btn.title, /Hold to talk/);
  assert.ok(w.document.getElementById("cwform").contains(btn), "the mic is outside the composer");
  assert.equal(w.document.getElementById("cwinput").value, "", "the composer starts with text in it");

  // jsdom has no microphone: pressing it explains that and does not record or send anything
  btn.dispatchEvent(new w.Event("pointerdown", { bubbles: true }));
  await wait(50);
  assert.match(w.document.getElementById("toast").textContent, /can't record audio/);
  assert.equal(w.eval("MIC.state"), "idle", "the page thinks it is recording");
  assert.equal(w.document.getElementById("cwinput").value, "");
  assert.equal(problems.length, 0, "the page raised errors: " + problems.join(" | "));

  // the button shows listening, and goes back to its normal face
  w.eval('MIC.state = "recording"; micPaint()');
  assert.equal(btn.getAttribute("aria-pressed"), "true");
  assert.ok(btn.classList.contains("cwmic"));
  assert.match(btn.title, /Listening/);
  w.eval('MIC.state = "sending"; micPaint()');
  assert.equal(btn.getAttribute("aria-pressed"), "false");
  assert.ok(btn.classList.contains("busy"));
  w.eval('MIC.state = "idle"; micPaint()');
  assert.match(btn.title, /Hold to talk/);
  assert.ok(!btn.classList.contains("busy"));
  closeAll();
}

console.log("ok: the mic's WAV, the composer text, the first-use progress and the button");
