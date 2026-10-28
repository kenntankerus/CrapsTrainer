// Craps Trainer in the browser. The server keeps the table and the throw log; this page shows them and sends
// what you do: bets, the dice, fixes and take-backs. Works with a mouse and keyboard or with touch.

import { chipColours, chipLabel, dieSvg, drawLayout, money } from "./layout.js";
import * as sound from "./sound.js";

const $ = (id) => document.getElementById(id);
const S = {
  config: null, me: null, settings: {}, player: null, tab: "table", table: null, tracker: null,
  dice: [], flash: {}, hover: null, moving: null, layout: "wide", busy: false,
};
let flashTimer = null, saveTimer = null, pendingSettings = {};
// this page's name, so it can tell its own changes from another device's when the news comes back
const CLIENT = (crypto.randomUUID ? crypto.randomUUID() : String(Math.random()).slice(2)).slice(0, 36);

// ---------------------------------------------------------------- talking to the server
class ApiError extends Error {
  constructor(message, status) { super(message); this.status = status; }
}

async function api(path, body) {
  const options = { headers: { "X-Requested-With": "craps", "X-Client-Id": CLIENT }, credentials: "same-origin" };
  if (body !== undefined) {
    options.method = "POST";
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch(path, options);
  } catch {
    throw new ApiError("Can't reach the server. Check the connection and try again.", 0);
  }
  const data = await response.json().catch(() => ({}));
  if (response.status === 401 && !path.startsWith("/api/auth/")) {
    showGate("signin");
    throw new ApiError("Sign in first.", 401);
  }
  if (!response.ok) throw new ApiError(data.error || `Something went wrong (${response.status}).`, response.status);
  return data;
}

function saveSettings(values) {
  Object.assign(S.settings, values);
  Object.assign(pendingSettings, values);
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => {
    const settings = pendingSettings;
    pendingSettings = {};
    api("/api/me", { settings }).catch(() => {});
  }, 400);
}

function status(text, error = false) {
  const node = $("status");
  node.textContent = text || "";
  node.classList.toggle("error", !!error);
}

function localTime() {
  const d = new Date(), p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

function clear(node) { node.replaceChildren(); return node; }

function item(parent, tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  parent.appendChild(node);
  return node;
}

function option(select, value, label = value) {
  const o = document.createElement("option");
  o.value = value;
  o.textContent = label;
  select.appendChild(o);
  return o;
}

function signed(cents) { return (cents > 0 ? "+" : "") + money(cents); }

// ---------------------------------------------------------------- dialogs
function ask(title, text, { input = false, value = "", placeholder = "", ok = "OK", danger = false, type = "text" } = {}) {
  return new Promise((resolve) => {
    const dialog = $("ask");
    $("ask-title").textContent = title;
    $("ask-text").textContent = text || "";
    const field = $("ask-input");
    field.hidden = !input;
    field.value = value;
    field.placeholder = placeholder;
    field.type = type;
    field.maxLength = type === "email" ? 254 : 40;
    field.required = input;
    $("ask-ok").textContent = ok;
    $("ask-ok").className = danger ? "danger" : "gold";
    dialog.onclose = () => resolve(dialog.returnValue === "ok" ? (input ? field.value.trim() : true) : null);
    dialog.returnValue = "";
    dialog.showModal();
    if (input) field.focus();
  });
}

// ---------------------------------------------------------------- signing in
function showGate(name) {
  for (const id of ["signin", "verify", "naming"]) $(id).hidden = id !== name;
  $("main").hidden = !!name;
  $("tabs").hidden = $("who").hidden = !!name;
  if (name === "signin") loadTurnstile();
}

let turnstileToken = "";
function loadTurnstile() {
  if (!S.config?.turnstile || window.onTurnstileLoad) return;
  window.onTurnstileLoad = () => window.turnstile.render("#turnstile-box", {
    sitekey: S.config.turnstile, theme: "dark",
    callback: (token) => { turnstileToken = token; },
    "expired-callback": () => { turnstileToken = ""; },
  });
  const script = document.createElement("script");
  script.src = "https://challenges.cloudflare.com/turnstile/v0/api.js?onload=onTurnstileLoad&render=explicit";
  script.async = true;
  document.head.appendChild(script);
}

function setupSignIn() {
  $("signin-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const node = $("signin-status");
    node.classList.remove("error");
    node.textContent = "Sending…";
    try {
      const result = await api("/api/auth/request", { email: $("email").value, turnstile: turnstileToken });
      node.textContent = result.message + " You can close this tab.";
    } catch (error) {
      node.textContent = error.message;
      node.classList.add("error");
    }
    if (window.turnstile) { window.turnstile.reset(); turnstileToken = ""; }
  });
}

async function verifyLink() {
  showGate("verify");
  const token = new URLSearchParams(location.search).get("token") || "";
  const fail = (message) => {
    $("verify-status").textContent = message;
    $("verify-status").classList.add("error");
    $("verify-back").hidden = false;
  };
  let info;
  try {
    info = await api(`/api/auth/link?token=${encodeURIComponent(token)}`);
  } catch (error) {
    return fail(error.message);
  }
  if (info.purpose === "claim" && info.guest) {
    $("verify-title").textContent = `${info.guest}'s throws`;
    $("verify-text").textContent = `${info.host || "Your host"} kept your throws as the guest "${info.guest}". ` +
      `Sign in as ${info.email} to move them into your own account.`;
    $("verify-button").textContent = "Take my history and sign in";
  } else {
    $("verify-text").textContent = `Sign in to ${S.config.site} as ${info.email} on this device.`;
  }
  $("verify-button").hidden = false;
  $("verify-button").onclick = async () => {
    $("verify-button").disabled = true;
    try {
      const result = await api("/api/auth/verify", { token });
      history.replaceState(null, "", "/");
      await boot(result.claimed ? `${result.claimed}'s throws are in your account now.` : "");
    } catch (error) {
      $("verify-button").disabled = false;
      fail(error.message);
    }
  };
}

