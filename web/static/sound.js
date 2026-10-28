// Sounds: the chip clack (made here, as craps_game.py's make_clack does), the camera beep, and the stickman.
// Phones only play sound after a tap, so unlock() runs on the first one.

let ctx = null;
let clacks = [];
let player = null;            // one <audio> element, reused: iOS lets it keep playing once a tap has started it
let ticket = 0;
const missing = new Set();    // clips the server has no recording of
let misses = 0;

export function unlock() {
  if (!ctx) {
    const AC = window.AudioContext || window.webkitAudioContext;
    if (AC) {
      ctx = new AC();
      clacks = [0, 1, 2].map(makeClack);
    }
  }
  if (ctx && ctx.state === "suspended") ctx.resume();
  if (!player) {
    player = new Audio();
    player.preload = "auto";
    player.src = "data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAIA+AAACABAAZGF0YQAAAAA=";
    player.play().catch(() => {});
    if ("speechSynthesis" in window) speechSynthesis.speak(new SpeechSynthesisUtterance(""));
  }
}

function rng(seed) {
  let a = seed + 0x9e3779b9;
  return () => {
    a |= 0; a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// A clay chip landing on another: a sharp knock, then one or two smaller ones as it settles
function makeClack(seed) {
  const rand = rng(seed), uniform = (a, b) => a + (b - a) * rand();
  const rate = ctx.sampleRate, samples = new Float32Array(Math.floor(rate * 0.12));
  for (const [start, level] of [[0, 1], [uniform(0.018, 0.03), 0.45], [uniform(0.045, 0.06), 0.18]]) {
    const modes = [[uniform(2300, 2700), 0.010], [uniform(3600, 4200), 0.006], [uniform(5400, 6300), 0.004]];
    const begin = Math.floor(start * rate);
    for (let i = begin; i < Math.min(samples.length, begin + Math.floor(rate * 0.06)); i++) {
      const t = (i - begin) / rate;
      let value = 0;
      for (const [freq, decay] of modes) value += Math.sin(2 * Math.PI * freq * t) * Math.exp(-t / decay);
      samples[i] += level * (value + uniform(-1.5, 1.5) * Math.exp(-t / 0.0015));
    }
  }
  const peak = samples.reduce((m, v) => Math.max(m, Math.abs(v)), 0) || 1;
  const buffer = ctx.createBuffer(1, samples.length, rate);
  buffer.getChannelData(0).set(samples.map((v) => (v / peak) * 0.7));
  return buffer;
}

export function clack() {
  if (!ctx || !clacks.length) return;
  const source = ctx.createBufferSource();
  source.buffer = clacks[Math.floor(Math.random() * clacks.length)];
  source.connect(ctx.destination);
  source.start();
}

export function beep(ok = true) {
  if (!ctx) return;
  const osc = ctx.createOscillator(), gain = ctx.createGain();
  osc.frequency.value = ok ? 1000 : 400;
  gain.gain.value = 0.12;
  osc.connect(gain).connect(ctx.destination);
  osc.start();
  osc.stop(ctx.currentTime + 0.07);
}

// How each mood is spoken by the browser's own voice: (rate, pitch), from stickman.MOODS
const MOODS = { hype: [1.14, 1.2], cheer: [1.08, 1.1], calm: [1.04, 1], down: [0.96, 0.85] };

function speak(text, mood) {
  return new Promise((resolve) => {
    if (!("speechSynthesis" in window)) return resolve();
    const u = new SpeechSynthesisUtterance(text);
    const [rate, pitch] = MOODS[mood] || [1, 1];
    u.rate = rate; u.pitch = pitch; u.lang = "en-US";
    u.onend = u.onerror = () => resolve();
    speechSynthesis.speak(u);
  });
}

function playClip(url) {
  return new Promise((resolve, reject) => {
    const done = (ok) => { player.onended = player.onerror = null; ok ? resolve() : reject(); };
    player.onended = () => done(true);
    player.onerror = () => done(false);
    player.src = url;
    player.play().catch(() => done(false));
  });
}

// Say the stickman's pieces one after another. A newer call cuts this one off.
export async function say(pieces, slug) {
  const mine = ++ticket;
  if (player) player.pause();
  if ("speechSynthesis" in window) speechSynthesis.cancel();
  for (const piece of pieces || []) {
    if (mine !== ticket) return;
    const url = `/api/voice/${slug}/${piece.clip}.mp3`;
    if (player && misses < 4 && !missing.has(url)) {
      try {
        await playClip(url);
        misses = 0;
        continue;
      } catch {
        missing.add(url);
        misses += 1;          // after a few in a row, the server has no voice: stop asking
      }
    }
    if (mine !== ticket) return;
    await speak(piece.spoken, piece.mood);
  }
}
