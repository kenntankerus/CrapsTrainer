// The craps layout, drawn as SVG. Two arrangements of the same spots: "wide" is craps_game.py's layout,
// "tall" stacks it for a phone held upright. Zone keys are the engine's spots, plus the two odds boxes and "hop".

export const POINTS = [4, 5, 6, 8, 9, 10];
const WORDS = { 4: "FOUR", 5: "FIVE", 6: "SIX", 8: "EIGHT", 9: "NINE", 10: "TEN" };
const HARD_PAYS = { 4: 7, 6: 9, 8: 9, 10: 7 };
export const COLORS = {
  felt: "#0f4a33", line: "#e9e3cf", text: "#efe8d2", muted: "#8fae9c", gold: "#dcb64c", red: "#e05252",
};
const CHIP_COLOURS = [[50000, "#6b3fa0", "#efe8d2"], [10000, "#1b1b1b", "#efe8d2"], [2500, "#2e8b57", "#efe8d2"],
  [500, "#c0392b", "#efe8d2"], [0, "#ecebe4", "#1a1405"]];
const SVG = "http://www.w3.org/2000/svg";

export function chipColours(cents) {
  const [, fill, text] = CHIP_COLOURS.find(([floor]) => cents >= floor);
  return { fill, text };
}

export function money(cents) {
  const sign = cents < 0 ? "-" : "";
  const abs = Math.abs(cents);
  const dollars = Math.floor(abs / 100), rest = abs % 100;
  return `${sign}$${dollars.toLocaleString("en-US")}` + (rest ? `.${String(rest).padStart(2, "0")}` : "");
}

export function chipLabel(cents) {
  if (cents >= 100000 && cents % 10000 === 0) return `${cents / 100000}k`;
  return money(cents).slice(1);
}

function wideZones() {
  const z = { small: [150, 10, 390, 44], all: [395, 10, 530, 44], tall: [535, 10, 775, 44], dont_come: [10, 50, 140, 216] };
  POINTS.forEach((n, i) => {
    const x0 = 150 + i * 105, x1 = 250 + i * 105;
    z[`lay_${n}`] = [x0, 50, x1, 72];
    z[`dc_${n}`] = [x0, 72, x1, 98];
    z[`place_${n}`] = [x0, 98, x1, 168];
    z[`come_${n}`] = [x0, 168, x1, 194];
    z[`buy_${n}`] = [x0, 194, x1, 216];
  });
  Object.assign(z, {
    come: [150, 222, 775, 280], field: [150, 286, 775, 346], big6: [10, 222, 73, 346], big8: [77, 222, 140, 346],
    dont_pass: [10, 352, 775, 382], pass: [10, 388, 775, 428], pass_odds: [10, 434, 390, 464], dp_odds: [395, 434, 775, 464],
    hard_6: [790, 10, 903, 70], hard_10: [907, 10, 1020, 70], hard_8: [790, 74, 903, 134], hard_4: [907, 74, 1020, 134],
    any7: [790, 140, 1020, 176], aces: [790, 182, 864, 232], ace_deuce: [868, 182, 942, 232], twelve: [946, 182, 1020, 232],
    yo: [790, 236, 864, 286], horn: [868, 236, 942, 286], ce: [946, 236, 1020, 286], any_craps: [790, 292, 1020, 328],
    hop: [790, 334, 1020, 464],
  });
  return z;
}

function tallZones() {
  const z = { small: [6, 6, 394, 40], tall: [6, 44, 394, 78], all: [6, 82, 394, 110] };
  POINTS.forEach((n, i) => {
    const x0 = 6 + i * 65, x1 = x0 + 63;
    z[`lay_${n}`] = [x0, 116, x1, 142];
    z[`dc_${n}`] = [x0, 142, x1, 172];
    z[`place_${n}`] = [x0, 172, x1, 232];
    z[`come_${n}`] = [x0, 232, x1, 262];
    z[`buy_${n}`] = [x0, 262, x1, 288];
  });
  Object.assign(z, {
    dont_come: [6, 294, 394, 328], come: [6, 332, 394, 380], field: [6, 384, 394, 440],
    big6: [6, 444, 198, 478], big8: [202, 444, 394, 478], dont_pass: [6, 482, 394, 514], pass: [6, 518, 394, 562],
    pass_odds: [6, 566, 198, 600], dp_odds: [202, 566, 394, 600],
    hard_6: [6, 610, 100, 662], hard_10: [104, 610, 198, 662], hard_8: [202, 610, 296, 662], hard_4: [300, 610, 394, 662],
    any7: [6, 666, 394, 698], aces: [6, 702, 131, 748], ace_deuce: [135, 702, 260, 748], twelve: [264, 702, 394, 748],
    yo: [6, 752, 131, 798], horn: [135, 752, 260, 798], ce: [264, 752, 394, 798], any_craps: [6, 802, 394, 834],
    hop: [6, 838, 394, 924],
  });
  return z;
}

