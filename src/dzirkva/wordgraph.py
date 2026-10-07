"""Word graph (/words): a word, its family (ka-lemma levels) and the words closest in meaning (word2vec).

Data: data/wordgraph.db from scripts/build_word_graph.py, one row per lemma; a page is a few lookups, no model.
"""

import json
import sqlite3
import time
from functools import cache
from pathlib import Path

from dzirkva.georgian import normalize
from dzirkva.morph import analyze, ka_lemma

DB = Path(__file__).resolve().parents[2] / "data" / "wordgraph.db"
KINDS = {4: "f4", 3: "f3", 2: "f2", 1: "f1"}  # ka-lemma level → node kind; "near" for meaning neighbours
START = ("ღვინო", "სახლი", "წიგნი", "მთა", "ზღვა", "პური", "ქალაქი", "სიმღერა", "ბაღი", "მეგობარი")  # /words alone: one a day
NEAR = 16    # meaning neighbours shown
FAMILY = 14  # family forms shown


@cache
def _db() -> sqlite3.Connection | None:
    return sqlite3.connect(f"file:{DB}?mode=ro", uri=True, check_same_thread=False) if DB.exists() else None


def _row(word: str) -> tuple[int, list, list] | None:
    db = _db()
    row = db.execute("SELECT count, family, near FROM g WHERE word = ?", (word,)).fetchone() if db else None
    return (row[0], json.loads(row[1]), json.loads(row[2])) if row else None


def resolve(raw: str) -> str | None:
    """The lemma the graph has for a typed word: the word itself, its ka-lemma lemma, or its morph.py lemma."""
    w = normalize(raw.strip().split()[0]) if raw.strip() else ""
    if not w:
        return None
    ka = ka_lemma(w)
    for cand in (w, ka[0] if ka else None, *(a.lemma for a in analyze(w)[:3])):
        if cand and _row(cand):
            return cand
    return None


def graph(word: str) -> dict | None:
    """{word, count, nodes: [{w, kind, sim, count}], links: [[a, b]]}. Links join two shown meaning neighbours
    when one is among the other's nearest words: groups of synonyms become visible."""
    row = _row(word)
    if row is None:
        return None
    count, family, near = row
    # a form whose lemma is this word opens no new map (ღვინოს → ღვინო): the page says so instead
    nodes = [{"w": f, "kind": KINDS[level], "sim": sim, "count": c, "opens": resolve(f) not in (None, word)}
             for f, level, c, sim in family[:FAMILY]]
    shown = {n for n, _ in near[:NEAR]}
    links: set[tuple[str, str]] = set()
    for n, sim in near[:NEAR]:
        other = _row(n)
        nodes.append({"w": n, "kind": "near", "sim": sim, "count": other[0] if other else 0, "opens": True})
        if other:
            links |= {tuple(sorted((n, m))) for m, _ in other[2] if m in shown}
    return {"word": word, "count": count, "nodes": nodes, "links": sorted(links)}


LABELS = {"center": "", "f4": "ფორმები", "f3": "ზმნისწინი და საწყისი", "f2": "სხვა ზმნისწინი",
          "f1": "ნაწარმოები", "near": "მსგავსი მნიშვნელობით"}