// ---------------------------------------------------------------- starting up
async function boot(message = "") {
  try {
    S.me = await api("/api/me");
  } catch (error) {
    if (error.status !== 401) {
      showGate("signin");
      $("signin-status").textContent = error.message;
    }
    return;
  }
  if (!S.me.name) {
    showGate("naming");
    $("your-name").focus();
    return;
  }
  S.settings = S.me.settings;
  showGate(null);
  fillControls();
  setTab(S.settings.tab === "tracker" ? "tracker" : "table", false);
  status(message);
  S.camera = S.me.camera;
  drawCamera();
  connectLive();
  await refresh();
}

function setupNaming() {
  $("naming-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      await api("/api/me", { name: $("your-name").value });
      await boot();
    } catch (error) {
      $("naming-status").textContent = error.message;
    }
  });
}

// ---------------------------------------------------------------- the controls along the top
const OTHER = "__other__";

function fillControls() {
  const s = S.settings, me = S.me;
  const players = clear($("player"));
  for (const p of me.players) option(players, p.id, p.is_self ? `${p.name} (you)` : p.name);
  option(players, "__manage__", "Players…");
  S.player = s.player_id;
  players.value = String(S.player);

  const sets = [...me.sets];
  const current = (s.dice_set || sets[0]);
  if (!sets.includes(current)) sets.push(current);
  fillPicker($("dice-set"), sets, current);
  const setup = clear($("setup"));
  option(setup, "", "Up-facing…");
  for (const v of S.config.setups) option(setup, v);
  setup.value = s.setup || "";
  showSetup();

  const positions = [...me.positions];
  const position = s.position || positions[0];
  if (!positions.includes(position)) positions.push(position);
  fillPicker($("position"), positions, position);

  const voice = clear($("voice"));
  option(voice, "Off");
  for (const v of S.config.voices) option(voice, v.name);
  voice.value = s.voice || S.config.default_voice;
  $("back-wall").checked = s.back_wall !== false;
  $("sounds").checked = s.sounds !== false;

  const hop = clear($("hop"));
  for (const h of S.config.hops) option(hop, h);
  hop.value = S.config.hops.includes(s.hop) ? s.hop : "2-5";
  const buyIn = clear($("buy-in"));
  for (const b of S.config.buy_ins) option(buyIn, b.cents, b.label);
  const chosen = s.buy_in || 10000;
  if (![...buyIn.options].some((o) => Number(o.value) === chosen)) option(buyIn, chosen, money(chosen));
  option(buyIn, OTHER, "Other…");
  buyIn.value = String(chosen);

  const compare = clear($("compare"));
  for (const m of S.config.modes) option(compare, m);
  compare.value = s.compare || S.config.modes[0];
  $("menu-email").textContent = `Signed in as ${me.email}`;
  drawRack();
  drawPad();
}

function fillPicker(select, values, current) {
  clear(select);
  for (const v of values) option(select, v);
  option(select, OTHER, "Other…");
  select.value = current;
  select.dataset.last = current;
}

async function pickOther(select, title, key) {
  if (select.value !== OTHER) {
    select.dataset.last = select.value;
    saveSettings({ [key]: select.value });
    return true;
  }
  const name = await ask(title, "", { input: true, placeholder: "Name" });
  if (!name) {
    select.value = select.dataset.last;
    return false;
  }
  if (![...select.options].some((o) => o.value === name)) {
    const o = document.createElement("option");
    o.value = o.textContent = name;
    select.insertBefore(o, select.lastElementChild);
  }
  select.value = select.dataset.last = name;
  saveSettings({ [key]: name });
  return true;
}

function showSetup() {
  $("setup").hidden = $("dice-set").value !== "Hardway";
}

function currentSet() {
  const name = $("dice-set").value === OTHER ? $("dice-set").dataset.last : $("dice-set").value;
  const setup = $("setup").value;
  return name === "Hardway" && setup ? `${name} ${setup}` : (name || "Unnamed");
}

function currentPosition() {
  return $("position").value === OTHER ? $("position").dataset.last : $("position").value;
}

function voiceSlug() {
  const v = S.config.voices.find((x) => x.name === $("voice").value);
  return v ? v.slug : "";
}

function setupControls() {
  $("player").addEventListener("change", async () => {
    if ($("player").value === "__manage__") {
      $("player").value = String(S.player);
      openPlayers();
      return;
    }
    S.player = Number($("player").value);
    S.flash = {};
    saveSettings({ player_id: S.player });
    syncCamera();
    const name = $("player").selectedOptions[0].textContent;
    status(`${name} is throwing.`);
    await refresh();
  });
  $("dice-set").addEventListener("change", async () => {
    await pickOther($("dice-set"), "New dice set", "dice_set");
    showSetup();
    syncCamera();
    if (S.tab === "tracker") refresh();
  });
  $("setup").addEventListener("change", () => { saveSettings({ setup: $("setup").value }); syncCamera(); if (S.tab === "tracker") refresh(); });
  $("position").addEventListener("change", async () => {
    await pickOther($("position"), "New position", "position");
    syncCamera();
    if (S.tab === "tracker") refresh();
  });
  $("voice").addEventListener("change", () => { saveSettings({ voice: $("voice").value }); setTimeout(syncCamera, 500); });
  $("back-wall").addEventListener("change", () => { saveSettings({ back_wall: $("back-wall").checked }); syncCamera(); });
  $("sounds").addEventListener("change", () => saveSettings({ sounds: $("sounds").checked }));
  $("compare").addEventListener("change", () => { saveSettings({ compare: $("compare").value }); refresh(); });
  $("hop").addEventListener("change", () => { saveSettings({ hop: $("hop").value }); drawTable(); });
  $("buy-in").addEventListener("change", async () => {
    const select = $("buy-in");
    if (select.value !== OTHER) { saveSettings({ buy_in: Number(select.value) }); return; }
    const typed = await ask("Buy-in", "Whole dollars, like 300.", { input: true, placeholder: "300" });
    const dollars = parseInt((typed || "").replace(/[^0-9]/g, ""), 10);
    if (!dollars) { select.value = String(S.settings.buy_in || 10000); return; }
    const cents = dollars * 100;
    if (![...select.options].some((o) => Number(o.value) === cents)) {
      const o = document.createElement("option");
      o.value = cents;
      o.textContent = money(cents);
      select.insertBefore(o, select.lastElementChild);
    }
    select.value = String(cents);
    saveSettings({ buy_in: cents });
  });
  for (const button of $("tabs").querySelectorAll("button")) {
    button.addEventListener("click", () => { setTab(button.dataset.tab); syncCamera(); refresh(); });
  }
}

