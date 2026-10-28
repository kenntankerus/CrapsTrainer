// Sounds, mixed in the browser: the stickman, the room, the crowd, the dice and chips.
// Everything runs through one mixer with a volume per part, and the room and crowd dip while the stickman talks.
// The chip clack, dice and the fallback room are made here, the way craps_game.py's make_clack makes its clack;
// recorded room and crowd sounds are used instead when the server has them (made by web/make_sounds.py).
// Phones only play sound after a tap, so unlock() runs on the first one.

let ctx = null, bus = null;
let clacks = [], dice = [], noise = null;
let ticket = 0;
let speaking = 0;
const clips = new Map();         // url -> compressed bytes (an ArrayBuffer), decoded when played
const missing = new Set();       // clips the server has no recording of
let misses = 0;
let prefetching = null;
const recorded = {};             // room / crowd sounds from the server: name -> AudioBuffer
let roomOn = false, roomNodes = null;

export const VOLUMES = { voice: 1, room: 0.35, crowd: 0.6, sfx: 0.8 };

export function unlock() {
  if (!ctx) {
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return;
    ctx = new AC();
    bus = {};
    for (const name of Object.keys(VOLUMES)) {
      bus[name] = ctx.createGain();
      bus[name].gain.value = VOLUMES[name];
      bus[name].connect(ctx.destination);
    }
    bus.duck = ctx.createGain();         // room and crowd pass through this; it dips under the stickman
    bus.duck.connect(ctx.destination);
    bus.room.disconnect(); bus.room.connect(bus.duck);
    bus.crowd.disconnect(); bus.crowd.connect(bus.duck);
    clacks = [0, 1, 2].map(makeClack);
    dice = [0, 1, 2].map(makeDice);
    noise = makeNoise(4);
    if (roomOn) startRoom();
  }
  if (ctx.state === "suspended") ctx.resume();
  if ("speechSynthesis" in window && !unlock.spoke) {
    unlock.spoke = true;
    speechSynthesis.speak(new SpeechSynthesisUtterance(""));
  }
}

export function setVolume(name, value) {
  VOLUMES[name] = value;
  if (bus) bus[name].gain.setTargetAtTime(value, ctx.currentTime, 0.05);
}