const LABELS = {
  small: "SMALL\n34 to 1", tall: "TALL\n34 to 1", all: "MAKE 'EM ALL\n175 to 1", dont_come: "DON'T\nCOME\n\nBar 12",
  come: "COME", big6: "BIG\n6", big8: "BIG\n8", field: "FIELD    3 · 4 · 9 · 10 · 11      2 pays double · 12 pays triple",
  dont_pass: "DON'T PASS BAR 12", pass: "PASS LINE", pass_odds: "PASS ODDS  (3-4-5x)", dp_odds: "DON'T PASS ODDS  (lay)",
  any7: "ANY SEVEN   4 to 1", aces: "2\n30 to 1", ace_deuce: "3\n15 to 1", twelve: "12\n30 to 1", yo: "11\n15 to 1",
  horn: "HORN\n2·3·11·12", ce: "C & E\n3 · 7 to 1", any_craps: "ANY CRAPS   7 to 1",
};
for (const [n, pays] of Object.entries(HARD_PAYS)) LABELS[`hard_${n}`] = `HARD ${n}\n${pays} to 1`;
const TALL_LABELS = {
  ...LABELS, small: "SMALL  34 to 1", tall: "TALL  34 to 1", all: "MAKE 'EM ALL  175 to 1", dont_come: "DON'T COME   Bar 12",
  field: "FIELD   3 · 4 · 9 · 10 · 11\n2 pays double · 12 pays triple", big6: "BIG 6", big8: "BIG 8",
  pass_odds: "PASS ODDS (3-4-5x)", dp_odds: "DON'T PASS ODDS", any7: "ANY SEVEN  4 to 1", any_craps: "ANY CRAPS  7 to 1",
};

export const LAYOUTS = {
  wide: { zones: wideZones(), labels: LABELS, width: 1030, height: 474, markerStart: 72, numberText: { lay: "LAY", buy: "BUY", dc: "DON'T COME", come: "COME" } },
  tall: { zones: tallZones(), labels: TALL_LABELS, width: 400, height: 930, markerStart: 130, numberText: { lay: "LAY", buy: "BUY", dc: "DC", come: "COME" } },
};

function el(name, attrs = {}, text) {
  const node = document.createElementNS(SVG, name);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  if (text !== undefined) node.textContent = text;
  return node;
}

function textLines(parent, x, y, label, attrs, lineHeight) {
  const lines = label.split("\n");
  const t = el("text", { x, y: y - ((lines.length - 1) * lineHeight) / 2, ...attrs });
  lines.forEach((line, i) => {
    const span = el("tspan", { x, dy: i ? lineHeight : 0 }, line || " ");
    t.appendChild(span);
  });
  parent.appendChild(t);
  return t;
}

// Where a bet's chip sits in its box, clear of the printed label (and of the puck in the number boxes)
function chipSpot(name, key, [x0, y0, x1, y1]) {
  const w = x1 - x0, h = y1 - y0;
  let r = Math.min(15, h / 2 - 2), cx = (x0 + x1) / 2, cy = (y0 + y1) / 2, oddsDir = 1;
  if (key.startsWith("place_")) {
    if (name === "tall") r = 10;
    cx = x0 + r + 3; cy = y0 + r + 3;
  } else if (["small", "tall", "all"].includes(key)) {
    r = 11; cx = x1 - r - 3;
  } else if (name === "tall") {
    r = Math.min(13, h / 2 - 3); cx = x1 - r - 4; oddsDir = -1;
  } else if (key.startsWith("come_") || key.startsWith("dc_")) {
    cx = x0 + r + 20;
  } else if (h > w) {
    cy = y1 - r - 8;
  } else if (h <= 60 && w <= 160) {
    r = w < 80 ? 9 : 11; cx = x1 - r - 3; cy = y0 + r + 3;
  } else {
    cx = x1 - r - 8;
  }
  return { cx, cy, r, oddsX: cx + oddsDir * (2 * r + 6) };
}