function setTab(tab, save = true) {
  S.tab = tab;
  for (const button of $("tabs").querySelectorAll("button")) button.setAttribute("aria-pressed", String(button.dataset.tab === tab));
  $("tab-table").hidden = tab !== "table";
  $("tab-tracker").hidden = tab !== "tracker";
  $("roll-button").textContent = tab === "table" ? "Roll" : "Log throw";
  if (save) saveSettings({ tab });
}

// ---------------------------------------------------------------- the dice pad
function drawPad() {
  const pad = clear($("pad"));
  for (let v = 1; v <= 6; v++) {
    const b = item(pad, "button");
    b.type = "button";
    b.innerHTML = dieSvg(v);
    b.setAttribute("aria-label", String(v));
    b.addEventListener("click", () => pickDie(v));
  }
  drawSlots();
}

function drawSlots() {
  for (const i of [0, 1]) {
    const slot = $(`slot-${i}`);
    slot.innerHTML = S.dice[i] ? dieSvg(S.dice[i]) : "";
    slot.classList.toggle("filled", !!S.dice[i]);
    slot.classList.toggle("next", S.dice.length === i);
  }
  $("roll-button").disabled = S.dice.length !== 2 || S.busy;
}

function pickDie(value) {
  if (S.dice.length >= 2) S.dice = [];
  S.dice.push(value);
  drawSlots();
}

function setupEntry() {
  $("slot-0").addEventListener("click", () => { S.dice = []; drawSlots(); });
  $("slot-1").addEventListener("click", () => { S.dice = S.dice.slice(0, 1); drawSlots(); });
  $("roll-button").addEventListener("click", rollDice);
  $("fix-button").addEventListener("click", fixLast);
  $("undo-button").addEventListener("click", undo);
  document.addEventListener("keydown", (event) => {
    if (!$("main").hidden && !document.querySelector("dialog[open]")) {
      const typing = ["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement?.tagName);
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "z" && !typing) {
        event.preventDefault();
        undo();
      } else if (event.key === "F2") {
        event.preventDefault();
        fixLast();
      } else if (!typing && /^[1-6]$/.test(event.key) && !event.ctrlKey && !event.metaKey && !event.altKey) {
        pickDie(Number(event.key));
      } else if (!typing && event.key === "Enter" && S.dice.length === 2 && document.activeElement?.tagName !== "BUTTON") {
        event.preventDefault();
        rollDice();
      } else if (!typing && (event.key === "Backspace" || event.key === "Escape")) {
        if (event.key === "Escape" && S.moving) { S.moving = null; drawTable(); status(""); }
        S.dice = event.key === "Escape" ? [] : S.dice.slice(0, -1);
        drawSlots();
      }
    }
  });
}

async function busy(job) {
  if (S.busy) return;
  S.busy = true;
  drawSlots();
  try {
    await job();
  } catch (error) {
    if (error.status !== 401) {
      status(error.message, true);
      if ($("sounds").checked) sound.beep(false);
    }
  } finally {
    S.busy = false;
    drawSlots();
  }
}

function rollDice() {
  if (S.dice.length !== 2) {
    status("Tap two dice first: left die, then right die.");
    return;
  }
  sound.unlock();
  const [die1, die2] = S.dice;
  return busy(async () => {
    const result = await api(`/api/players/${S.player}/roll`, {
      die1, die2, back_wall: $("back-wall").checked, dice_set: currentSet(), position: currentPosition(),
      local_time: localTime(), mode: S.tab === "table" ? "table" : "tracker",
    });
    S.dice = [];
    speak(result);
    status(result.message || "");
    await refresh(true);
  });
}

function fixLast() {
  if (S.dice.length !== 2) {
    status("To fix the last throw, tap the two right dice (left die first), then Fix last.");
    return;
  }
  sound.unlock();
  const [die1, die2] = S.dice;
  return busy(async () => {
    const result = await api(`/api/players/${S.player}/fix`, { die1, die2 });
    S.dice = [];
    speak(result);
    status(result.message);
    await refresh(true);
  });
}

function undo() {
  return busy(async () => {
    const result = await api(`/api/players/${S.player}/undo`, {});
    S.flash = {};
    status(result.message);
    await refresh();
  });
}

function speak(result) {
  if (result.call?.length && $("voice").value !== "Off") sound.say(result.call, voiceSlug());
}

// ---------------------------------------------------------------- loading what's on screen
async function refresh(afterRoll = false) {
  if (!S.player) return;
  try {
    if (S.tab === "table") {
      S.table = await api(`/api/players/${S.player}/table`);
      if (afterRoll) flashRoll();
      drawTable();
    } else {
      const q = new URLSearchParams({ mode: $("compare").value, dice_set: currentSet(), position: currentPosition() });
      S.tracker = await api(`/api/players/${S.player}/tracker?${q}`);
      drawTracker();
    }
    $("undo-button").disabled = S.tab === "table" ? !S.table?.can_undo : !S.tracker?.recent.length;
  } catch (error) {
    if (error.status === 404) {          // the player was removed on another device
      S.me = await api("/api/me");
      S.settings = S.me.settings;
      fillControls();
      return refresh();
    }
    if (error.status !== 401) status(error.message, true);
  }
}

function flashRoll() {
  S.flash = {};
  for (const e of S.table.last_roll?.events || []) {
    if (e.net) S.flash[e.zone] = e.net > 0 ? "win" : "loss";
  }
  clearTimeout(flashTimer);
  flashTimer = setTimeout(() => { S.flash = {}; drawTable(); }, 4000);
}

