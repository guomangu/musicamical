// v4.0 strudel_prelude.mjs — helpers anti-erreur injectés dans le scope d'évaluation headless
// (strudel_eval.mjs) : grid(), track(), deg(), glide(). Rend les fautes musicales usuelles
// syntaxiquement impossibles ou auto-corrigées avant le pont Python (0 token LLM).
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

export function installPrelude(core, meta) {
  const g = globalThis;
  g.__strudel_meta = meta || {};
  g.__strudel_grid = null;
  g.__strudel_track_roles = {};

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
    const src = String(seq).trim();
    const out = [];
    let i = 0;
    while (i < src.length) {
      const ch = src[i];
      if (/\s/.test(ch)) { i++; continue; }
      if (ch === '~') { out.push('~'); i++; continue; }
      if (ch === '<') { let j = src.indexOf('>', i); const inner = src.slice(i + 1, j < 0 ? src.length : j); out.push('<' + inner.split(/\s+/).filter(Boolean).map((t) => t === '~' ? '~' : midiToName(degToMidi(t, tonic, scale))).join(' ') + '>'); i = (j < 0 ? src.length : j + 1); continue; }
      if (ch === '[') {
        let j = src.indexOf(']', i);
        const inner = src.slice(i + 1, j < 0 ? src.length : j);
        const names = inner.split(/\s+/).filter(Boolean).map((t) => midiToName(degToMidi(t, tonic, scale)));
        out.push('[' + names.join(',') + ']');
        i = (j < 0 ? src.length : j + 1);
        continue;
      }
      let j = i;
      while (j < src.length && !/[\s~\[\]<]/.test(src[j])) j++;
      out.push(midiToName(degToMidi(src.slice(i, j), tonic, scale)));
      i = j;
    }
    return core.note(out.join(' '));
  };

  g.glide = (ms) => (p) => (typeof p.legato === 'function' ? p.legato(Math.min(2.0, Math.max(0.5, ms / 300.0))) : p);

  try {
    const proto = Object.getPrototypeOf(core.note('c4'));
    if (proto && !proto.glide) {
      proto.glide = function (ms) {
        return typeof this.legato === 'function' ? this.legato(Math.min(2.0, Math.max(0.5, (ms || 60) / 300.0))) : this;
      };
    }
  } catch { /* glide() curried reste disponible */ }

  return g;
}
