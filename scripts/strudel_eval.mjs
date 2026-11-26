#!/usr/bin/env node
// v4.0 — Evaluateur Strudel 100 % headless (Node.js pur, queryArc, 0 Chromium, 0 temps reel).
// Usage : node scripts/strudel_eval.mjs <composition.strudel> [--bars N] [--seed S] [--out haps.json] [--compact]
import { register } from 'node:module';
import { createHash } from 'node:crypto';
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

const FORM_SECTION = { i: 'intro', v: 'verse', p: 'pre_chorus', c: 'chorus', b: 'bridge', o: 'outro', d: 'drop' };
function parseMetaLine(body, meta) {
  for (const tok of body.split(/\s+/).filter(Boolean)) {
    let m;
    if ((m = /^(\d{2,3})$/.exec(tok))) meta.bpm = Number(m[1]);
    else if ((m = /^([A-Ga-g][#b]?):(\w+)$/.exec(tok))) { meta.key = m[1][0].toUpperCase() + m[1].slice(1); meta.mode = m[2]; }
    else if ((m = /^V=(-?\d+(?:\.\d+)?)$/.exec(tok))) meta.valence = Number(m[1]);
    else if ((m = /^seed=(\d+)$/.exec(tok))) meta.seed = Number(m[1]);
    else if ((m = /^ts=(\d+)\/(\d+)$/.exec(tok))) meta.time_signature = [Number(m[1]), Number(m[2])];
    else if ((m = /^form=([A-Za-z]\d+(?:-[A-Za-z]\d+)*)$/.exec(tok))) {
      const secs = [];
      let bar = 0;
      for (const part of m[1].split('-')) {
        const mm = /^([A-Za-z])(\d+)$/.exec(part);
        if (!mm) continue;
        const name = FORM_SECTION[mm[1].toLowerCase()] || ('section' + bar);
        const nb = Number(mm[2]);
        secs.push({ name, bars: nb });
        bar += nb;
      }
      if (secs.length) meta.sections = secs;
    } else if (/^[a-z][a-z0-9_]*$/.test(tok) && meta.mix_profile === undefined) meta.mix_profile = tok;
  }
  return meta;
}

function extractMeta(source) {
  const lines = source.split('\n');
  const meta = {};
  let collecting = false;
  let buf = '';
  for (const ln of lines) {
    const t = ln.trim();
    if (!collecting && t.startsWith('//') && t.includes('@meta') && !t.includes('@strudel-meta')) {
      parseMetaLine(t.replace(/^\/\/\s*@meta/, '').trim(), meta);
      continue;
    }
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
  const prelude = [];
  let current = null;
  for (const ln of lines) {
    const t = ln.trim();
    if (t.startsWith('//')) continue;
    if (!t) continue;
    const m = t.match(/^\$([A-Za-z0-9_]*)\s*:\s*(.*)$/);
    if (m) {
      current = { name: m[1] || '$', expr: m[2] };
      stacks.push(current);
    } else if (current) {
      current.expr += ' ' + t;
    } else {
      prelude.push(t); // statement de prélude (ex. kit("trap_half")) : effet de bord, pas une piste
    }
  }
  const clean = stacks.filter((s) => s.expr.trim().length > 0);
  if (!clean.length && prelude.length) clean.push({ name: '$', expr: prelude.join(' ') }); // legacy : fichier sans $
  return { prelude, stacks: clean };
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
const seedVal = Number(seedArg ?? meta.seed ?? (parseInt(createHash('sha256').update(src).digest('hex').slice(0, 8), 16) % 1000003));
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

function eventsOf(pattern) {
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

function isKitDummy(pattern) {
  return Boolean(pattern && ((globalThis.__kitMarker && globalThis.__kitMarker.has(pattern)) || pattern.__aiStudioKit));
}

function sectionOf(pattern) {
  const sm = globalThis.__sectionMap;
  return sm && pattern ? sm.get(pattern) || null : null;
}

async function evalStack(stack) {
  seed(seedVal);
  globalThis.__strudel_last_bass_mode = null;
  const r = await evaluate(stack.expr);
  const pattern = r.pattern;
  if (!pattern || typeof pattern.queryArc !== 'function') {
    throw new Error(`piste "$${stack.name}" : l'expression ne produit pas un Pattern (got ${typeof pattern})`);
  }
  if (isKitDummy(pattern)) return null; // pile englobante d'un kit() : pas une piste
  const out = eventsOf(pattern);
  const sec = sectionOf(pattern);
  if (sec) out.sections = sec;
  if (globalThis.__strudel_last_bass_mode) out.bass_mode = globalThis.__strudel_last_bass_mode;
  return out;
}

const { prelude, stacks } = splitStacks(src);
if (!stacks.length && !prelude.length) {
  console.error('[strudel_eval] aucune piste `$nom: ...` trouvée dans le fichier');
  process.exit(2);
}

const tracks = {};
const errors = [];
for (const stmt of prelude) {
  try { seed(seedVal); await evaluate(stmt); } catch (err) { errors.push(`prelude: ${err.message}`); }
}
for (const st of stacks) {
  try {
    const out = await evalStack(st);
    if (out) tracks[st.name] = out;
  } catch (err) {
    errors.push(`$${st.name}: ${err.message}`);
  }
}
// v4.1 : pistes atomiques enregistrées par kit() (1 rôle = 1 piste, hors piles $)
for (const [role, pat] of Object.entries(globalThis.__strudel_kit || {})) {
  if (tracks[role]) continue;
  try {
    seed(seedVal);
    tracks[role] = eventsOf(pat);
  } catch (err) {
    errors.push(`kit:${role}: ${err.message}`);
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
  schema_version: '4.1',
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