CSS = """
.wg{--k-center:#d9774b;--k-f4:#a9cf8e;--k-f3:#e2b87c;--k-f2:#c7b3e6;--k-f1:#ef9f9f;--k-near:#8ec5e0}
.wg-top{display:flex;flex-wrap:wrap;align-items:flex-end;gap:14px 18px;margin:24px 0 12px}
.wg-title{display:grid;grid-template-columns:auto 1fr;align-items:baseline;column-gap:14px}
.nav,.nav:visited{background:#8ec5e02a}
.wg-top h1{margin:0;font-size:30px;line-height:1.2;font-weight:600;color:var(--ink);letter-spacing:.02em}
.wg-top .m{font-size:12.5px}
.wg-intro{margin:0 0 6px;font-size:14.5px;color:var(--text);max-width:620px}
.wg-form{display:flex;gap:8px;margin-left:auto;min-width:220px;flex:0 1 300px}
.wg-legend{display:flex;flex-wrap:wrap;gap:6px 8px;margin:2px 0 8px}
.wg-legend button{display:flex;align-items:center;gap:7px;background:transparent;color:var(--muted);
 box-shadow:none;padding:3px 8px;font-size:11.5px;font-weight:500;transition:opacity .2s,color .2s}
.wg-legend button:hover{color:var(--ink)}
.wg-legend button i{width:9px;height:9px;border-radius:50%;background:var(--c)}
.wg-legend button.off{opacity:.4;text-decoration:line-through}
.wg-stage{position:relative;left:50%;transform:translateX(-50%);width:min(1100px,calc(100vw - 24px));
 height:min(68vh,640px);min-height:380px;margin:14px 0 6px;border:1px solid var(--line);border-radius:16px;overflow:hidden;
 background:radial-gradient(ellipse at 50% 45%,#2a221d 0%,var(--bg) 70%)}
.wg-stage svg{width:100%;height:100%;display:block;touch-action:none;user-select:none;-webkit-user-select:none}
.wg-side{position:absolute;top:14px;font-size:10.5px;letter-spacing:.06em;color:var(--muted);pointer-events:none}
.wg-side.l{left:18px} .wg-side.r{right:18px} .wg-stage.tall .wg-side.r{top:auto;bottom:14px;left:18px;right:auto}
.wg .edge{fill:none;stroke-linecap:round;transition:opacity .25s}
.wg .tie{stroke:#8ec5e033;stroke-width:1;stroke-dasharray:3 4;transition:opacity .25s}
.wg .node{cursor:pointer;transition:opacity .25s}
.wg .node rect{fill:var(--card);stroke:var(--c);stroke-width:1.2;transition:fill .2s}
.wg .node text{fill:var(--ink);font-size:14px;dominant-baseline:central;text-anchor:middle;pointer-events:none}
.wg .node.center rect{fill:var(--k-center);stroke:none} .wg .node.center text{fill:#1b1714;font-size:19px;font-weight:600}
.wg .node:hover rect,.wg .node.hot rect,.wg .node:focus rect{fill:#3a3029} .wg .node.center:hover rect{fill:#e88a5f}
.wg .node:focus{outline:none}
.wg svg.focus .node:not(.hot):not(.center),.wg svg.focus .edge:not(.hot),.wg svg.focus .tie:not(.hot){opacity:.18}
.wg .node.enter{opacity:0}
.wg .node.same{cursor:default} .wg .node.same rect{stroke-dasharray:3 3;fill:transparent}
.wg .node.pulse rect{animation:wg-pulse .5s ease}
@keyframes wg-pulse{50%{transform:scale(1.12)}}
.wg-tip{position:absolute;pointer-events:none;background:#14110fee;border:1px solid var(--line);border-radius:10px;
 padding:8px 12px;font-size:12.5px;line-height:1.5;color:var(--text);opacity:0;transform:translateY(4px);
 transition:opacity .15s,transform .15s;max-width:260px;z-index:2}
.wg-tip.on{opacity:1;transform:none} .wg-tip b{color:var(--ink);font-size:15px;font-weight:600}
.wg-tip .k{display:inline-block;margin-top:2px;font-size:11px;color:var(--c)}
.wg-hint{font-size:13px;margin:0 0 16px}
.wg-lists .row{margin:10px 0} .wg-lists .row .cap{display:flex;align-items:center;gap:7px;font-size:11px;color:var(--muted);margin-bottom:5px}
.wg-lists .row .cap i{width:8px;height:8px;border-radius:50%;background:var(--c)}
.wg-lists .rel a{font-size:13.5px}
.wg-lists .rel span{border:1px dashed var(--line);border-radius:16px;padding:4px 12px;font-size:13.5px;color:var(--muted)}
.wg-empty{padding:40px 0;text-align:center;color:var(--muted)}
@media (max-width:520px){.wg-stage{height:72vh;border-radius:12px} .wg-legend button{flex:none}
 .wg-top h1{font-size:25px} .wg .node text{font-size:13px} .wg-form{flex-basis:100%;margin-left:0}}
"""