// ---------------------------------------------------------------- the craps table
function drawRack() {
  const rack = clear($("rack"));
  const chosen = S.config.chips.includes(S.settings.chip) ? S.settings.chip : S.config.chips[1];
  S.settings.chip = chosen;
  for (const cents of S.config.chips) {
    const b = item(rack, "button", chipLabel(cents), "chip");
    const { fill, text } = chipColours(cents);
    b.type = "button";
    b.style.background = fill;
    b.style.color = text;
    b.setAttribute("aria-pressed", String(cents === chosen));
    b.setAttribute("aria-label", `${money(cents)} chip`);
    b.addEventListener("click", () => { saveSettings({ chip: cents }); drawRack(); });
  }
}

function drawTable() {
  const t = S.table;
  if (!t) return;
  drawLayout($("layout"), S.layout, t, { flash: S.flash, hover: S.hover, moving: S.moving, hop: $("hop").value });
  $("money").textContent = `Rack ${money(t.bankroll)}   ·   On the table ${money(t.on_table)}   ·   ` +
    `${t.up >= 0 ? "Up" : "Down"} ${money(Math.abs(t.up))}`;
  $("comeout").checked = t.work_on_comeout;
  $("layout-help").textContent = S.layout === "wide"
    ? "Click a spot to bet the chosen chip. Right-click to take a bet down, or drag it to another spot (or onto the chips to take it down)."
    : "Tap a spot to bet the chosen chip. Press and hold a bet to take it down or move it.";

  const last = clear($("last-roll"));
  const r = t.last_roll;
  if (r) {
    last.insertAdjacentHTML("beforeend", dieSvg(r.dice[0]) + dieSvg(r.dice[1]));
    item(last, "span", r.no_roll ? `${r.dice[0]}-${r.dice[1]}  ·  NO ROLL` : `${r.dice[0]}-${r.dice[1]} = ${r.total}`, "total");
  } else {
    last.insertAdjacentHTML("beforeend", dieSvg(0) + dieSvg(0));
    item(last, "span", "No rolls yet", "muted");
  }
  $("last-state").textContent = r?.state || "";
  const payouts = clear($("payouts"));
  if (r && r.events?.length) {
    for (const e of r.events) {
      const li = item(payouts, "li");
      item(li, "span", e.name, "grow");
      item(li, "span", e.note);
      item(li, "span", e.net ? signed(e.net) : "", e.net > 0 ? "win" : e.net < 0 ? "loss" : "");
    }
    const li = item(payouts, "li");
    item(li, "span", "This roll", "grow");
    item(li, "span", r.net ? signed(r.net) : "$0", r.net > 0 ? "win" : r.net < 0 ? "loss" : "");
  } else if (r && !r.no_roll && r.state) {
    item(payouts, "li", "No action on your bets.");
  }
  const rolls = clear($("rolls"));
  for (const roll of t.rolls) {
    const li = item(rolls, "li");
    item(li, "span", roll.time);
    item(li, "span", `${roll.dice[0]}-${roll.dice[1]}${roll.back_wall ? ` = ${roll.total}` : ""}`);
    item(li, "span", roll.back_wall ? (roll.game ? roll.note : "logged") : "no roll", "grow");
    item(li, "span", roll.net ? signed(roll.net) : "", roll.net > 0 ? "win" : roll.net < 0 ? "loss" : "");
  }
  if (!t.rolls.length) item(rolls, "li", "Nothing rolled yet.");
  if ($("stats-dialog").open) drawRunStats();
}

function drawRunStats() {
  const t = S.table;
  const box = clear($("run-summary"));
  for (const [section, lines] of t.summary) {
    item(box, "h3", section);
    const dl = item(box, "dl", undefined, "summary");
    for (const [caption, value] of lines) {
      item(dl, "dt", caption);
      item(dl, "dd", value);
    }
  }
  const table = clear($("by-bet"));
  const head = item(table, "tr");
  for (const h of ["Bet", "Won", "Lost", "Net"]) item(head, "th", h);
  for (const row of t.by_bet) {
    const tr = item(table, "tr");
    for (const v of [row.bet, row.won, row.lost, row.net]) item(tr, "td", String(v));
  }
  if (!t.by_bet.length) item(item(table, "tr"), "td", "No bets settled yet.");
}

async function change(action, args = {}) {
  sound.unlock();
  try {
    const result = await api(`/api/players/${S.player}/change`, { action, hop: $("hop").value, ...args });
    S.table = result.table;
    S.flash = {};
    status(result.message);
    if (["bet", "move"].includes(action) && $("sounds").checked) sound.clack();
    drawTable();
    $("undo-button").disabled = !S.table.can_undo;
    return true;
  } catch (error) {
    if (error.status !== 401) {
      status(error.message, true);
      if ($("sounds").checked) sound.beep(false);
    }
    drawTable();
    return false;
  }
}

function betOn(zone) {
  if (zone === "hop" || !zone.startsWith("hop_")) change("bet", { zone, cents: S.settings.chip });
}

function betInZone(zone) {
  // what taking down this zone would lift: the odds on it first, then the bet itself
  const bets = S.table?.bets || [];
  const base = { pass_odds: "pass", dp_odds: "dont_pass" }[zone] || ((zone.startsWith("come_") || zone.startsWith("dc_")) ? zone : null);
  const find = (spot) => bets.find((b) => b.spot === spot);
  if (base && find(base)?.odds) return { spot: base, odds: true, cents: find(base).odds, name: find(base).name };
  const spot = base || (zone === "hop" ? `hop_${$("hop").value.replace("-", "_")}` : zone);
  const bet = find(spot);
  return bet ? { spot, odds: false, cents: bet.amount + bet.odds, name: bet.name } : null;
}

// The finger lifting after a press-and-hold sends a click to whatever is under it: by then, the open sheet
let sheetOpened = 0;
function guardSheet() {
  $("sheet").addEventListener("click", (event) => {
    if (performance.now() - sheetOpened < 450) { event.preventDefault(); event.stopPropagation(); }
  }, true);
}

