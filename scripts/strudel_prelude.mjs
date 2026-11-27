// v4.1 strudel_prelude.mjs — helpers anti-erreur injectés dans le scope d'évaluation headless
// (strudel_eval.mjs) : grid(), track(), deg(), glide() (v4.0) + bassline(), kit(),
// .in()/.only()/.muteIn() (v4.1). Rend les fautes usuelles syntaxiquement impossibles ou
// auto-corrigées AVANT le pont Python (0 token LLM) : registre basse [31,48] garanti à la
// source, 1 piste = 1 rôle atomique, présence par section déclarative.
const NOTE_NAMES = ['c', 'c#', 'd', 'd#', 'e', 'f', 'f#', 'g', 'g#', 'a', 'a#', 'b'];
const SCALES = {
  major: [0, 2, 4, 5, 7, 9, 11], ionian: [0, 2, 4, 5, 7, 9, 11],
  minor: [0, 2, 3, 5, 7, 8, 10], aeolian: [0, 2, 3, 5, 7, 8, 10],
  dorian: [0, 2, 3, 5, 7, 9, 10], phrygian: [0, 1, 3, 5, 7, 8, 10],
  mixolydian: [0, 2, 4, 5, 7, 9, 10], harmonic_minor: [0, 2, 3, 5, 7, 8, 11],
  melodic_minor: [0, 2, 3, 5, 7, 9, 11], phrygian_dominant: [0, 1, 4, 5, 7, 8, 10],
  pentatonic_minor: [0, 3, 5, 7, 10], pentatonic_major: [0, 2, 4, 7, 9], blues: [0, 3, 5, 6, 7, 10],
};
const TONIC_PC = { c: 0, 'c#': 1, db: 1, d: 2, 'd#': 3, eb: 3, e: 4, f: 5, 'f#': 6, gb: 6, g: 7, 'g#': 8, ab: 8, a: 9, 'a#': 10, bb: 10, b: 11 };

// Registre basse canonique du pont (miroir de BASS_REGISTER du pont Python)
const BASS_LO = 31, BASS_HI = 48;

// Kits rythmiques embarqués (miroir local de midi_groove_bank) : 1 entrée = 1 rôle atomique.
const KITS = {
  trap_half: { kick: 'bd ~ ~ ~', snare: '~ ~ cp ~', hihat: 'hh*8', perc: '~ ~ ~ ~' },
  trap_full: { kick: 'bd ~ bd ~', snare: '~ ~ cp ~', hihat: 'hh*8', perc: '~ ~ sh ~' },
  boom_bap: { kick: 'bd ~ ~ bd', snare: '~ ~ ~ cp', hihat: 'hh*4 oh', perc: '~ rim ~ ~' },
  house: { kick: 'bd*4', snare: '~ ~ cp ~', hihat: '~ oh ~ oh', perc: '~ ~ ~ ~' },
  drill: { kick: 'bd ~ bd bd', snare: '~ ~ cp cp', hihat: 'hh*16', perc: '~ ~ ~ sh' },
  ambient: { kick: 'bd ~ ~ ~', snare: '~ ~ ~ ~', hihat: 'hh*4', perc: '~ ~ ~ ~' },
};

// Qualités d'accords -> intervalles depuis la fondamentale (pour snaper la basse sur la grille)
const CHORD_QUALITIES = {
  '': [0, 4, 7], maj: [0, 4, 7], maj7: [0, 4, 7, 11], 'maj7': [0, 4, 7, 11], M7: [0, 4, 7, 11],
  m: [0, 3, 7], min: [0, 3, 7], m7: [0, 3, 7, 10], min7: [0, 3, 7, 10], '-7': [0, 3, 7, 10],
  7: [0, 4, 7, 10], dom7: [0, 4, 7, 10], '7': [0, 4, 7, 10],
  dim: [0, 3, 6], '°': [0, 3, 6], aug: [0, 4, 8], '+': [0, 4, 8],
  sus4: [0, 5, 7], sus2: [0, 2, 7], 6: [0, 4, 7, 9], m6: [0, 3, 7, 9],
  'm(maj7)': [0, 3, 7, 11], mM7: [0, 3, 7, 11], 9: [0, 4, 7, 10, 2], m9: [0, 3, 7, 10, 2],
};