JS = r"""
(() => {
const LABELS = %LABELS%, svg = document.getElementById('wg'), tip = document.querySelector('.wg-tip');
const NS = 'http://www.w3.org/2000/svg', hidden = new Set(), pos = new Map();
const RING = {f4: 120, f3: 150, f2: 175, f1: 195};
let data = %DATA%, nodes = [], ties = [], alpha = 0, frame = 0, view = {x: -450, y: -300, w: 900, h: 600};
const el = (tag, attrs = {}, parent) => { const e = document.createElementNS(NS, tag);
  for (const k in attrs) e.setAttribute(k, attrs[k]); if (parent) parent.appendChild(e); return e; };
const color = k => `var(--k-${k})`;
const num = n => n.toLocaleString('ka-GE').replace(/[,\u00a0]/g, ' ');

function build(origin) {
  svg.textContent = ''; svg.classList.remove('focus');
  const gEdges = el('g', {}, svg), gTies = el('g', {}, svg), gNodes = el('g', {}, svg);
  const shown = data.nodes.filter(n => !hidden.has(n.kind)), small = isTall();
  const order = ['f4', 'f3', 'f2', 'f1'];  // one colour stays together on the arc
  const fam = shown.filter(n => n.kind !== 'near').sort((a, b) => order.indexOf(a.kind) - order.indexOf(b.kind))
    .slice(0, small ? 12 : 99);
  const near = shown.filter(n => n.kind === 'near').slice(0, small ? 12 : 99);  // a phone shows the closest only
  const sims = near.map(n => n.sim), lo = Math.min(...sims), hi = Math.max(...sims);
  const center = {w: data.word, kind: 'center', count: data.count, r: 0, home: 0};
  // form on the left, meaning on the right: each group fans out over its own half
  fam.forEach((n, i) => { n.r = RING[n.kind] || 160; n.home = Math.PI * (0.62 + 0.76 * (i + .5) / fam.length); });
  near.forEach((n, i) => { n.r = 190 + 170 * (1 - (n.sim - lo) / ((hi - lo) || 1));
    n.home = Math.PI * (-0.42 + 0.84 * (i + .5) / near.length); });
  nodes = [center, ...fam, ...near];
  const o = origin || {x: 0, y: 0};
  for (const n of nodes) {
    const p = pos.get(n.w);
    n.x = p ? p.x : o.x + Math.cos(n.home) * 30; n.y = p ? p.y : o.y + Math.sin(n.home) * 30;
    n.vx = n.vy = 0; n.fixed = false;
    if (n.kind !== 'center') {
      n.edge = el('path', {class: 'edge', stroke: color(n.kind),
        'stroke-width': 0.8 + 2.2 * Math.max(n.sim || 0.3, 0), opacity: 0.25 + 0.55 * Math.max(n.sim || 0.3, 0)}, gEdges);
    }
    const same = n.kind !== 'center' && n.opens === false;
    const g = n.g = el('g', {class: `node ${n.kind}${same ? ' same' : ''}${p ? '' : ' enter'}`, tabindex: 0, role: 'button',
                             'aria-label': n.w, style: `--c:${color(n.kind)}`}, gNodes);
    const rect = el('rect', {rx: n.kind === 'center' ? 20 : 15}, g), text = el('text', {}, g);
    text.textContent = n.w;
    const w = text.getComputedTextLength() + (n.kind === 'center' ? 40 : 26), h = n.kind === 'center' ? 40 : 30;
    Object.assign(n, {bw: w, bh: h});
    rect.setAttribute('x', -w / 2); rect.setAttribute('y', -h / 2);
    rect.setAttribute('width', w); rect.setAttribute('height', h);
    bind(n);
  }
  for (const n of nodes) n.tx = n.ty = null;
  if (small) { flow(fam, -1); flow(near, 1); }
  const byWord = new Map(nodes.map(n => [n.w, n]));
  ties = data.links.map(([a, b]) => [byWord.get(a), byWord.get(b)]).filter(([a, b]) => a && b)
    .map(([a, b]) => ({a, b, line: el('line', {class: 'tie'}, gTies)}));
  requestAnimationFrame(() => svg.querySelectorAll('.enter').forEach(g => {
    g.style.transition = 'opacity .5s'; g.classList.remove('enter'); }));
  heat(1);
}

function flow(list, dir) {  // phone: words in centred rows, form above the centre word, meaning below
  const W = 330, gap = 8, rows = [[]];
  let width = 0;
  for (const n of list) {
    if (width + n.bw > W && rows[rows.length - 1].length) { rows.push([]); width = 0; }
    rows[rows.length - 1].push(n); width += n.bw + gap;
  }
  rows.forEach((row, i) => {
    let x = -(row.reduce((s, n) => s + n.bw + gap, -gap)) / 2;
    for (const n of row) { n.tx = x + n.bw / 2; n.ty = dir * (78 + i * 44); x += n.bw + gap; }
  });
}

function isTall() { const b = svg.getBoundingClientRect(); return b.height > b.width * 1.1; }

function heat(a) { alpha = Math.max(alpha, a); if (!frame) frame = requestAnimationFrame(tick); }

function tick() {
  frame = 0;
  const k = alpha, tall = isTall();
  svg.parentNode.classList.toggle('tall', tall);
  for (const n of nodes) {
    if (n.fixed) continue;
    // wide screen: form left, meaning right (an ellipse); phone: rows from flow()
    const tx = n.tx != null ? n.tx : Math.cos(n.home) * n.r * 1.25, ty = n.ty != null ? n.ty : Math.sin(n.home) * n.r * 0.9;
    if (n.tx != null) { n.vx = (tx - n.x) * 0.16; n.vy = (ty - n.y) * 0.16; continue; }  // rows: glide straight there
    n.vx += (tx - n.x) * 0.02 * k; n.vy += (ty - n.y) * 0.02 * k;
  }
  for (const t of ties) {  // linked synonyms pull together a little
    const dx = t.b.x - t.a.x, dy = t.b.y - t.a.y;
    t.a.vx += dx * 0.004 * k; t.a.vy += dy * 0.004 * k; t.b.vx -= dx * 0.004 * k; t.b.vy -= dy * 0.004 * k;
  }
  if (!tall) for (let i = 0; i < nodes.length; i++) for (let j = i + 1; j < nodes.length; j++) {  // labels must not overlap
    const a = nodes[i], b = nodes[j], dx = b.x - a.x, dy = b.y - a.y;
    const ox = (a.bw + b.bw) / 2 + 10 - Math.abs(dx), oy = (a.bh + b.bh) / 2 + 8 - Math.abs(dy);
    if (ox > 0 && oy > 0) {
      const push = 0.5, sx = Math.sign(dx) || (Math.random() - .5), sy = Math.sign(dy) || (Math.random() - .5);
      if (ox < oy * 2.2) { a.vx -= sx * ox * push; b.vx += sx * ox * push; }
      else { a.vy -= sy * oy * push; b.vy += sy * oy * push; }
    }
  }
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (const n of nodes) {
    if (!n.fixed) { if (n.tx == null) { n.vx *= 0.6; n.vy *= 0.6; } n.x += n.vx; n.y += n.vy; }
    if (n.kind === 'center' && !n.fixed) { n.x *= 0.85; n.y *= 0.85; }
    pos.set(n.w, {x: n.x, y: n.y});
    minX = Math.min(minX, n.x - n.bw / 2); maxX = Math.max(maxX, n.x + n.bw / 2);
    minY = Math.min(minY, n.y - n.bh / 2); maxY = Math.max(maxY, n.y + n.bh / 2);
  }
  fit(minX, minY, maxX, maxY);
  draw();
  alpha *= 0.985;
  if (alpha > 0.02) frame = requestAnimationFrame(tick);
}

function fit(minX, minY, maxX, maxY) {  // keep every word on screen, same aspect as the stage
  const box = svg.getBoundingClientRect(), ratio = box.width / Math.max(box.height, 1), pad = 40;
  let w = maxX - minX + 2 * pad, h = maxY - minY + 2 * pad;
  if (w / h > ratio) h = w / ratio; else w = h * ratio;
  const cx = (minX + maxX) / 2, cy = (minY + maxY) / 2, s = 0.18;
  view = {x: view.x + (cx - w / 2 - view.x) * s, y: view.y + (cy - h / 2 - view.y) * s,
          w: view.w + (w - view.w) * s, h: view.h + (h - view.h) * s};
  svg.setAttribute('viewBox', `${view.x} ${view.y} ${view.w} ${view.h}`);
}

function draw() {
  const c = nodes[0];
  for (const n of nodes) {
    n.g.setAttribute('transform', `translate(${n.x},${n.y})`);
    if (n.edge) {
      const mx = (c.x + n.x) / 2, my = (c.y + n.y) / 2, bend = 0.12;  // a soft curve, not a straight spoke
      n.edge.setAttribute('d', `M${c.x},${c.y} Q${mx - (n.y - c.y) * bend},${my + (n.x - c.x) * bend} ${n.x},${n.y}`);
    }
  }
  for (const t of ties) for (const [k, v] of [['x1', t.a.x], ['y1', t.a.y], ['x2', t.b.x], ['y2', t.b.y]]) t.line.setAttribute(k, v);
}

function point(e) { const p = svg.createSVGPoint(); p.x = e.clientX; p.y = e.clientY; return p.matrixTransform(svg.getScreenCTM().inverse()); }

function bind(n) {
  let start = null, moved = false;
  let slop = 6;
  n.g.addEventListener('pointerdown', e => { start = {x: e.clientX, y: e.clientY}; moved = false;
    slop = e.pointerType === 'touch' ? 14 : 6;  // a finger moves a little on every tap
    n.g.setPointerCapture(e.pointerId); });
  n.g.addEventListener('pointermove', e => {
    if (!start) return;
    if (Math.hypot(e.clientX - start.x, e.clientY - start.y) > slop) moved = true;
    if (moved) { const p = point(e); n.x = p.x; n.y = p.y; n.fixed = true; hideTip(); heat(0.25); }
  });
  n.g.addEventListener('pointerup', () => { if (start && !moved) open(n); start = null; n.fixed = false; heat(0.15); });
  n.g.addEventListener('pointerenter', e => focus(n, e));
  n.g.addEventListener('pointerleave', () => { svg.classList.remove('focus'); hideTip(); });
  n.g.addEventListener('keydown', e => { if (e.key === 'Enter') open(n); });
  n.g.addEventListener('focus', () => focus(n));
}

function focus(n, e) {
  svg.querySelectorAll('.hot').forEach(x => x.classList.remove('hot'));
  svg.classList.add('focus'); n.g.classList.add('hot'); if (n.edge) n.edge.classList.add('hot');
  for (const t of ties) if (t.a === n || t.b === n) {
    t.line.classList.add('hot'); (t.a === n ? t.b : t.a).g.classList.add('hot'); }
  const kind = n.kind === 'center' ? 'დააჭირეთ, რომ მოძებნოთ' : LABELS[n.kind];
  const sim = n.sim != null && n.kind !== 'center' ? ` · მსგავსება ${Math.round(n.sim * 100)}%` : '';
  tip.innerHTML = `<b></b><div class=m>${n.count ? num(n.count) + '-ჯერ ტექსტებში' : ''}${sim}</div><span class=k></span>`;
  tip.querySelector('b').textContent = n.w; tip.querySelector('.k').textContent = kind;
  tip.style.setProperty('--c', color(n.kind));
  const stage = svg.parentNode.getBoundingClientRect(), box = n.g.getBoundingClientRect();
  let left = box.left - stage.left + box.width / 2 - 70, top = box.bottom - stage.top + 10;
  if (top > stage.height - 90) top = box.top - stage.top - 80;
  tip.style.left = Math.max(8, Math.min(left, stage.width - 270)) + 'px'; tip.style.top = top + 'px';
  tip.classList.add('on');
}
function hideTip() { tip.classList.remove('on'); }

async function open(n) {
  if (n.kind === 'center') { location.href = '/?q=' + encodeURIComponent(n.w) + '&from=words'; return; }
  if (n.opens === false) {  // a form of the centre word: point at it, there is no other map
    focus(n); tip.querySelector('.k').textContent = 'იგივე სიტყვაა, სხვა ფორმით';
    const c = nodes[0].g; c.classList.remove('pulse'); void c.getBBox(); c.classList.add('pulse');
    return;
  }
  const r = await fetch('/words.json?w=' + encodeURIComponent(n.w));
  if (!r.ok) return;
  const next = await r.json();
  if (!next) return;
  hideTip();
  data = next; history.pushState(null, '', '/words?w=' + encodeURIComponent(data.word));
  show({x: n.x, y: n.y});
}

function show(origin) {
  for (const k of [...pos.keys()]) if (k !== data.word && !data.nodes.some(n => n.w === k)) pos.delete(k);
  document.title = data.word + ' · ძირკვა';
  document.querySelector('.wg-form input').value = '';
  document.querySelector('.wg-top h1').textContent = data.word;
  document.querySelector('.wg-top .m').textContent = num(data.count) + '-ჯერ ტექსტებში';
  const link = document.querySelector('.wg-search');
  link.href = '/?q=' + encodeURIComponent(data.word) + '&from=words'; link.textContent = `„${data.word}“ ძიებაში →`;
  lists(); build(origin);
}

function lists() {
  const box = document.querySelector('.wg-lists'); box.textContent = '';
  for (const kind of Object.keys(LABELS).filter(k => k !== 'center')) {
    const words = data.nodes.filter(n => n.kind === kind);
    if (!words.length) continue;
    const row = document.createElement('div'); row.className = 'row'; row.style.setProperty('--c', color(kind));
    row.innerHTML = '<div class=cap><i></i></div><div class=rel></div>';
    row.querySelector('.cap').append(LABELS[kind]);
    for (const n of words) {
      const a = document.createElement(n.opens === false ? 'span' : 'a'); a.textContent = n.w;
      if (n.opens !== false) { a.href = '/words?w=' + encodeURIComponent(n.w); a.onclick = e => { e.preventDefault(); open(n); }; }
      row.querySelector('.rel').append(a);
    }
    box.append(row);
  }
}

document.querySelectorAll('.wg-legend button').forEach(b => b.addEventListener('click', () => {
  const k = b.dataset.k; hidden.has(k) ? hidden.delete(k) : hidden.add(k); b.classList.toggle('off'); build();
}));
addEventListener('popstate', async () => {
  const w = new URLSearchParams(location.search).get('w'); if (!w) return;
  const r = await fetch('/words.json?w=' + encodeURIComponent(w)); const next = r.ok && await r.json();
  if (next) { data = next; show(); }
});
let wasTall = isTall();
addEventListener('resize', () => { const t = isTall(); if (t !== wasTall && data) { wasTall = t; build(); } else heat(0.1); });
if (data) { lists(); build(); }
})();
"""