function openSheet(zone) {
  const target = betInZone(zone);
  if (!target) {
    betOn(zone);
    return;
  }
  $("sheet-title").textContent = target.odds ? `Odds on ${target.name}: ${money(target.cents)}` : `${target.name}: ${money(target.cents)}`;
  const actions = clear($("sheet-actions"));
  const add = (label, run, cls) => {
    const b = item(actions, "button", label, cls);
    b.type = "button";
    b.addEventListener("click", () => { $("sheet").close(); run(); });
  };
  add(`Add a ${money(S.settings.chip)} chip`, () => betOn(zone), "gold");
  add(target.odds ? "Take the odds down" : "Take it down", () => change("take_down", { zone }));
  if (!target.odds) add("Move it…", () => {
    S.moving = zone;
    status(`Tap the spot to move ${target.name} to. Tap it again to cancel.`);
    drawTable();
  });
  sheetOpened = performance.now();
  $("sheet").showModal();
}

// Mouse: click bets, right-click takes down, drag moves (or drops on the rack to take down).
// Touch: tap bets, press and hold opens the bet's actions, and a swipe scrolls the page as usual.
function setupLayout() {
  const svg = $("layout");
  let press = null;
  const zoneAt = (x, y) => document.elementFromPoint(x, y)?.closest?.("[data-zone]")?.dataset.zone || null;
  const overRack = (x, y) => !!document.elementFromPoint(x, y)?.closest?.("#rack");
  const ghost = $("ghost");

  svg.addEventListener("contextmenu", (event) => event.preventDefault());
  svg.addEventListener("pointerdown", (event) => {
    sound.unlock();
    const zone = event.target.closest("[data-zone]")?.dataset.zone;
    if (!zone || !S.table) return;
    if (event.pointerType === "mouse" && event.button === 2) {
      if (betInZone(zone)) change("take_down", { zone });
      return;
    }
    if (event.button !== 0) return;
    press = { zone, x: event.clientX, y: event.clientY, type: event.pointerType, long: false, drag: false };
    if (event.pointerType !== "mouse") {
      press.timer = setTimeout(() => {
        if (!press) return;
        press.long = true;
        navigator.vibrate?.(15);
        openSheet(press.zone);
      }, 500);
    }
  });
  svg.addEventListener("pointermove", (event) => {
    if (!press) return;
    const moved = Math.abs(event.clientX - press.x) + Math.abs(event.clientY - press.y);
    if (moved > 8 && !press.drag) {
      clearTimeout(press.timer);
      const target = press.type === "mouse" ? betInZone(press.zone) : null;
      if (!target || target.odds) {
        if (press.type !== "mouse") press = null;     // a swipe: let the page scroll
        return;
      }
      press.drag = true;
      svg.setPointerCapture(event.pointerId);
      const { fill, text } = chipColours(target.cents);
      ghost.textContent = chipLabel(target.cents);
      ghost.style.background = fill;
      ghost.style.color = text;
      ghost.hidden = false;
    }
    if (press?.drag) {
      ghost.style.left = `${event.clientX}px`;
      ghost.style.top = `${event.clientY}px`;
      const over = zoneAt(event.clientX, event.clientY);
      $("rack").classList.toggle("drop", overRack(event.clientX, event.clientY));
      const hover = over === press.zone ? null : over;
      if (hover !== S.hover) { S.hover = hover; drawTable(); }
    }
  });
  const finish = (event) => {
    if (!press) return;
    clearTimeout(press.timer);
    const p = press;
    press = null;
    if (event.type === "pointercancel" || p.long) return;
    if (p.drag) {
      ghost.hidden = true;
      $("rack").classList.remove("drop");
      S.hover = null;
      const to = zoneAt(event.clientX, event.clientY);
      if (overRack(event.clientX, event.clientY)) change("take_down", { zone: p.zone });
      else if (to && to !== p.zone) change("move", { zone: p.zone, to });
      else drawTable();
      return;
    }
    if (S.moving) {
      const from = S.moving;
      S.moving = null;
      if (from === p.zone) { status("Left where it was."); drawTable(); return; }
      change("move", { zone: from, to: p.zone });
      return;
    }
    betOn(p.zone);
  };
  svg.addEventListener("pointerup", finish);
  svg.addEventListener("pointercancel", finish);

  const pick = () => {
    const name = $("layout-wrap").clientWidth >= 720 ? "wide" : "tall";
    if (name !== S.layout) { S.layout = name; drawTable(); }
  };
  new ResizeObserver(pick).observe($("layout-wrap"));
  pick();

  $("comeout").addEventListener("change", () => change("comeout", { on: $("comeout").checked }));
  $("take-all").addEventListener("click", () => change("take_all_down"));
  $("rebuy").addEventListener("click", () => change("rebuy", { cents: Number($("buy-in").value) || 10000 }));
  $("reset").addEventListener("click", async () => {
    const cents = Number($("buy-in").value) || 10000;
    const ok = await ask("Reset bankroll", `Clear the table and start over with ${money(cents)}? The run stats start over too. ` +
      "Your logged throws are kept.", { ok: "Reset", danger: true });
    if (ok) { await change("reset", { cents }); S.flash = {}; refresh(); }
  });
  $("stats-button").addEventListener("click", () => { drawRunStats(); $("stats-dialog").showModal(); });
}

