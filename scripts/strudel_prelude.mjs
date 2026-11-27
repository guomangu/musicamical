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

// Kits rythmiques pro 2 mesures (1 entrée = 1 rôle atomique, vrai bounce + perc + open_hat).
// Jamais de `hh*16` continu sur tout le morceau ni de `perc` vide !
const KITS = {
  trap_half: {
    kick: '<[bd ~ ~ ~ ~ ~ ~ bd ~ ~ bd ~ ~ ~ ~ ~] [bd ~ ~ ~ ~ ~ bd ~ ~ ~ bd ~ ~ ~ ~ ~]>',
    snare: '<[~ ~ cp ~] [~ ~ cp ~]>',
    hihat: '<[hh hh hh hh hh hh hh hh] [hh hh hh [hh*2] hh hh hh hh]>',
    hihat_rolls: '<[hh hh hh [hh*3] hh hh hh hh] [hh hh hh hh hh [hh*3] hh [hh*4]]>',
    open_hat: '<[~ ~ ~ ~ ~ oh ~ ~] [~ ~ ~ ~ ~ oh ~ ~]>',
    perc: '<[~ ~ ~ rim ~ ~ ~ ~] [~ ~ ~ ~ ~ rim ~ ~]>',
  },
  trap_full: {
    kick: '<[bd ~ ~ bd ~ ~ bd ~] [bd ~ ~ bd ~ ~ ~ bd]>',
    snare: '<[~ ~ cp ~] [~ ~ cp ~]>',
    hihat: '<[hh hh hh hh hh hh hh hh] [hh hh [hh*2] hh hh hh hh hh]>',
    hihat_rolls: '<[hh hh [hh*3] hh hh hh hh [hh*2]] [hh hh hh [hh*3] hh hh [hh*4] hh]>',
    open_hat: '<[~ ~ ~ oh ~ ~ ~ ~] [~ ~ ~ oh ~ ~ ~ oh]>',
    perc: '<[~ ~ sh ~ ~ ~ sh ~] [~ ~ sh ~ ~ sh ~ ~]>',
  },
  boom_bap: {
    kick: '<[bd ~ ~ ~ ~ ~ ~ bd ~ ~ bd ~ ~ ~ ~ ~] [bd ~ ~ ~ ~ ~ bd ~ ~ ~ bd ~ ~ bd ~ ~]>',
    snare: '<[~ cp ~ cp] [~ cp ~ cp]>',
    hihat: '<[hh hh hh hh hh hh hh hh] [hh hh hh hh hh hh hh hh]>',
    hihat_rolls: '<[hh hh hh hh hh hh [hh*2] hh] [hh hh hh [hh*2] hh hh hh hh]>',
    open_hat: '<[~ ~ ~ ~ ~ ~ ~ oh] [~ ~ ~ ~ ~ ~ ~ oh]>',
    perc: '<[~ rim ~ ~ ~ ~ rim ~] [~ ~ rim ~ ~ ~ ~ rim]>',
  },
  house: {
    kick: 'bd*4',
    snare: '~ cp ~ cp',
    hihat: '<[~ hh ~ hh ~ hh ~ hh] [~ hh ~ hh ~ hh [hh*2] hh]>',
    hihat_rolls: '<[~ hh ~ hh ~ hh ~ hh] [~ hh ~ [hh*2] ~ hh [hh*2] hh]>',
    open_hat: '~ oh ~ oh',
    perc: '<[~ ~ ~ rim ~ ~ rim ~] [~ ~ rim ~ ~ ~ ~ rim]>',
  },
  drill: {
    kick: '<[bd ~ ~ ~ ~ ~ ~ ~ ~ ~ bd ~ ~ ~ ~ ~] [bd ~ ~ ~ ~ ~ bd ~ ~ ~ ~ ~ ~ bd ~ ~]>',
    snare: '<[~ ~ ~ ~ ~ ~ cp ~ ~ ~ ~ ~ ~ ~ ~ cp] [~ ~ ~ ~ ~ ~ cp ~ ~ ~ ~ ~ ~ cp ~ ~]>',
    hihat: '<[hh ~ ~ hh ~ ~ hh ~ hh ~ ~ hh ~ ~ hh ~] [hh ~ ~ hh ~ ~ hh ~ hh ~ hh ~ hh ~ ~ hh]>',
    hihat_rolls: '<[hh ~ ~ hh ~ ~ [hh*3] ~ hh ~ ~ hh ~ ~ hh ~] [hh ~ ~ hh ~ ~ hh ~ hh ~ [hh*3] ~ hh ~ [hh*2] hh]>',
    open_hat: '<[~ ~ ~ ~ ~ ~ ~ ~ ~ oh ~ ~ ~ ~ ~ ~] [~ ~ ~ ~ ~ ~ ~ ~ ~ oh ~ ~ ~ ~ ~ ~]>',
    perc: '<[~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ sh ~ ~ ~] [~ ~ ~ sh ~ ~ ~ ~ ~ ~ ~ ~ ~ ~ sh ~]>',
  },
  pop: {
    kick: '<[bd ~ ~ bd ~ ~ bd ~] [bd ~ ~ bd ~ bd ~ ~]>',
    snare: '<[~ cp ~ cp] [~ cp ~ cp]>',
    hihat: '<[hh hh hh hh hh hh hh hh] [hh hh hh hh hh hh hh hh]>',
    hihat_rolls: '<[hh hh hh hh hh hh [hh*2] hh] [hh hh hh hh hh [hh*2] hh hh]>',
    open_hat: '<[~ ~ ~ ~ ~ ~ ~ oh] [~ ~ ~ ~ ~ ~ ~ oh]>',
    perc: '<[~ ~ sh ~ ~ ~ sh ~] [~ ~ sh ~ ~ ~ sh ~]>',
  },
  ambient: {
    kick: '<[bd ~ ~ ~] [~ ~ ~ ~]>',
    snare: '<[~ ~ ~ ~] [~ ~ cp ~]>',
    hihat: '<[hh ~ hh ~] [hh ~ ~ hh]>',
    hihat_rolls: '<[hh ~ hh ~] [hh ~ hh [hh*2]]>',
    open_hat: '~ ~ ~ ~',
    perc: '<[~ ~ ~ rim] [~ ~ ~ ~]>',
  },
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

// Parse une séquence de degrés ("0 _ _ ~ 2@2 [3 5] <7 1>") -> liste de tokens {v: midi|null|number[], w: weight}
// Supporte `_` (prolongation de la note précédente, essentiel pour phrasés mélodiques lents/expressifs)
// et `@N` (durée N pas, ex. `0@3 ~ 2@2`).
function parseDegToken(raw, tonic, scale) {
  const m = /^(-?\d+)(?:@(\d+(?:\.\d+)?))?$/.exec(raw);
  if (m) return { v: degToMidi(m[1], tonic, scale), w: m[2] ? Number(m[2]) : 1 };
  return { v: degToMidi(raw, tonic, scale), w: 1 };
}

function parseDegSeq(src, tonic, scale) {
  const out = [];
  let i = 0;
  while (i < src.length) {
    const ch = src[i];
    if (/\s/.test(ch)) { i++; continue; }
    if (ch === '~') {
      let j = i + 1;
      let w = 1;
      if (src[j] === '@') {
        let k = j + 1;
        while (k < src.length && /[\d.]/.test(src[k])) k++;
        w = Number(src.slice(j + 1, k)) || 1;
        j = k;
      }
      out.push({ v: null, w });
      i = j;
      continue;
    }
    if (ch === '_') {
      // Prolonge la note précédente (ou silence si aucun token avant)
      if (out.length) out[out.length - 1].w += 1;
      else out.push({ v: null, w: 1 });
      i++;
      continue;
    }
    if (ch === '<') {
      let j = src.indexOf('>', i);
      const inner = src.slice(i + 1, j < 0 ? src.length : j);
      for (const t of inner.split(/\s+/).filter(Boolean)) {
        if (t === '~') out.push({ v: null, w: 1 });
        else if (t === '_') { if (out.length) out[out.length - 1].w += 1; else out.push({ v: null, w: 1 }); }
        else out.push(parseDegToken(t, tonic, scale));
      }
      i = (j < 0 ? src.length : j + 1);
      continue;
    }
    if (ch === '[') {
      let j = src.indexOf(']', i);
      const inner = src.slice(i + 1, j < 0 ? src.length : j);
      const ms = inner.split(/\s+/).filter(Boolean).map((t) => degToMidi(t, tonic, scale));
      out.push({ v: ms, w: 1 }); // accord (liste)
      i = (j < 0 ? src.length : j + 1);
      continue;
    }
    let j = i;
    while (j < src.length && !/[\s~\[\]<_]/.test(src[j])) j++;
    out.push(parseDegToken(src.slice(i, j), tonic, scale));
    i = j;
  }
  return out;
}

function seqToNoteString(tokens) {
  // tokens: ({v: number|null|number[], w: number}) ou legacy (number|null|number[])
  const parts = [];
  for (const item of tokens) {
    const isObj = item !== null && typeof item === 'object' && !Array.isArray(item) && 'v' in item;
    const tk = isObj ? item.v : item;
    const w = isObj && item.w && item.w !== 1 ? item.w : 1;
    const suffix = w !== 1 ? `@${w}` : '';
    if (tk === null) parts.push('~' + suffix);
    else if (Array.isArray(tk)) parts.push('[' + tk.map(midiToName).join(',') + ']' + suffix);
    else parts.push(midiToName(tk) + suffix);
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

  // v4.4 — arpège harmonique synchronisé sur la grille courante (__strudel_grid) :
  // indices 0=fondamentale, 1=tierce, 2=quinte, 3=septième, 4=octave ; supporte `~` et `_`.
  // Produit 0 clash b2 garanti et enrichit immédiatement l'arrangement (couche $arp / $counter).
  g.arp = (seq, octave = 4) => {
    const m = g.__strudel_meta || {};
    const gridSyms = Array.isArray(g.__strudel_grid) && g.__strudel_grid.length ? g.__strudel_grid : null;
    const raw = String(seq || '0 ~ 2 ~ 1 ~ 2 ~').trim();
    const tonic = keyToTonicMidi(m.key || 'C', octave);
    const scale = SCALES[String(m.mode || 'minor').toLowerCase()] || SCALES.minor;
    if (!gridSyms) return core.note(seqToNoteString(parseDegSeq(raw, tonic, scale)));
    // Parse indices (0..7) + ~ + _
    const rawSteps = [];
    for (const tok of raw.replace(/[<>]/g, ' ').split(/\s+/).filter(Boolean)) {
      if (tok === '~') rawSteps.push({ idx: null, w: 1 });
      else if (tok === '_') { if (rawSteps.length) rawSteps[rawSteps.length - 1].w += 1; else rawSteps.push({ idx: null, w: 1 }); }
      else {
        const mm = /^(-?\d+)(?:@(\d+(?:\.\d+)?))?$/.exec(tok);
        rawSteps.push({ idx: mm ? parseInt(mm[1], 10) : 0, w: mm && mm[2] ? Number(mm[2]) : 1 });
      }
    }
    const baseOct = 12 * (Number(octave) + 1);
    const barsPat = [];
    for (const sym of gridSyms) {
      const pcs = parseChordPcs(sym) || [tonic % 12, (tonic + 3) % 12, (tonic + 7) % 12];
      // Construire une échelle montante des tons de l'accord à partir de la fondamentale
      const rootPc = pcs[0];
      const chordUp = [rootPc];
      for (let k = 1; k < pcs.length; k++) {
        let p = pcs[k];
        while (p <= chordUp[chordUp.length - 1]) p += 12;
        chordUp.push(p);
      }
      const barToks = rawSteps.map((st) => {
        if (st.idx === null) return { v: null, w: st.w };
        const n = chordUp.length;
        const octShift = Math.floor(st.idx / n);
        const ci = ((st.idx % n) + n) % n;
        let midi = baseOct + chordUp[ci] + 12 * octShift;
        while (midi > 74) midi -= 12;
        while (midi < 48) midi += 12;
        return { v: midi, w: st.w };
      });
      barsPat.push('[' + seqToNoteString(barToks) + ']');
    }
    return core.note('<' + barsPat.join(' ') + '>');
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
      const totalW = tokens.reduce((a, t) => a + (t.w || 1), 0) || 1;
      let accW = 0;
      tokens = tokens.map((tk) => {
        const w = tk.w || 1;
        const cur = accW;
        accW += w;
        if (tk.v === null) return { v: null, w };
        const barIdx = Math.floor((cur / totalW) * gridSyms.length) % gridSyms.length;
        const pcs = parseChordPcs(gridSyms[barIdx]);
        const rootPc = pcs ? pcs[0] : tonic % 12;
        const p = rootPc + 12 * 3; // octave 3 -> 36..47
        return { v: foldBass(p), w };
      });
    } else {
      tokens = tokens.map((tk) => ({
        v: tk.v === null ? null : Array.isArray(tk.v) ? tk.v.map(foldBass) : foldBass(tk.v),
        w: tk.w || 1,
      }));
    }
    let pat = core.note(seqToNoteString(tokens));
    const glideMs = Number(opts.glide || 0);
    if (glideMs > 0 && typeof pat.legato === 'function') pat = pat.legato(Math.min(2.0, Math.max(0.5, glideMs / 300.0)));
    return pat;
  };

  // v4.4 — kit atomique pro : enregistre $kick/$snare/$hihat/$open_hat/$perc séparés (1 piste = 1 rôle)
  // avec vrai groove 2 mesures, rolls ponctuels, backbeat protégé et présence par section intelligente.
  g.kit = (name, opts = {}) => {
    const spec = KITS[String(name)] || KITS.trap_half;
    const m = g.__strudel_meta || {};
    const secs = Array.isArray(m.sections) ? m.sections : [];
    const secNames = new Set(secs.map((s) => String(s.name || '').toLowerCase()));
    const hasMultiSec = secs.length >= 3 && (secNames.has('verse') || secNames.has('chorus') || secNames.has('drop'));
    const kitSecMap = (g.__strudel_kit_sections = g.__strudel_kit_sections || {});
    const rolesOrder = ['kick', 'snare', 'hihat', 'open_hat', 'perc'];

    for (const role of rolesOrder) {
      let patStr = role === 'hihat' && opts.hats === 'rolls' && spec.hihat_rolls ? spec.hihat_rolls : spec[role];
      if (!patStr || /^[\s~]+$/.test(patStr)) continue;
      let p = core.s(patStr);
      // Ne JAMAIS détruire le backbeat (kick/snare) par degradeBy ! Variation douce sur hats/perc/open_hat seuls.
      if (opts.var && (role === 'hihat' || role === 'perc' || role === 'open_hat')) {
        const prob = Math.min(0.28, Number(opts.var) * 0.08);
        p = p.sometimesBy(prob, (x) => x.degradeBy(0.30));
      }
      g.__strudel_kit[role] = p;
      // Présence automatique par section (surchargeable via opts.full / opts.intro / opts.outro)
      if (hasMultiSec && !opts.full) {
        if (role === 'open_hat') {
          const targets = ['chorus', 'drop', 'hook', 'climax'].filter((s) => secNames.has(s));
          if (targets.length) kitSecMap[role] = { mode: 'in', names: targets };
        } else if (role === 'perc') {
          const muted = ['intro', 'outro'].filter((s) => secNames.has(s));
          if (muted.length) kitSecMap[role] = { mode: 'mute', names: muted };
        } else if (role === 'hihat') {
          const muted = [opts.intro === 'hats' ? null : 'intro', 'outro'].filter((s) => s && secNames.has(s));
          if (muted.length) kitSecMap[role] = { mode: 'mute', names: muted };
        } else if (role === 'kick' || role === 'snare') {
          const muted = [
            opts.intro === 'drums' ? null : 'intro',
            opts.outro === 'drums' ? null : 'outro',
            role === 'kick' && opts.bridge !== 'drums' ? 'bridge' : null,
          ].filter((s) => s && secNames.has(s));
          if (muted.length) kitSecMap[role] = { mode: 'mute', names: muted };
        }
      }
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