def page(raw: str) -> tuple[str, dict | None]:
    """(body HTML, graph data) for /words?w=raw. The lists under the graph are links, so the page works without JS."""
    from html import escape

    word = resolve(raw) if raw else START[int(time.strftime("%j")) % len(START)]
    data = graph(word) if word else None
    legend = "".join(f"<button type=button data-k={k} style='--c:var(--k-{k})'><i></i>{escape(v)}</button>"
                     for k, v in LABELS.items() if k != "center")
    form = ("<form class=wg-form action=/words><input name=w placeholder='სხვა სიტყვა' aria-label='სიტყვა'>"
            "<button>ნახვა</button></form>")
    if data is None:
        return (f"<div class='wg'><div class=wg-top><div class=wg-title><h1>ვერ ვიპოვეთ</h1></div>{form}</div>"
                f"<p class=wg-empty>სიტყვა „{escape(raw)}“ ჩვენს ტექსტებში საკმარისად ხშირად არ გვხვდება. სცადეთ სხვა "
                "ფორმა ან სხვა სიტყვა.</p></div>"), None
    q = escape(data["word"])
    lists = "".join(
        f"<div class=row style='--c:var(--k-{k})'><div class=cap><i></i>{escape(v)}</div><div class=rel>"
        + "".join(f"<a href='/words?w={escape(n['w'])}'>{escape(n['w'])}</a>" if n["opens"] else f"<span>{escape(n['w'])}</span>"
                  for n in data["nodes"] if n["kind"] == k)
        + "</div></div>"
        for k, v in LABELS.items() if any(n["kind"] == k for n in data["nodes"]))
    count = f"{data['count']:,}".replace(",", " ")
    intro = ("" if raw else "<p class=wg-intro>ერთ მხარეს ჩანს ფორმები და მონათესავეები, მეორეზე მნიშვნელობით ახლოები. "
             "დააჭირეთ ნებისმიერს და მისი რუკა გაიხსნება.</p>")
    return (f"<div class=wg><div class=wg-top><div class=wg-title>"
            f"<h1>{q}</h1><span class=m>{count}-ჯერ ტექსტებში</span></div>{form}</div>{intro}"
            "<div class=wg-stage><span class='wg-side l'>ᲤᲝᲠᲛᲐ</span><span class='wg-side r'>ᲛᲜᲘᲨᲕᲜᲔᲚᲝᲑᲐ</span>"
            "<svg id=wg role=img aria-label='სიტყვების რუკა'></svg><div class=wg-tip></div></div>"
            f"<div class=wg-legend>{legend}</div>"
            f"<p class=wg-hint><a class=wg-search href='/?q={q}&amp;from=words'>„{q}“ ძიებაში →</a></p>"
            f"<div class=wg-lists>{lists}</div></div>"), data


def script(data: dict) -> str:
    return JS.replace("%LABELS%", json.dumps(LABELS, ensure_ascii=False)).replace(
        "%DATA%", json.dumps(data, ensure_ascii=False).replace("</", "<\\/"))