// ---------------------------------------------------------------- the tracker
function drawTracker() {
  const v = S.tracker;
  const last = clear($("tracker-last"));
  if (v.last) {
    last.insertAdjacentHTML("beforeend", dieSvg(v.last.dice[0]) + dieSvg(v.last.dice[1]));
    item(last, "span", `${v.last.dice[0]}-${v.last.dice[1]} = ${v.last.total}`, "total");
    item($("tracker-last"), "span", `${v.last.source}, ${v.last.dice_set}, ${v.last.position}`, "small muted");
  } else {
    last.insertAdjacentHTML("beforeend", dieSvg(0) + dieSvg(0));
    item(last, "span", "No throws yet", "muted");
  }
  const shooter = clear($("shooter"));
  item(shooter, "div", v.shooter.point ? "ON" : "OFF", `puck ${v.shooter.point ? "on" : "off"}`);
  for (const [value, caption] of [[v.shooter.point || "—", "POINT"], [v.shooter.run, "RUN"], [v.shooter.last_run ?? "—", "LAST RUN"]]) {
    const f = item(shooter, "div", undefined, "figure");
    item(f, "b", String(value));
    item(f, "span", caption);
  }

  const bonus = clear($("bonus"));
  const hit = new Set(v.bonus.hit);
  for (const [name, numbers, done] of [["SMALL", [2, 3, 4, 5, 6], v.bonus.small_done], ["TALL", [8, 9, 10, 11, 12], v.bonus.tall_done]]) {
    const row = item(bonus, "div", undefined, "bonus-row");
    item(row, "span", name, `name${done ? " done" : ""}`);
    for (const n of numbers) item(row, "span", String(n), `ball${hit.has(n) ? " hit" : ""}`);
    if (done) item(row, "span", "✔", "win");
  }
  const m = v.bonus.made;
  item(bonus, "p", `Rolls this run: ${v.bonus.run}   ·   Made: Small ${m.Small} · Tall ${m.Tall} · All ${m.All}`, "small muted");
  if (v.bonus.wins.length) {
    item(bonus, "p", "Recent wins, with the dice sets thrown in that run:", "small muted");
    const list = item(bonus, "ul", undefined, "lines");
    for (const w of v.bonus.wins) {
      const li = item(list, "li");
      item(li, "span", w.time);
      item(li, "span", w.kind, "win");
      item(li, "span", `${w.rolls} rolls`);
      item(li, "span", w.sets, "grow");
    }
  }

  const recent = clear($("recent"));
  for (const t of v.recent) {
    const li = item(recent, "li");
    item(li, "span", t.time.slice(0, 5));
    item(li, "span", `${t.dice[0]}-${t.dice[1]} = ${t.total}`);
    item(li, "span", `${t.dice_set} · ${t.position} · ${t.tag}${t.back_wall ? "" : " · missed wall"}`, "grow");
  }
  if (!v.recent.length) item(recent, "li", "Nothing logged yet.");
  $("accuracy").textContent = v.accuracy;
  $("fair").textContent = v.fair;

  const table = clear($("stats-table"));
  const head = item(table, "tr");
  for (const h of ["Group", "Throws", "Sevens", "95% range", "Rolls/7", "Hardways", "vs random", "Runs", "Nums/run", "S/T/A", "Nums vs random"]) item(head, "th", h);
  for (const r of v.rows) {
    const tr = item(table, "tr");
    for (const key of ["group", "throws", "sevens", "range", "rps", "hardways", "verdict", "runs", "numbers", "bonus", "numbers_verdict"]) {
      item(tr, "td", String(r[key]));
    }
  }
  if (!v.rows.length) item(item(table, "tr"), "td", "No throws yet.");
  drawChart(v.chart);
}

function drawChart(chart) {
  const svg = $("chart");
  svg.replaceChildren();
  const ns = "http://www.w3.org/2000/svg";
  const add = (name, attrs, text) => {
    const node = document.createElementNS(ns, name);
    for (const [k, val] of Object.entries(attrs)) node.setAttribute(k, val);
    if (text !== undefined) node.textContent = text;
    svg.appendChild(node);
  };
  $("chart-title").textContent = `Totals for ${chart.chosen || "the current selection"}`;
  if (!chart.n) {
    add("text", { x: 245, y: 92, fill: "#8fae9c", "text-anchor": "middle", "font-size": 13 }, `No throws logged yet for '${chart.chosen}'`);
    return;
  }
  const left = 30, bottom = 150, top = 18, width = 28, gap = 12;
  const maxRate = Math.max(...chart.bars.map((b) => Math.max(b.rate, b.expected))) * 1.15;
  const scale = (bottom - top) / maxRate;
  chart.bars.forEach((b, i) => {
    const x0 = left + i * (width + gap), x1 = x0 + width;
    add("rect", { x: x0, y: bottom - b.rate * scale, width, height: b.rate * scale, fill: "#dcb64c", rx: 2 });
    add("text", { x: (x0 + x1) / 2, y: bottom - b.rate * scale - 5, fill: "#efe8d2", "font-size": 10, "text-anchor": "middle" }, `${Math.round(b.rate * 100)}%`);
    const ey = bottom - b.expected * scale;
    add("line", { x1: x0 - 3, x2: x1 + 3, y1: ey, y2: ey, stroke: "#e05252", "stroke-width": 2 });
    add("text", { x: (x0 + x1) / 2, y: bottom + 15, fill: "#efe8d2", "font-size": 12, "font-weight": 700, "text-anchor": "middle" }, String(b.total));
  });
  add("line", { x1: left - 5, x2: left + 11 * (width + gap), y1: bottom, y2: bottom, stroke: "#2a6650" });
  add("text", { x: 245, y: 178, fill: "#8fae9c", "font-size": 12, "text-anchor": "middle" }, `${chart.chosen}: ${chart.n} throws`);
}

// ---------------------------------------------------------------- the menu and the players
function setupMenu() {
  $("menu-button").addEventListener("click", () => $("menu").showModal());
  $("menu-name").addEventListener("click", async () => {
    $("menu").close();
    const name = await ask("Your name", "", { input: true, value: S.me.name });
    if (!name) return;
    try {
      await api("/api/me", { name });
      S.me = await api("/api/me");
      fillControls();
      status(`The table calls you ${name} now.`);
    } catch (error) { status(error.message, true); }
  });
  $("menu-players").addEventListener("click", () => { $("menu").close(); openPlayers(); });
  const signOut = async (everywhere) => {
    await api("/api/auth/signout", { everywhere }).catch(() => {});
    location.href = "/";
  };
  $("menu-signout").addEventListener("click", () => signOut(false));
  $("menu-signout-all").addEventListener("click", () => signOut(true));
  $("menu-delete").addEventListener("click", async () => {
    $("menu").close();
    const typed = await ask("Delete my account", "This removes your account, your guests and every throw, for good. " +
      "Download your throws first if you want to keep them. Type DELETE to go ahead.", { input: true, placeholder: "DELETE", ok: "Delete", danger: true });
    if (typed === null) return;
    try {
      await api("/api/me/delete", { confirm: typed });
      location.href = "/";
    } catch (error) { status(error.message, true); }
  });
  $("add-guest").addEventListener("click", addGuest);
  $("guest-name").addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); addGuest(); } });
}