function drawChip(g, x, y, radius, cents, off = false, offLeft = false) {
  const { fill, text } = chipColours(cents);
  g.appendChild(el("circle", { cx: x + 2, cy: y + 3, r: radius, fill: "#05140e" }));
  g.appendChild(el("circle", { cx: x, cy: y, r: radius, fill, stroke: COLORS.line, "stroke-width": 2, "stroke-dasharray": "3 3" }));
  g.appendChild(el("text", { x, y: y + 0.5, fill: text, "font-size": radius > 11 ? 10.5 : 9, "font-weight": 700,
    "text-anchor": "middle", "dominant-baseline": "middle" }, chipLabel(cents)));
  if (off) {
    const side = offLeft ? -1 : 1;
    g.appendChild(el("text", { x: x + side * (radius + 2), y: y - radius + 6, fill: COLORS.red, "font-size": 9,
      "font-weight": 700, "text-anchor": offLeft ? "end" : "start" }, "OFF"));
  }
}

// Draw the whole layout into the svg. view is the server's table view; ui holds hover, flash, hop and moving.
export function drawLayout(svg, name, view, ui) {
  const L = LAYOUTS[name];
  svg.setAttribute("viewBox", `0 0 ${L.width} ${L.height}`);
  svg.replaceChildren();
  svg.appendChild(el("rect", { x: 0, y: 0, width: L.width, height: L.height, rx: 14, fill: COLORS.felt }));
  const zonesG = el("g"), chipsG = el("g", { "pointer-events": "none" });
  svg.append(zonesG, chipsG);
  const hits = new Set(view?.bonus_hits || []);

  for (const [key, box] of Object.entries(L.zones)) {
    const [x0, y0, x1, y1] = box;
    const g = el("g", { "data-zone": key, class: "zone" });
    const flash = ui.flash?.[key];
    const lit = key === ui.hover || key === ui.moving;
    const stroke = lit ? COLORS.gold : flash === "win" ? COLORS.gold : flash === "loss" ? COLORS.red : COLORS.line;
    g.appendChild(el("rect", { x: x0, y: y0, width: x1 - x0, height: y1 - y0, rx: 3, fill: lit ? "rgba(220,182,76,0.12)" : "transparent",
      stroke, "stroke-width": lit || flash ? 3 : 1.5 }));
    const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2;
    const [kind, number] = [key.split("_")[0], key.split("_")[1]];
    const common = { "text-anchor": "middle", "dominant-baseline": "middle", "font-weight": 700 };
    if (kind === "place") {
      g.appendChild(el("text", { ...common, x: name === "tall" ? cx - 4 : cx, y: cy - 8, fill: COLORS.text, "font-size": name === "tall" ? 28 : 32 }, number));
      g.appendChild(el("text", { ...common, x: cx, y: cy + 21, fill: COLORS.muted, "font-size": 10 }, WORDS[number]));
    } else if (["lay", "buy", "dc", "come"].includes(kind) && number) {
      g.appendChild(el("text", { ...common, x: cx, y: cy, fill: COLORS.muted, "font-size": 9 }, L.numberText[kind]));
    } else if (key === "hop") {
      g.appendChild(el("text", { x: x0 + 10, y: y0 + 18, fill: COLORS.text, "font-size": 12, "font-weight": 700 }, `HOP  ${ui.hop}`));
      g.appendChild(el("text", { x: x0 + 10, y: y0 + 34, fill: COLORS.muted, "font-size": 10, "font-weight": 700 }, "easy 15 to 1 · hard 30 to 1"));
    } else if (["small", "tall", "all"].includes(key)) {
      const done = { small: view?.small_done, tall: view?.tall_done, all: false }[key];
      textLines(g, x0 + 8, cy, L.labels[key], { fill: done ? COLORS.gold : COLORS.text, "font-size": 10, "font-weight": 700,
        "dominant-baseline": "middle" }, 12);
      if (key !== "all") {
        const numbers = key === "small" ? [2, 3, 4, 5, 6] : [8, 9, 10, 11, 12];
        numbers.forEach((n, i) => {
          const mx = x0 + L.markerStart + i * 27, hit = hits.has(n);
          g.appendChild(el("circle", { cx: mx, cy, r: 12, fill: hit ? COLORS.gold : COLORS.felt, stroke: hit ? COLORS.gold : COLORS.muted, "stroke-width": 1.5 }));
          g.appendChild(el("text", { ...common, x: mx, y: cy + 0.5, fill: hit ? "#1a1405" : COLORS.muted, "font-size": 10 }, String(n)));
        });
      }
    } else {
      const big = ["pass", "come", "field", "dont_pass"].includes(key);
      const size = ["pass", "come"].includes(key) ? 17 : 11.5;
      textLines(g, x1 - x0 < 80 && name === "wide" ? cx - 6 : cx, cy, L.labels[key],
        { ...common, fill: big ? COLORS.text : COLORS.muted, "font-size": size }, size * 1.2);
    }
    zonesG.appendChild(g);
  }

  if (!view) return;
  // the puck
  if (view.point) {
    const [, y0, x1] = L.zones[`place_${view.point}`];
    const pr = name === "tall" ? 10 : 13, px = x1 - pr - 3, py = y0 + pr + 3;
    chipsG.appendChild(el("circle", { cx: px, cy: py, r: pr, fill: "#f4f1e6", stroke: COLORS.gold, "stroke-width": 2 }));
    chipsG.appendChild(el("text", { x: px, y: py + 0.5, fill: "#111", "font-size": pr - 2, "font-weight": 700, "text-anchor": "middle", "dominant-baseline": "middle" }, "ON"));
  } else {
    const [, y0, x1] = L.zones.dont_come;
    chipsG.appendChild(el("circle", { cx: x1 - 20, cy: y0 + 17, r: 14, fill: "#111", stroke: COLORS.line, "stroke-width": 2 }));
    chipsG.appendChild(el("text", { x: x1 - 20, y: y0 + 17.5, fill: "#f4f1e6", "font-size": 9.5, "font-weight": 700, "text-anchor": "middle", "dominant-baseline": "middle" }, "OFF"));
  }

  const hops = [];
  for (const bet of view.bets) {
    if (bet.spot.startsWith("hop_")) {
      hops.push(`${bet.name.slice(4)}: ${money(bet.amount)}`);
      continue;
    }
    const box = L.zones[bet.spot];
    if (!box) continue;
    const { cx, cy, r, oddsX } = chipSpot(name, bet.spot, box);
    drawChip(chipsG, cx, cy, r, bet.amount, !bet.working, cx > (box[0] + box[2]) / 2);
    if (bet.odds) {
      if (bet.spot === "pass" || bet.spot === "dont_pass") {
        const [, oy0, ox1, oy1] = L.zones[bet.spot === "pass" ? "pass_odds" : "dp_odds"];
        drawChip(chipsG, ox1 - 21, (oy0 + oy1) / 2, 13, bet.odds);
      } else {
        drawChip(chipsG, oddsX, cy, r, bet.odds, !bet.odds_working);
      }
    }
  }
  if (hops.length) {
    const [x0, y0] = L.zones.hop;
    hops.forEach((line, i) => {
      const cols = name === "tall" ? 3 : 2, rows = name === "tall" ? 2 : 4;
      const col = Math.floor(i / rows) % cols, row = i % rows;
      chipsG.appendChild(el("text", { x: x0 + 14 + col * (name === "tall" ? 125 : 110), y: y0 + 56 + row * 18,
        fill: COLORS.gold, "font-size": 12, "font-weight": 700 }, line));
    });
  }
}

// A die face as SVG markup, for the dice pad and the last-roll panels (0 draws a blank die)
const PIPS = { 1: [[.5, .5]], 2: [[.27, .27], [.73, .73]], 3: [[.27, .27], [.5, .5], [.73, .73]],
  4: [[.27, .27], [.73, .27], [.27, .73], [.73, .73]], 5: [[.27, .27], [.73, .27], [.5, .5], [.27, .73], [.73, .73]],
  6: [[.27, .25], [.27, .5], [.27, .75], [.73, .25], [.73, .5], [.73, .75]] };

export function dieSvg(value, size = 56) {
  const pips = (PIPS[value] || []).map(([x, y]) => `<circle cx="${x * 100}" cy="${y * 100}" r="8.5" fill="#f6efdc"/>`).join("");
  return `<svg class="die" width="${size}" height="${size}" viewBox="-3 -3 108 108" aria-hidden="true">` +
    `<rect x="1" y="3" width="100" height="100" rx="18" fill="#05140e"/>` +
    `<rect x="-1" y="0" width="100" height="100" rx="18" fill="${value ? "#c23a32" : "#2a4a3c"}" stroke="#8f2a24"/>${pips}</svg>`;
}
