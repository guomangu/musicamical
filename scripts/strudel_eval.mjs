#!/usr/bin/env node
// v4.0 — Evaluateur Strudel 100 % headless (Node.js pur, queryArc, 0 Chromium, 0 temps reel).
// Usage : node scripts/strudel_eval.mjs <composition.strudel> [--bars N] [--seed S] [--out haps.json] [--compact]
import { register } from 'node:module';
import fs from 'node:fs';
import path from 'node:path';

register('./strudel_loader.mjs', import.meta.url);

const { lintSource } = await import('./strudel_lint.mjs');
const { installPrelude } = await import('./strudel_prelude.mjs');

const args = process.argv.slice(2);
function argValue(flag) {
  const i = args.indexOf(flag);
  return i >= 0 ? args[i + 1] : undefined;
}
const file = args.find((a, i) => !a.startsWith('--') && args[i - 1] !== '--bars' && args[i - 1] !== '--seed' && args[i - 1] !== '--out');
const barsArg = Number(argValue('--bars') || 0);
const seedArg = argValue('--seed');
const outPath = argValue('--out');
const compact = args.includes('--compact');

if (!file) {
  console.error('usage: node strudel_eval.mjs <composition.strudel> [--bars N] [--seed S] [--out haps.json] [--compact]');
  process.exit(2);
}

const src = fs.readFileSync(file, 'utf8');

const lintErrors = lintSource(src);
if (lintErrors.length) {
  console.error(lintErrors.join('\n'));
  process.exit(4);
}