async function reloadPlayers(message) {
  S.me = await api("/api/me");
  if (!S.me.players.some((p) => p.id === S.player)) S.player = S.me.players[0].id;
  S.settings.player_id = S.player;
  fillControls();
  drawPlayers();
  if (message) $("players-status").textContent = message;
}

function drawPlayers() {
  const list = clear($("players-list"));
  for (const p of S.me.players) {
    const li = item(list, "li");
    item(li, "span", p.is_self ? `${p.name} (you)` : p.name, "grow");
    item(li, "span", `${p.throws} throws`, "small muted");
    const button = (label, run, cls) => {
      const b = item(li, "button", label, cls);
      b.type = "button";
      b.addEventListener("click", run);
    };
    button("Rename", async () => {
      const name = await ask("Rename", "", { input: true, value: p.name });
      if (!name) return;
      try { await api(`/api/players/${p.id}`, { name }); await reloadPlayers(`Renamed to ${name}.`); }
      catch (error) { $("players-status").textContent = error.message; }
    });
    if (!p.is_self) {
      button("Send history", async () => {
        const email = await ask(`Send ${p.name} their history`, `We'll email ${p.name} a link. Opening it moves these ${p.throws} throws ` +
          "into an account of their own (one is made if they're new), and they leave your table.", { input: true, type: "email", placeholder: "their@email.com", ok: "Send" });
        if (!email) return;
        try { const r = await api(`/api/players/${p.id}/invite`, { email }); $("players-status").textContent = r.message; }
        catch (error) { $("players-status").textContent = error.message; }
      });
      button("Remove", async () => {
        const ok = await ask(`Remove ${p.name}?`, `Their bankroll and all ${p.throws} of their throws are deleted.`, { ok: "Remove", danger: true });
        if (!ok) return;
        try { await api(`/api/players/${p.id}/delete`, {}); await reloadPlayers(`${p.name} has left the table.`); refresh(); }
        catch (error) { $("players-status").textContent = error.message; }
      }, "danger");
    }
  }
}

function openPlayers() {
  $("players-status").textContent = "";
  drawPlayers();
  $("players-dialog").showModal();
}

async function addGuest() {
  const name = $("guest-name").value.trim();
  if (!name) return;
  try {
    const r = await api("/api/players", { name });
    $("guest-name").value = "";
    S.player = r.id;
    saveSettings({ player_id: r.id });
    await reloadPlayers(`${name} has a seat. They're throwing now.`);
    refresh();
  } catch (error) {
    $("players-status").textContent = error.message;
  }
}

// ---------------------------------------------------------------- live updates from the server
let live = null;
function connectLive() {
  if (live || !window.EventSource) return;
  live = new EventSource("/api/events");
  let first = true;
  live.onopen = () => {
    if (!first) refresh();                 // back after a dropped connection: catch up on anything missed
    first = false;
  };
  live.onmessage = (event) => {
    let m;
    try { m = JSON.parse(event.data); } catch { return; }
    const mine = m.client === CLIENT;
    if (m.type === "changed") {
      if (!mine && (m.player_id == null || m.player_id === S.player)) refresh();
    } else if (m.type === "players") {
      if (!mine) reloadPlayers().then(refresh).catch(() => {});
    } else if (m.type === "camera") {
      loadCamera();
    } else if (m.type === "camera_throw") {
      if (m.player_id === S.player) refresh(true);
      if (mine) {
        if ($("sounds").checked) sound.beep(true);
        if (m.call?.length && $("voice").value !== "Off") sound.say(m.call, voiceSlug());
        status(m.message);
      }
    } else if (m.type === "camera_unclear") {
      if (mine) {
        if ($("sounds").checked) sound.beep(false);
        status(m.message, true);
      }
    } else if (m.type === "camera_error") {
      status(m.message, true);
      loadCamera();
    }
  };
}

// ---------------------------------------------------------------- the camera at the table
function cameraMine() {
  return !!(S.camera?.running && S.camera.mine && S.camera.client === CLIENT);
}

function cameraBinding() {
  return { player_id: S.player, mode: S.tab === "tracker" ? "tracker" : "table", back_wall: $("back-wall").checked,
    dice_set: currentSet(), position: currentPosition(), tz_offset: -new Date().getTimezoneOffset() };
}

async function loadCamera() {
  if (!S.camera?.allowed) return;
  try { S.camera = await api("/api/camera"); } catch { return; }
  drawCamera();
}

function drawCamera() {
  const c = S.camera;
  $("camera-card").hidden = !c?.allowed;
  if (!c?.allowed) return;
  const button = $("camera-button");
  const elsewhere = c.running && !cameraMine();
  button.textContent = !c.running ? "Start camera" : elsewhere ? (c.mine ? "Use the camera here" : "Camera busy") : "Stop camera";
  button.disabled = c.running && !c.mine;
  let text = "Camera off";
  if (c.running) {
    const size = c.resolution ? `${c.resolution[0]}x${c.resolution[1]}${c.fps ? ` @ ${c.fps} fps` : ""}: ` : "";
    text = elsewhere ? (c.mine ? "Running from your other device." : `${c.owner} is using it.`) : `Camera ${size}${c.status || "starting"}`;
  } else if (c.error) {
    text = c.error;
  }
  $("camera-status").textContent = text;
  $("camera-hint").textContent = cameraMine()
    ? `Throws go to ${$("player").selectedOptions[0]?.textContent || "this player"} on the ${S.tab === "tracker" ? "tracker (no bets)" : "table"}. ` +
      "Wrong read? Tap the right dice (left die first), then Fix last." + (c.model ? `  (${c.model})` : "")
    : "";
  $("camera-tune").disabled = !c.running;
  $("camera-preview").hidden = !c.running || !$("camera-show").checked;
  previewLoop();
}