// ---------------------------------------------------------------- made here
function rng(seed) {
  let a = seed + 0x9e3779b9;
  return () => {
    a |= 0; a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function toBuffer(samples, level = 0.7) {
  const peak = samples.reduce((m, v) => Math.max(m, Math.abs(v)), 0) || 1;
  const buffer = ctx.createBuffer(1, samples.length, ctx.sampleRate);
  buffer.getChannelData(0).set(samples.map((v) => (v / peak) * level));
  return buffer;
}

// Knocks: each a few ringing tones that die away within milliseconds, plus a click of noise at contact
function knocks(samples, rand, hits, tones) {
  const rate = ctx.sampleRate, uniform = (a, b) => a + (b - a) * rand();
  for (const [start, level] of hits) {
    const modes = tones.map(([low, high, decay]) => [uniform(low, high), decay]);
    const begin = Math.floor(start * rate);
    for (let i = begin; i < Math.min(samples.length, begin + Math.floor(rate * 0.06)); i++) {
      const t = (i - begin) / rate;
      let value = 0;
      for (const [freq, decay] of modes) value += Math.sin(2 * Math.PI * freq * t) * Math.exp(-t / decay);
      samples[i] += level * (value + uniform(-1.5, 1.5) * Math.exp(-t / 0.0015));
    }
  }
}

// A clay chip landing on another: a sharp knock, then one or two smaller ones as it settles
function makeClack(seed) {
  const rand = rng(seed), uniform = (a, b) => a + (b - a) * rand();
  const samples = new Float32Array(Math.floor(ctx.sampleRate * 0.12));
  knocks(samples, rand, [[0, 1], [uniform(0.018, 0.03), 0.45], [uniform(0.045, 0.06), 0.18]],
    [[2300, 2700, 0.010], [3600, 4200, 0.006], [5400, 6300, 0.004]]);
  return toBuffer(samples);
}

// Two dice hitting the back wall and tumbling to a stop on felt: hard, high clicks that come closer together
function makeDice(seed) {
  const rand = rng(100 + seed), uniform = (a, b) => a + (b - a) * rand();
  const samples = new Float32Array(Math.floor(ctx.sampleRate * 1.1));
  const hits = [[0, 1], [uniform(0.004, 0.012), 0.9]];           // both dice into the wall
  let t = 0.08, gap = 0.11, level = 0.6;
  while (t < 1.0 && level > 0.05) {
    hits.push([t, level], [t + uniform(0.005, 0.03), level * uniform(0.5, 0.9)]);
    t += gap * uniform(0.7, 1.2);
    gap *= 0.82;
    level *= 0.78;
  }
  knocks(samples, rand, hits, [[3800, 4600, 0.004], [6200, 7400, 0.003], [1700, 2100, 0.006]]);
  return toBuffer(samples, 0.55);
}

function makeNoise(seconds) {
  // brown-ish noise: the low rumble of a busy room
  const samples = new Float32Array(Math.floor(ctx.sampleRate * seconds));
  let last = 0;
  for (let i = 0; i < samples.length; i++) {
    last = (last + 0.02 * (Math.random() * 2 - 1)) / 1.02;
    samples[i] = last;
  }
  return toBuffer(samples, 0.9);
}

function play(buffer, out, { gain = 1, rate = 1, pan = 0, at = 0 } = {}) {
  const source = ctx.createBufferSource();
  source.buffer = buffer;
  source.playbackRate.value = rate;
  let node = source;
  if (gain !== 1) { const g = ctx.createGain(); g.gain.value = gain; node.connect(g); node = g; }
  if (pan && ctx.createStereoPanner) { const p = ctx.createStereoPanner(); p.pan.value = pan; node.connect(p); node = p; }
  node.connect(out);
  source.start(ctx.currentTime + at);
  return source;
}

export function clack() {
  if (ctx) play(clacks[Math.floor(Math.random() * clacks.length)], bus.sfx);
}

export function diceRoll() {
  if (!ctx) return;
  if (recorded.dice) play(recorded.dice, bus.sfx);
  else play(dice[Math.floor(Math.random() * dice.length)], bus.sfx, { rate: 0.95 + Math.random() * 0.1 });
}

export function beep(ok = true) {
  if (!ctx) return;
  const osc = ctx.createOscillator(), gain = ctx.createGain();
  osc.frequency.value = ok ? 1000 : 400;
  gain.gain.value = 0.12;
  osc.connect(gain).connect(bus.sfx);
  osc.start();
  osc.stop(ctx.currentTime + 0.07);
}

// ---------------------------------------------------------------- voices in a crowd
// One made voice: a buzz at a speaking pitch, shaped into a vowel by two filters (the way a mouth shapes it),
// with a little vibrato. Cheers, groans and the room's murmur are many of these at once.
const VOWELS = { ah: [750, 1200], eh: [550, 1800], oh: [500, 850], ooh: [350, 750], yeah: [600, 1700] };

function voiceAt(out, { start, length, pitch, glide = 1, vowel = "ah", level = 0.1, attack = 0.1, pan = 0 }) {
  const osc = ctx.createOscillator();
  osc.type = "sawtooth";
  osc.frequency.setValueAtTime(pitch, start);
  osc.frequency.exponentialRampToValueAtTime(pitch * glide, start + length * 0.7);
  const wobble = ctx.createOscillator(), depth = ctx.createGain();
  wobble.frequency.value = 4.5 + Math.random() * 2;
  depth.gain.value = pitch * 0.015;
  wobble.connect(depth).connect(osc.frequency);
  const shaped = ctx.createGain();
  shaped.gain.value = 1;
  for (const formant of VOWELS[vowel]) {
    const f = ctx.createBiquadFilter();
    f.type = "bandpass";
    f.frequency.value = formant * (0.92 + Math.random() * 0.16);
    f.Q.value = 7;
    osc.connect(f).connect(shaped);
  }
  const soft = ctx.createBiquadFilter();
  soft.type = "lowpass";
  soft.frequency.value = 3000;
  const env = ctx.createGain();
  env.gain.setValueAtTime(0.0001, start);
  env.gain.exponentialRampToValueAtTime(level, start + attack);
  env.gain.setTargetAtTime(0.0001, start + length * 0.65, length * 0.18);
  let node = shaped.connect(soft).connect(env);
  if (ctx.createStereoPanner) {
    const p = ctx.createStereoPanner();
    p.pan.value = pan;
    node = node.connect(p);
  }
  node.connect(out);
  osc.start(start);
  wobble.start(start);
  osc.stop(start + length * 1.4);
  wobble.stop(start + length * 1.4);
}

const someone = () => (Math.random() < 0.6 ? 95 + Math.random() * 60 : 180 + Math.random() * 80);   // men and women

// ---------------------------------------------------------------- the room
// A low rumble, a murmur of people talking somewhere, and now and then chips clacking across the room
export function room(on) {
  roomOn = on;
  if (!ctx) return;
  if (on && !roomNodes) startRoom();
  if (!on && roomNodes) stopRoom();
}

function startRoom() {
  const nodes = { sources: [], timers: [] };
  roomNodes = nodes;
  if (recorded.room) {
    const s = play(recorded.room, bus.room);
    s.loop = true;
    nodes.sources.push(s);
    return;
  }
  const rumble = ctx.createBufferSource();
  rumble.buffer = noise;
  rumble.loop = true;
  const low = ctx.createBiquadFilter();
  low.type = "lowpass";
  low.frequency.value = 220;
  const g = ctx.createGain();
  g.gain.value = 0.6;
  rumble.connect(low).connect(g).connect(bus.room);
  rumble.start();
  nodes.sources.push(rumble);

  const murmur = ctx.createBiquadFilter();       // talk across the room loses its edges
  murmur.type = "lowpass";
  murmur.frequency.value = 1100;
  murmur.connect(bus.room);
  const talk = () => {                           // a phrase from someone: a few syllables on different vowels
    if (roomNodes !== nodes) return;
    const vowels = Object.keys(VOWELS), pitch = someone(), pan = Math.random() * 1.6 - 0.8;
    let at = ctx.currentTime + 0.05;
    for (let i = 0, n = 2 + Math.floor(Math.random() * 5); i < n; i++) {
      const length = 0.12 + Math.random() * 0.18;
      voiceAt(murmur, { start: at, length, pitch: pitch * (0.9 + Math.random() * 0.25), glide: 0.9 + Math.random() * 0.2,
        vowel: vowels[Math.floor(Math.random() * vowels.length)], level: 0.02 + Math.random() * 0.025, attack: 0.03, pan });
      at += length * (0.8 + Math.random() * 0.6);
    }
    nodes.timers.push(setTimeout(talk, 150 + Math.random() * 700));
  };
  for (let i = 0; i < 3; i++) nodes.timers.push(setTimeout(talk, i * 400));
  const distantChips = () => {
    if (roomNodes !== nodes) return;
    play(clacks[Math.floor(Math.random() * clacks.length)], bus.room,
      { gain: 0.08 + Math.random() * 0.1, rate: 0.85 + Math.random() * 0.3, pan: Math.random() * 1.6 - 0.8 });
    nodes.timers.push(setTimeout(distantChips, 1500 + Math.random() * 5000));
  };
  nodes.timers.push(setTimeout(distantChips, 2000));
}

function stopRoom() {
  for (const timer of roomNodes.timers) clearTimeout(timer);
  for (const s of roomNodes.sources) { try { s.stop(); } catch { /* already stopped */ } }
  roomNodes = null;
}

// ---------------------------------------------------------------- the crowd
// "cheer", "big_cheer", "groan" or "aww". Recorded sounds when the server has them, otherwise made ones.
const CROWDS = {
  cheer: { voices: 10, length: 1.4, glide: 1.45, vowel: ["yeah", "ah"], attack: 0.08, level: 0.07, claps: 18 },
  big_cheer: { voices: 18, length: 2.4, glide: 1.6, vowel: ["yeah", "ah", "ooh"], attack: 0.06, level: 0.06, claps: 45 },
  groan: { voices: 10, length: 1.5, glide: 0.72, vowel: ["oh", "ah"], attack: 0.25, level: 0.07, claps: 0 },
  aww: { voices: 6, length: 1.1, glide: 0.82, vowel: ["ah", "oh"], attack: 0.15, level: 0.06, claps: 0 },
};

export function react(kind) {
  if (!ctx || !CROWDS[kind]) return;
  if (recorded[kind]) { play(recorded[kind], bus.crowd); return; }
  const c = CROWDS[kind];
  for (let i = 0; i < c.voices; i++) {
    const start = ctx.currentTime + Math.random() * 0.3;
    voiceAt(bus.crowd, { start, length: c.length * (0.8 + Math.random() * 0.4), pitch: someone() * (c.glide > 1 ? 1.2 : 1),
      glide: c.glide * (0.92 + Math.random() * 0.16), vowel: c.vowel[i % c.vowel.length], level: c.level,
      attack: c.attack, pan: Math.random() * 1.4 - 0.7 });
  }
  for (let i = 0; i < c.claps; i++) {             // hands clapping: low, dull knocks scattered across the crowd
    play(clacks[i % clacks.length], bus.crowd, { gain: 0.1 + Math.random() * 0.12, rate: 0.3 + Math.random() * 0.12,
      pan: Math.random() * 1.6 - 0.8, at: 0.25 + Math.random() * c.length });
  }
}

// Recorded room and crowd sounds the server offers, fetched once
export async function loadRecorded(names) {
  for (const name of names || []) {
    if (recorded[name] || !ctx) continue;
    try {
      const bytes = await (await fetch(`/api/sounds/${name}`)).arrayBuffer();
      recorded[name] = await ctx.decodeAudioData(bytes);
      if (name === "room" && roomNodes) { stopRoom(); startRoom(); }
    } catch { /* keep the made one */ }
  }
}

// ---------------------------------------------------------------- the stickman
function duck(on) {
  if (!bus) return;
  speaking += on ? 1 : -1;
  bus.duck.gain.setTargetAtTime(speaking > 0 ? 0.35 : 1, ctx.currentTime, on ? 0.05 : 0.4);
}

async function fetchClip(url) {
  if (clips.has(url)) return clips.get(url);
  if (missing.has(url)) return null;
  const response = await fetch(url, { credentials: "same-origin" });
  if (!response.ok) {
    missing.add(url);
    return null;
  }
  const bytes = await response.arrayBuffer();
  clips.set(url, bytes);
  return bytes;
}

// Fetch every clip of a voice ahead of time, a few at once, so calls play the moment the dice land
export function prefetch(urls) {
  const mine = prefetching = {};
  const queue = [...urls];
  const worker = async () => {
    while (queue.length && prefetching === mine) {
      try { await fetchClip(queue.shift()); } catch { /* try again when it's needed */ }
    }
  };
  for (let i = 0; i < 4; i++) worker();
}

function speak(text, mood) {
  return new Promise((resolve) => {
    if (!("speechSynthesis" in window)) return resolve();
    const u = new SpeechSynthesisUtterance(text);
    const [rate, pitch] = { hype: [1.14, 1.2], cheer: [1.08, 1.1], calm: [1.04, 1], down: [0.96, 0.85] }[mood] || [1, 1];
    u.rate = rate; u.pitch = pitch; u.lang = "en-US";
    u.volume = VOLUMES.voice;
    u.onend = u.onerror = () => resolve();
    speechSynthesis.speak(u);
  });
}

async function playBytes(bytes, mine) {
  const buffer = await ctx.decodeAudioData(bytes.slice(0));
  if (mine !== ticket) return;
  await new Promise((resolve) => {
    const source = play(buffer, bus.voice);
    say.current = source;
    source.onended = resolve;
  });
}

// Say the stickman's pieces one after another. A newer call cuts this one off.
export async function say(pieces) {
  const mine = ++ticket;
  try { say.current?.stop(); } catch { /* already done */ }
  if ("speechSynthesis" in window) speechSynthesis.cancel();
  for (const piece of pieces || []) { if (piece.url && !clips.has(piece.url)) fetchClip(piece.url).catch(() => {}); }
  duck(true);
  try {
    for (const piece of pieces || []) {
      if (mine !== ticket) return;
      let bytes = null;
      if (ctx && piece.url && misses < 4) {
        try {
          bytes = await Promise.race([fetchClip(piece.url), new Promise((r) => setTimeout(() => r(null), piece.dynamic ? 6000 : 3000))]);
        } catch { bytes = null; }
        misses = bytes ? 0 : misses + 1;       // after a few misses in a row the server has no voice: stop asking
      }
      if (mine !== ticket) return;
      if (bytes) {
        try { await playBytes(bytes, mine); continue; } catch { /* not audio after all */ }
      }
      await speak(piece.spoken, piece.mood);
    }
  } finally {
    duck(false);
  }
}