function extractMeta(source) {
  const lines = source.split('\n');
  const meta = {};
  let collecting = false;
  let buf = '';
  for (const ln of lines) {
    const t = ln.trim();
    if (!collecting && t.startsWith('//') && t.includes('@strudel-meta')) {
      collecting = true;
      buf = t.replace(/^\/\/\s*@strudel-meta/, '').trim();
      if (buf.startsWith('{')) {
        let depth = 0;
        for (const ch of buf) { if (ch === '{') depth++; if (ch === '}') depth--; }
        if (depth === 0) { try { Object.assign(meta, JSON.parse(buf)); } catch {} collecting = false; buf = ''; continue; }
      }
      continue;
    }
    if (collecting) {
      buf += '\n' + t.replace(/^\/\//, '').trim();
      let depth = 0;
      for (const ch of buf) { if (ch === '{') depth++; if (ch === '}') depth--; }
      if (depth <= 0 && buf.includes('{')) {
        try { Object.assign(meta, JSON.parse(buf)); } catch {}
        collecting = false;
      }
      continue;
    }
  }
  return meta;
}

function splitStacks(source) {
  const lines = source.split('\n');
  const stacks = [];
  let current = null;
  let sawMeta = false;
  for (const ln of lines) {
    const t = ln.trim();
    if (t.startsWith('//')) {
      if (t.includes('@strudel-meta')) sawMeta = true;
      continue;
    }
    if (!t) continue;
    const m = t.match(/^\$([A-Za-z0-9_]*)\s*:\s*(.*)$/);
    if (m) {
      current = { name: m[1] || '$', expr: m[2] };
      stacks.push(current);
    } else if (current) {
      current.expr += ' ' + t;
    } else {
      current = { name: '$', expr: t };
      stacks.push(current);
    }
  }
  return stacks.filter((s) => s.expr.trim().length > 0);
}

let core;
let mini;
let tonal = null;
console.log = (...a) => console.error(...a);
try {
  core = await import('@strudel/core');
  mini = await import('@strudel/mini');
  tonal = await import('@strudel/tonal');
} catch (err) {
  console.error(`[strudel_eval] dependance @strudel/* manquante : lance \`npm install\` dans scripts/ (scripts/package.json). ${err.message}`);
  process.exit(3);
}

const { evalScope, evaluate, setStringParser, seed, noteToMidi } = core;
await evalScope(core, mini, ...(tonal ? [tonal] : []));
setStringParser(mini.minify);
const meta0 = extractMeta(src);
installPrelude(core, meta0);

const meta = extractMeta(src);
const seedVal = Number(seedArg ?? meta.seed ?? 42);
const ts = Array.isArray(meta.time_signature) ? meta.time_signature : [4, 4];
const beatsPerBar = Number(ts[0] || 4);
let sections = Array.isArray(meta.sections) && meta.sections.length ? meta.sections : null;
let bars = barsArg || Number(meta.bars || 0);
if (!bars && sections) bars = sections.reduce((a, s) => a + Number(s.bars || 0), 0);
if (!bars) bars = 16;

const NOTE_RE = /^[a-gA-G][#bsf]*-?\d*$/;
function midiOf(n) {
  if (typeof n === 'number') return Math.round(n);
  if (typeof n === 'string' && NOTE_RE.test(n)) {
    try { return noteToMidi(n); } catch { return null; }
  }
  return null;
}

const NUMERIC_CONTROLS = ['gain', 'pan', 'sustain', 'legato', 'attack', 'release', 'speed', 'octave', 'lpf', 'cutoff', 'hpf'];

function extractHap(h, trackAutos) {
  const v = h.value || {};
  const ev = {
    start: Number(h.whole.begin),
    end: Math.max(Number(h.whole.end), Number(h.part && h.part.end !== undefined ? h.part.end : h.whole.end)),
  };
  if (v.note !== undefined) {
    const arr = Array.isArray(v.note) ? v.note : [v.note];
    const notes = arr.map(midiOf).filter((n) => n !== null);
    if (notes.length) ev.notes = notes;
  }
  if (v.chord !== undefined) ev.chord = String(v.chord);
  const s = v.s !== undefined ? v.s : v.sound;
  if (s !== undefined) ev.s = String(s).split(':')[0];
  for (const k of NUMERIC_CONTROLS) {
    const c = v[k];
    if (typeof c === 'number') ev[k] = c;
    else if (c && typeof c.queryArc === 'function') {
      if (!trackAutos[k]) trackAutos[k] = c;
    }
  }
  return ev;
}

async function evalStack(stack) {
  seed(seedVal);
  const r = await evaluate(stack.expr);
  const pattern = r.pattern;
  if (!pattern || typeof pattern.queryArc !== 'function') {
    throw new Error(`piste "$${stack.name}" : l'expression ne produit pas un Pattern (got ${typeof pattern})`);
  }
  seed(seedVal);
  const haps = pattern.queryArc(0, bars);
  const autos = {};
  const seen = new Set();
  const events = [];
  for (const h of haps) {
    const ev = extractHap(h, autos);
    if (ev.end <= ev.start + 1e-9) continue;
    const key = `${ev.start}|${ev.end}|${JSON.stringify(ev.notes || ev.chord || ev.s || '')}|${ev.gain ?? ''}`;
    if (seen.has(key)) continue;
    seen.add(key);
    events.push(ev);
  }
  const automations = {};
  for (const [k, sig] of Object.entries(autos)) {
    seed(seedVal);
    automations[k] = sig.queryArc(0, bars)
      .filter((h) => typeof h.value === 'number')
      .map((h) => ({ t: Number(h.whole.begin), v: Number(h.value) }));
  }
  return { events, automations };
}

const stacks = splitStacks(src);
if (!stacks.length) {
  console.error('[strudel_eval] aucune piste `$nom: ...` trouvée dans le fichier');
  process.exit(2);
}

const tracks = {};
const errors = [];
for (const st of stacks) {
  try {
    tracks[st.name] = await evalStack(st);
  } catch (err) {
    errors.push(`$${st.name}: ${err.message}`);
  }
}

const chordTrackName = Object.keys(tracks).find((n) => /chord|harmony/i.test(n));
const progression = new Array(bars).fill(null);
if (chordTrackName) {
  let last = null;
  const evs = tracks[chordTrackName].events.filter((e) => e.chord);
  for (let c = 0; c < bars; c++) {
    const hit = evs.find((e) => e.start <= c + 1e-6 && e.end > c + 1e-6);
    if (hit) last = hit.chord;
    progression[c] = last;
  }
} else if (globalThis.__strudel_grid && globalThis.__strudel_grid.length) {
  const gridSyms = globalThis.__strudel_grid;
  let last = null;
  for (let c = 0; c < bars; c++) {
    const sym = gridSyms[c % gridSyms.length];
    if (sym) last = sym;
    progression[c] = last;
  }
}
if (!sections) sections = [{ name: 'song', bars }];

const output = {
  schema_version: '4.0',
  source: path.basename(file),
  meta: {
    bpm: Number(meta.bpm ?? 120),
    key: String(meta.key ?? 'C'),
    mode: String(meta.mode ?? 'minor'),
    time_signature: { numerator: beatsPerBar, denominator: Number(ts[1] || 4) },
    seed: seedVal,
    sections,
    progression,
    mix_profile: meta.mix_profile ? String(meta.mix_profile) : undefined,
    valence: meta.valence !== undefined ? Number(meta.valence) : undefined,
  },
  bars,
  beats_per_bar: beatsPerBar,
  seed: seedVal,
  tracks,
  errors,
};

const json = JSON.stringify(output, null, compact ? 0 : 2);
if (outPath) {
  fs.writeFileSync(outPath, json + '\n');
  console.error(`[strudel_eval] ${Object.keys(tracks).length} pistes, ${bars} mesures -> ${outPath}`);
} else {
  process.stdout.write(json + '\n');
}
if (errors.length) {
  console.error('[strudel_eval] erreurs: ' + errors.join(' | '));
  process.exit(4);
}