async function toggleCamera() {
  sound.unlock();
  try {
    if (cameraMine()) S.camera = await api("/api/camera/stop", {});
    else S.camera = await api("/api/camera/start", cameraBinding());
    status(S.camera.running ? "Camera on. Throw when your bets are down." : "Camera off.");
  } catch (error) {
    status(error.message, true);
    await loadCamera();
  }
  drawCamera();
}

async function syncCamera() {
  if (!cameraMine()) return;
  try { S.camera = await api("/api/camera/update", cameraBinding()); } catch (error) { status(error.message, true); }
  drawCamera();
}

// The picture: the next one is asked for once the last has arrived, a few times a second
let previewBusy = false, statusTick = 0;
function previewLoop() {
  if (previewBusy) return;
  const targets = [];
  if (!$("camera-preview").hidden) targets.push($("camera-preview"));
  if ($("tune-dialog").open) targets.push($("tune-preview"));
  if (!S.camera?.running || !targets.length || document.hidden) return;
  previewBusy = true;
  const img = new Image();
  img.onload = img.onerror = () => {
    if (img.naturalWidth) for (const t of targets) t.src = img.src;
    previewBusy = false;
    if (++statusTick % 8 === 0) loadCamera(); else setTimeout(previewLoop, 250);
  };
  img.src = `/api/camera/preview.jpg?t=${Date.now()}`;
}

// The tuning sliders: dice_camera_test.py's, for a camera on a computer with no screen of its own
const SLIDERS = [
  ["hue_low", "Hue low", 0, 179], ["hue_high", "Hue high", 0, 179], ["sat_low", "Saturation low", 0, 255],
  ["val_low", "Brightness low", 0, 255], ["white_level", "White (pips)", 0, 255],
  ["min_die_area", "Smallest die", 50, 20000], ["two_dice_area", "Two dice touching", 500, 40000],
  ["exposure", "Exposure", -13, 0], ["focus", "Focus", 0, 255, true], ["gain", "Gain", 0, 255, true],
];
let tuneStart = null, tuneTimer = null;

async function openTuning() {
  let values;
  try { values = await api("/api/camera/settings"); } catch (error) { status(error.message, true); return; }
  tuneStart = values;
  const box = clear($("sliders"));
  for (let [key, label, low, high, optional] of SLIDERS) {
    if (key === "exposure" && values.linux) {
      key = "exposure_linux"; label = "Exposure (0.1 ms)"; low = 1; high = 2500;
    }
    const value = key === "exposure_linux" ? values.effective_exposure : values[key];
    item(box, "label", label).htmlFor = `tune-${key}`;
    const range = item(box, "input");
    Object.assign(range, { type: "range", id: `tune-${key}`, min: low, max: high, step: 1, value: value ?? low });
    range.dataset.key = key;
    const out = item(box, "output", value ?? "auto");
    range.addEventListener("input", () => {
      out.textContent = range.value;
      if (optional) $(`auto-${key}`).checked = false;
      sendTuning();
    });
    if (optional) {
      item(box, "span");
      const auto = item(item(box, "span", undefined, "auto"), "label", undefined, "toggle");
      const check = item(auto, "input");
      Object.assign(check, { type: "checkbox", id: `auto-${key}`, checked: value == null });
      auto.append(" Let the camera choose");
      check.addEventListener("change", () => { out.textContent = check.checked ? "auto" : range.value; sendTuning(); });
    }
  }
  $("tune-status").textContent = "";
  $("tune-dialog").showModal();
  previewLoop();
}

function tuningValues() {
  const values = {};
  for (const range of $("sliders").querySelectorAll("input[type=range]")) {
    const auto = $(`auto-${range.dataset.key}`);
    values[range.dataset.key] = auto?.checked ? null : Number(range.value);
  }
  return values;
}

function sendTuning(save = false) {
  clearTimeout(tuneTimer);
  const view = document.querySelector("input[name=tune-view]:checked").value;
  const go = () => api("/api/camera/settings", { values: tuningValues(), view, save })
    .then(() => { if (save) status("Camera settings saved."); })
    .catch((error) => { $("tune-status").textContent = error.message; });
  if (save) return go();
  tuneTimer = setTimeout(go, 150);
}

function setupCamera() {
  $("camera-button").addEventListener("click", toggleCamera);
  $("camera-show").addEventListener("change", drawCamera);
  $("camera-tune").addEventListener("click", openTuning);
  $("tune-train").addEventListener("click", async () => {
    const button = $("tune-train"), report = $("train-report");
    button.disabled = true;
    report.hidden = false;
    report.textContent = "Learning from your saved dice pictures…";
    try {
      const result = await api("/api/camera/train", {});
      report.textContent = result.report;
      loadCamera();
    } catch (error) {
      report.textContent = error.message;
    }
    button.disabled = false;
  });
  for (const radio of document.querySelectorAll("input[name=tune-view]")) radio.addEventListener("change", () => sendTuning());
  $("tune-dialog").addEventListener("close", async () => {
    const choice = $("tune-dialog").returnValue;
    if (choice === "save") {
      await sendTuning(true);
    } else if (tuneStart) {                // put back what it was
      const values = {};
      for (const range of $("sliders").querySelectorAll("input[type=range]")) {
        const key = range.dataset.key;
        values[key] = key === "exposure_linux" ? tuneStart.exposure_linux : tuneStart[key];
      }
      await api("/api/camera/settings", { values, view: "normal" }).catch(() => {});
    }
    await api("/api/camera/settings", { values: {}, view: "normal" }).catch(() => {});
    document.querySelector("input[name=tune-view][value=normal]").checked = true;
  });
  document.addEventListener("visibilitychange", previewLoop);
}

// ---------------------------------------------------------------- go
async function start() {
  try {
    S.config = await api("/api/config");
  } catch (error) {
    showGate("signin");
    $("signin-status").textContent = error.message;
    return;
  }
  setupSignIn();
  setupNaming();
  setupControls();
  setupEntry();
  setupLayout();
  setupMenu();
  guardSheet();
  setupCamera();
  document.addEventListener("visibilitychange", () => { if (!document.hidden && !$("main").hidden) refresh(); });
  if (location.pathname === "/auth/verify") return verifyLink();
  await boot();
}

start();