export function midiToName(m) {
  const mm = Math.round(m);
  return NOTE_NAMES[((mm % 12) + 12) % 12] + (Math.floor(mm / 12) - 1);
}

export function keyToTonicMidi(key, octave = 4) {
  const m = /^([a-gA-G])([#b]?)/.exec(String(key || 'C').trim());
  const pc = m ? (TONIC_PC[(m[1] + (m[2] || '')).toLowerCase()] ?? 0) : 0;
  return pc + 12 * (octave + 1);
}

export function degToMidi(degToken, tonicMidi, scale) {
  const d = parseInt(degToken, 10);
  const oct = Math.floor(d / scale.length);
  return tonicMidi + 12 * oct + scale[((d % scale.length) + scale.length) % scale.length];
}

export function foldBass(midi) {
  let p = Math.round(midi);
  while (p < BASS_LO) p += 12;
  while (p > BASS_HI) p -= 12;
  return p;
}

export function parseChordPcs(sym) {
  const m = /^([A-Ga-g])([#b]?)(.*)$/.exec(String(sym).trim());
  if (!m) return null;
  const root = TONIC_PC[(m[1] + (m[2] || '')).toLowerCase()];
  if (root === undefined) return null;
  const q = CHORD_QUALITIES[m[3]] || CHORD_QUALITIES[''] || [0, 4, 7];
  return q.map((i) => (root + i) % 12);
}

// Parse une séquence de degrés ("1 ~ [3 5] <7 1>") -> liste de tokens midi|null (rest)
function parseDegSeq(src, tonic, scale) {
  const out = [];
  let i = 0;
  while (i < src.length) {
    const ch = src[i];
    if (/\s/.test(ch)) { i++; continue; }
    if (ch === '~') { out.push(null); i++; continue; }
    if (ch === '<') {
      let j = src.indexOf('>', i);
      const inner = src.slice(i + 1, j < 0 ? src.length : j);
      for (const t of inner.split(/\s+/).filter(Boolean)) out.push(t === '~' ? null : degToMidi(t, tonic, scale));
      i = (j < 0 ? src.length : j + 1);
      continue;
    }
    if (ch === '[') {
      let j = src.indexOf(']', i);
      const inner = src.slice(i + 1, j < 0 ? src.length : j);
      const ms = inner.split(/\s+/).filter(Boolean).map((t) => degToMidi(t, tonic, scale));
      out.push(ms); // accord (liste)
      i = (j < 0 ? src.length : j + 1);
      continue;
    }
    let j = i;
    while (j < src.length && !/[\s~\[\]<]/.test(src[j])) j++;
    out.push(degToMidi(src.slice(i, j), tonic, scale));
    i = j;
  }
  return out;
}

function seqToNoteString(tokens) {
  // tokens: (number|null|number[]) par pas ; rend une mini-notation note("...")
  const parts = [];
  for (const tk of tokens) {
    if (tk === null) parts.push('~');
    else if (Array.isArray(tk)) parts.push('[' + tk.map(midiToName).join(',') + ']');
    else parts.push(midiToName(tk));
  }
  return parts.join(' ');
}

export function installPrelude(core, meta) {
  const g = globalThis;
  g.__strudel_meta = meta || {};
  g.__strudel_grid = null;
  g.__strudel_track_roles = {};
  g.__strudel_kit = {};
  const kitMarker = new WeakSet();
  g.__kitMarker = kitMarker;
  const sectionMap = new WeakMap();
  g.__sectionMap = sectionMap;
  // v4.3 (D5) — pont vers les moteurs narratifs v3.2/v3.3 (NarrativeHarmony, transitions_lib,
  // midi_groove_bank, orchestration_engine) : métadonnées déclaratives exportées dans le Haps JSON.
  g.__strudel_narrative = { section_grids: {}, transitions: [], groove_id: null, comping_by_section: {} };
  g.__strudel_lifts = new WeakMap();

  // v4.3 (D5) — progressions narratives par section : compile vers harmonic_core.section_progressions.
  // 1 symbole d'accord = 1 mesure ; chaque section suit sa propre progression (repli `default`).
  g.sectionGrid = (spec) => {
    const m = g.__strudel_meta || {};
    const secs = Array.isArray(m.sections) && m.sections.length ? m.sections : [{ name: 'song', bars: 16 }];
    const syms = [];
    const sectionGrids = {};
    for (const s of secs) {
      const rawKey = String(s.name || 'song');
      const key = rawKey.toLowerCase();
      const list = (spec && (spec[key] || spec[rawKey] || spec.default)) || '';
      const arr = String(list).trim().split(/\s+/).filter(Boolean);
      sectionGrids[key] = arr;
      const nb = Math.max(1, Number(s.bars || 4));
      for (let b = 0; b < nb; b++) syms.push(arr.length ? arr[b % arr.length] : 'Cm7');
    }
    g.__strudel_grid = syms;
    g.__strudel_narrative.section_grids = sectionGrids;
    return core.chord('<' + syms.join(' ') + '>');
  };

  // v4.3 (D5) — transition déclarative (ex. transition("before:chorus", "drum_fill_and_riser", {style:"snare_roll"})).
  // Effet de bord uniquement (compile vers arrangement_plan.transitions[]) : n'émet aucune piste.
  g.transition = (where, kind, opts = {}) => {
    g.__strudel_narrative.transitions.push({ where: String(where), kind: String(kind), opts: opts || {} });
    return core.s('~ ~ ~ ~');
  };

  g.grid = (symbols) => {
    const arr = String(symbols).trim().split(/\s+/).filter(Boolean);
    g.__strudel_grid = arr;
    return core.chord('<' + arr.join(' ') + '>');
  };

  g.track = (role, pat) => {
    g.__strudel_track_roles[String(role).toLowerCase()] = true;
    return pat;
  };

  g.deg = (seq, octave) => {
    const m = g.__strudel_meta || {};
    const tonic = keyToTonicMidi(m.key || 'C', octave !== undefined ? octave : 4);
    const scale = SCALES[String(m.mode || 'minor').toLowerCase()] || SCALES.minor;
    return core.note(seqToNoteString(parseDegSeq(String(seq).trim(), tonic, scale)));
  };

  // v4.1 — basse en degrés, snappée sur la grille __strudel_grid, pliée dans [31,48] à la source
  g.bassline = (seq, opts = {}) => {
    const m = g.__strudel_meta || {};
    const tonic = keyToTonicMidi(m.key || 'C', 2); // octave 2 -> fondamentale dans [36,47]
    const scale = SCALES[String(m.mode || 'minor').toLowerCase()] || SCALES.minor;
    const mode = String(opts.mode || 'stepwise');
    g.__strudel_last_bass_mode = mode;
    const gridSyms = Array.isArray(g.__strudel_grid) ? g.__strudel_grid : null;
    let tokens = parseDegSeq(String(seq).trim(), tonic, scale);
    if (mode === 'root' && gridSyms && gridSyms.length) {
      // chaque pas non-repos = fondamentale de l'accord de la mesure courante, pliée [31,48]
      const nSteps = tokens.length || 1;
      tokens = tokens.map((tk, idx) => {
        if (tk === null) return null;
        const barIdx = Math.floor((idx / Math.max(1, nSteps)) * gridSyms.length) % gridSyms.length;
        const pcs = parseChordPcs(gridSyms[barIdx]);
        const rootPc = pcs ? pcs[0] : tonic % 12;
        let p = rootPc + 12 * 3; // octave 3 -> 36..47
        return foldBass(p);
      });
    } else {
      tokens = tokens.map((tk) => (tk === null ? null : Array.isArray(tk) ? tk.map(foldBass) : foldBass(tk)));
    }
    let pat = core.note(seqToNoteString(tokens));
    const glideMs = Number(opts.glide || 0);
    if (glideMs > 0 && typeof pat.legato === 'function') pat = pat.legato(Math.min(2.0, Math.max(0.5, glideMs / 300.0)));
    return pat;
  };

  // v4.1 — kit atomique : enregistre $kick/$snare/$hihat/$perc séparés (1 piste = 1 rôle)
  g.kit = (name, opts = {}) => {
    const spec = KITS[String(name)] || KITS.trap_half;
    for (const [role, patStr] of Object.entries(spec)) {
      let p = core.s(patStr);
      if (opts.hats === 'rolls' && role === 'hihat') p = core.s('hh*16').sometimesBy(0.3, (x) => x.degradeBy(0.4));
      if (opts.var) p = p.sometimesBy(Math.min(0.5, Number(opts.var) / 10), (x) => x.degradeBy(0.5));
      g.__strudel_kit[role] = p;
    }
    // v4.3 (D5) — banque de grooves minés localement (listés par studio_prepare) :
    // kit("trap_half", { groove: "trap_dark_03", var: 2, hats: "rolls" })
    if (opts.groove) g.__strudel_narrative.groove_id = String(opts.groove);
    const dummy = core.s('~ ~ ~ ~');
    try { kitMarker.add(dummy); } catch { /* WeakSet ok */ }
    return dummy; // si kit() est appelé dans une pile $, celle-ci est ignorée (marqueur)
  };

  g.glide = (ms) => (p) => (typeof p.legato === 'function' ? p.legato(Math.min(2.0, Math.max(0.5, ms / 300.0))) : p);

  try {
    const proto = Object.getPrototypeOf(core.note('c4'));
    if (proto) {
      if (!proto.glide) {
        proto.glide = function (ms) {
          return typeof this.legato === 'function' ? this.legato(Math.min(2.0, Math.max(0.5, (ms || 60) / 300.0))) : this;
        };
      }
      // v4.1 — présence par section (macro-forme déclarative), lue par strudel_eval.mjs.
      // NB : strudel a DEJA une méthode .in (setter de contrôle) -> on la surcharge en détectant
      // une liste de noms de sections ; sinon on retombe sur l'originale.
      const SECTION_NAMES = new Set(['intro', 'verse', 'pre_chorus', 'prechorus', 'pre-refrain', 'chorus', 'refrain', 'drop', 'hook', 'climax', 'bridge', 'pont', 'outro', 'break', 'breakdown', 'couplet', 'song']);
      const isSectionList = (list) => {
        const names = String(list).split(',').map((s) => s.trim().toLowerCase()).filter(Boolean);
        return names.length > 0 && names.every((n) => SECTION_NAMES.has(n));
      };
      const defineSec = (key, mode, original) => {
        proto[key] = function (list) {
          if (isSectionList(list)) {
            sectionMap.set(this, { mode, names: String(list).split(',').map((s) => s.trim().toLowerCase()).filter(Boolean) });
            return this;
          }
          return original ? original.call(this, list) : this;
        };
      };
      defineSec('in', 'in', proto.in);
      defineSec('only', 'only', proto.only);
      defineSec('muteIn', 'mute', proto.muteIn);
      // v4.3 (D5) — .lift("chorus", { oct: 12, energy: "+1.5dB" }) : variation structurée par
      // section (anti-clone 8 mesures), compilée vers arrangement_plan role_modifiers.
      if (!proto.lift) {
        proto.lift = function (section, opts = {}) {
          try {
            const cur = g.__strudel_lifts.get(this) || [];
            cur.push(Object.assign({ section: String(section) }, opts || {}));
            g.__strudel_lifts.set(this, cur);
          } catch { /* WeakMap indisponible */ }
          return this;
        };
      }
      // v4.3 (D5) — .comping({ verse: "pad_hold", chorus: "syncopated_stab" }) après sectionGrid :
      // style de comping par section (orchestration_engine), exporté dans le Haps JSON.
      if (!proto.comping) {
        proto.comping = function (map) {
          g.__strudel_narrative.comping_by_section = map || {};
          return this;
        };
      }
    }
  } catch { /* glide() curried reste disponible */ }

  return g;
}
