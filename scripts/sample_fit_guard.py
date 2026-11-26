#!/usr/bin/env python3
"""
v4.2 `sample_fit_guard.py` — Politique de timbre, filtre de registre (S_max),
résolution de catalogue en cascade et garde finale déterministe (pur stdlib, 0 token, < 1 ms).

Point unique de vérité partagé par :
  - `palette_builder.py` (pré-filtrage politique F4 + registre F3 + cohérence F8)
  - `timbre_pleasantness.py` (vérification nature/catégorie dans `pleasantness_gate` F5)
  - `prod_server.py` (`_resolve_palette` F3/F6 + garde finale + `refine_project` F11)
  - `blueprint_safety.py`, `song_builder.py`, `render_and_audit.py`, `loop_first_runner.py`
    (cascade catalogue + `notes_by_track` depuis `score_ir.json` F1).

Règle de conception : le garde-fou **remplace** au lieu de bloquer (alternative saine dans
le pool -> synthé `gl.*` calibré), pour rester à 0 tour LLM supplémentaire.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any, Iterable

ROOT = os.environ.get("AI_STUDIO_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DEFAULT_SLOT_FOR: dict[str, str] = {
    "kick": "kick",
    "snare": "snare_clap",
    "clap": "snare_clap",
    "hihat": "hihat_perc",
    "hat": "hihat_perc",
    "open_hat": "hihat_perc",
    "perc": "hihat_perc",
    "rim": "hihat_perc",
    "bass": "bass_sub",
    "808": "bass_sub",
    "sub": "bass_sub",
    "chords": "chord_keys",
    "keys": "chord_keys",
    "piano": "chord_keys",
    "pad": "pad_atmosphere",
    "strings": "pad_atmosphere",
    "lead": "lead_melody",
    "melody": "lead_melody",
    "counter": "lead_melody",
    "fx": "fx",
}

TONAL_SLOTS = frozenset({"bass_sub", "chord_keys", "pad_atmosphere", "lead_melody"})
PERCUSSIVE_SLOTS = frozenset({"kick", "snare_clap", "hihat_perc"})
PERCUSSIVE_TOKENS = (
    "kick", "snare", "clap", "hat", "open_hat", "perc", "drum", "tom", "rim", "shaker", "cymbal",
)

# Politique de catégories par rôle (F4) :
#   preferred (+0.30), neutral (0.00), discouraged (-0.25), forbidden (exclu).
# Les noms canoniques de rôles (ex. "chord_keys", "bass_sub", "hihat_perc") sont acceptés
# comme `preferred` pour eux-mêmes (compatibilité catalogues synthétiques / fixtures de test).
_ALL_PERC_CATS = frozenset({"kick", "snare", "clap", "hat", "open_hat", "perc", "snare_clap", "hihat_perc"})
_ALL_TONAL_CATS = frozenset({"piano", "guitar", "synth", "pad", "bass", "808", "chord_keys", "pad_atmosphere", "lead_melody", "bass_sub"})

ROLE_CATEGORY_POLICY: dict[str, dict[str, frozenset[str]]] = {
    "kick": {
        "preferred": frozenset({"kick"}),
        "discouraged": frozenset({"808", "other"}),
        "forbidden": frozenset({
            "fx", "vocal", "loop", "piano", "guitar", "synth", "pad", "bass",
            "snare", "clap", "hat", "open_hat", "perc", "sfz",
            "snare_clap", "hihat_perc", "chord_keys", "pad_atmosphere", "lead_melody", "bass_sub",
        }),
    },
    "snare_clap": {
        "preferred": frozenset({"snare", "clap", "snare_clap"}),
        "discouraged": frozenset({"perc", "other"}),
        "forbidden": frozenset({
            "fx", "vocal", "loop", "kick", "808", "hat", "open_hat", "sfz", "hihat_perc",
        }) | _ALL_TONAL_CATS,
    },
    "hihat_perc": {
        "preferred": frozenset({"hat", "open_hat", "perc", "hihat_perc"}),
        "discouraged": frozenset({"other"}),
        "forbidden": frozenset({
            "fx", "vocal", "loop", "kick", "808", "snare", "clap", "sfz", "snare_clap",
        }) | _ALL_TONAL_CATS,
    },
    "bass_sub": {
        "preferred": frozenset({"808", "bass", "bass_sub"}),
        "discouraged": frozenset({"synth", "other", "piano", "guitar"}),
        "forbidden": frozenset({
            "kick", "fx", "vocal", "loop", "hat", "open_hat", "snare", "clap", "perc", "sfz",
            "snare_clap", "hihat_perc",
        }),
    },
    "chord_keys": {
        "preferred": frozenset({"piano", "guitar", "chord_keys"}),
        "discouraged": frozenset({"vocal", "bass", "808", "bass_sub"}),
        "forbidden": frozenset({"fx", "loop", "sfz"}) | _ALL_PERC_CATS,
    },
    "pad_atmosphere": {
        "preferred": frozenset({"pad", "synth", "piano", "pad_atmosphere"}),
        "discouraged": frozenset({"fx", "bass", "808", "bass_sub"}),
        "forbidden": frozenset({"loop", "sfz"}) | _ALL_PERC_CATS,
    },
    "lead_melody": {
        "preferred": frozenset({"piano", "guitar", "synth", "lead_melody"}),
        "discouraged": frozenset({"bass", "808", "bass_sub", "other"}),
        "forbidden": frozenset({"fx", "loop", "sfz"}) | _ALL_PERC_CATS,
    },
}

_KNOWN_FOLDER_CATS = frozenset({
    "808", "bass", "kick", "snare", "clap", "hat", "open_hat", "perc",
    "piano", "guitar", "synth", "pad", "vocal", "fx", "loop", "other", "sfz",
})

_ELECTRONIC_NAME_RE = re.compile(
    r"(?:^|[^a-z0-9])("
    r"fm|glitch|laser|robot|zap|digital|bitcrush|chiptune|metallic|stab|"
    r"sfx|siren|alarm|riser|sweep|acid|reese|distorted"
    r")(?:$|[^a-z0-9])",
    re.IGNORECASE,
)

# Seuils de transposition (mêmes caps que blueprint_safety)
DEFAULT_SHIFT_CAP_ST: dict[str, int] = {"acoustic": 5, "synth": 7}
DEFAULT_MULTIZONE_AMBITUS_ST: dict[str, int] = {"acoustic": 18, "synth": 20}
ACOUSTIC_TOKENS = (
    "piano", "guitar", "string", "violin", "cello", "brass", "woodwind",
    "flute", "voice", "vocal", "choir", "acoustic", "rhodes", "organ", "harp",
)
BELL_INHARMONICITY_MAX = 0.15

# Cache léger de politique/ranges par (sha_score_ir, sha_catalog) (§5 économie CPU)
_GUARD_CACHE: dict[tuple[str, str], dict[str, Any]] = {}


def normalize_slot_role(role_or_name: str, slot_for: dict[str, str] | None = None) -> str:
    """Convertit un nom de piste ou rôle blueprint vers le rôle de slot canonique."""
    mapping = slot_for or DEFAULT_SLOT_FOR
    raw = str(role_or_name or "").strip().lower()
    if raw in ROLE_CATEGORY_POLICY:
        return raw
    if raw in mapping:
        return mapping[raw]
    for tok, slot in mapping.items():
        if tok in raw:
            return slot
    return raw


def is_percussive_track(role: str, name: str = "") -> bool:
    comb = f"{role or ''} {name or ''}".lower()
    return any(tok in comb for tok in PERCUSSIVE_TOKENS)


def infer_sample_category(category: str | None = None, filepath: str | None = None) -> str:
    """Résout la catégorie normalisée depuis le champ explicite ou le dossier parent."""
    cat = str(category or "").strip().lower()
    if cat == "synth-samples":
        cat = "synth"
    if cat and cat not in ("unknown", "sample", "project_sample"):
        return cat
    if filepath:
        norm = os.path.normpath(str(filepath)).replace("\\", "/")
        parts = [p.lower() for p in norm.split("/") if p]
        # Cherche le dossier juste sous `samples/` ou le parent direct
        for idx, p in enumerate(parts[:-1]):
            if p == "samples" and idx + 1 < len(parts) - 1:
                sub = parts[idx + 1]
                if sub == "synth-samples":
                    return "synth"
                if sub in _KNOWN_FOLDER_CATS:
                    return sub
        if len(parts) >= 2:
            parent = parts[-2]
            if parent == "synth-samples":
                return "synth"
            if parent in _KNOWN_FOLDER_CATS:
                return parent
    return cat or "other"


def category_tier(role: str, category: str | None = None, filepath: str | None = None) -> tuple[str, float, str]:
    """Retourne `(tier, bonus, resolved_category)` selon la matrice F4."""
    slot = normalize_slot_role(role)
    cat = infer_sample_category(category, filepath)
    pol = ROLE_CATEGORY_POLICY.get(slot)
    if not pol:
        return ("neutral", 0.0, cat)
    if cat in pol["forbidden"]:
        return ("forbidden", -1.0, cat)
    if cat in pol["preferred"]:
        return ("preferred", 0.30, cat)
    if cat in pol["discouraged"]:
        return ("discouraged", -0.25, cat)
    return ("neutral", 0.0, cat)


def nature_verdict(role: str, sample_nature: str | None = None, inharmonicity: float = 0.0) -> tuple[str, float, str | None]:
    """Vérifie la compatibilité `sample_nature` / `inharmonicity` pour un rôle (F4)."""
    slot = normalize_slot_role(role)
    nat = str(sample_nature or "").strip().lower()
    try:
        inh = float(inharmonicity or 0.0)
    except (TypeError, ValueError):
        inh = 0.0

    is_bell_fx = (nat == "inharmonic_bell_or_fx") or (inh > BELL_INHARMONICITY_MAX)
    if is_bell_fx:
        if slot in ("chord_keys", "pad_atmosphere", "bass_sub"):
            return ("forbidden", -1.0, f"inharmonic_bell_or_fx interdit pour {slot} (inh={inh:.2f})")
        if slot == "lead_melody":
            return ("discouraged", -0.30, f"inharmonic_bell_or_fx pénalisé sur {slot} (inh={inh:.2f})")

    if slot in TONAL_SLOTS and nat in ("poly_chord_or_dyad", "melodic_sequence", "ambient_bed", "percussion"):
        return ("forbidden", -1.0, f"nature={nat} interdite pour rôle tonal {slot}")

    if slot == "kick" and nat == "melodic_sequence":
        return ("forbidden", -1.0, "melodic_sequence interdite pour kick")

    return ("ok", 0.0, None)


def electronic_penalty(role: str, filename_or_path: str = "", timbre_card: str = "") -> tuple[float, list[str]]:
    """Malus −0.20 sur les noms/cartes à consonance électronique/métallique pour rôles tonaux."""
    slot = normalize_slot_role(role)
    if slot not in TONAL_SLOTS:
        return (0.0, [])
    base = os.path.basename(str(filename_or_path or ""))
    hay = f"{base} {timbre_card or ''}"
    hits = sorted({m.group(1).lower() for m in _ELECTRONIC_NAME_RE.finditer(hay)})
    if not hits:
        return (0.0, [])
    return (-0.20, hits)


def candidate_policy(role: str, cand: dict[str, Any], valence: float = 0.0) -> dict[str, Any]:
    """Politique combinée F4 (catégorie + nature + nom électronique + préférence organique)."""
    slot = normalize_slot_role(role)
    fp = str(cand.get("filepath") or cand.get("sample_path") or cand.get("filename") or "")
    cat_tier, cat_bonus, cat = category_tier(slot, cand.get("category"), fp)
    nat_verdict, nat_bonus, nat_reason = nature_verdict(
        slot, cand.get("sample_nature"), float(cand.get("inharmonicity") or 0.0)
    )
    elec_pen, elec_hits = electronic_penalty(slot, fp, str(cand.get("timbre_card") or ""))

    reasons: list[str] = []
    allowed = True
    if cat_tier == "forbidden":
        allowed = False
        reasons.append(f"category={cat} interdite pour {slot}")
    elif cat_tier == "discouraged":
        reasons.append(f"category={cat} déconseillée pour {slot}")

    if nat_verdict == "forbidden":
        allowed = False
        if nat_reason:
            reasons.append(nat_reason)
    elif nat_verdict == "discouraged" and nat_reason:
        reasons.append(nat_reason)

    if elec_hits:
        reasons.append(f"electronic_name({','.join(elec_hits[:3])})")

    # Pré-filtre « organique » par défaut pour valence >= -0.2 (§5)
    organic_bonus = 0.0
    if float(valence or 0.0) >= -0.2 and slot in ("chord_keys", "lead_melody", "pad_atmosphere"):
        if cat in ("piano", "guitar", "pad", "chord_keys", "pad_atmosphere"):
            organic_bonus = 0.08

    if not allowed:
        tier = "forbidden"
    elif cat_tier == "discouraged" or nat_verdict == "discouraged" or elec_pen < 0.0:
        tier = "discouraged"
    elif cat_tier == "preferred":
        tier = "preferred"
    else:
        tier = "neutral"

    return {
        "allowed": allowed,
        "tier": tier,
        "category": cat,
        "bonus": round(cat_bonus + nat_bonus + elec_pen + organic_bonus, 4),
        "reasons": reasons,
    }


def _load_config_shift_caps() -> tuple[dict[str, int], dict[str, int]]:
    """Lit `max_semitone_shift_st` (+ `max_semitone_shift_st_by_role` optionnel) depuis config/mix_profiles.json."""
    caps = dict(DEFAULT_SHIFT_CAP_ST)
    by_role: dict[str, int] = {}
    cfg_path = os.environ.get("MIX_PROFILES_PATH") or os.path.join(ROOT, "config", "mix_profiles.json")
    try:
        with open(cfg_path, encoding="utf-8") as fh:
            cfg = json.load(fh)
        inv = cfg.get("global_invariants") or {}
        raw = inv.get("max_semitone_shift_st") or {}
        if isinstance(raw, dict):
            for k in ("acoustic", "synth"):
                if raw.get(k) is not None:
                    caps[k] = int(raw[k])
            for k, v in raw.items():
                if k not in ("acoustic", "synth") and v is not None:
                    by_role[str(k).lower()] = int(v)
        raw_r = inv.get("max_semitone_shift_st_by_role") or {}
        if isinstance(raw_r, dict):
            for k, v in raw_r.items():
                if v is not None:
                    by_role[str(k).lower()] = int(v)
    except Exception:
        pass
    return caps, by_role


def shift_category(entry: dict[str, Any] | None) -> str:
    text = " ".join(
        str(entry.get(k) or "") for k in ("category", "timbre_card", "filename", "filepath", "sample_path", "sample_nature")
    ).lower() if entry else ""
    if any(t in text for t in ACOUSTIC_TOKENS):
        return "acoustic"
    return "synth"


def effective_shift_cap(entry: dict[str, Any] | None = None, role: str | None = None) -> tuple[str, int]:
    """Retourne `(category, cap_st)` aligné sur `config/mix_profiles.json` + `blueprint_safety`."""
    caps, by_role = _load_config_shift_caps()
    cat = shift_category(entry)
    cap = caps.get(cat, caps["synth"])
    slot = normalize_slot_role(role or "")
    # Un 808/sub tolère un peu plus de pitch-shift qu'un piano acoustique (§5)
    if slot and slot in by_role and cat != "acoustic":
        sample_cat = infer_sample_category((entry or {}).get("category"), (entry or {}).get("filepath") or (entry or {}).get("sample_path"))
        if slot == "bass_sub" and sample_cat in ("808", "bass", "bass_sub"):
            cap = int(by_role[slot])
        elif slot != "bass_sub":
            cap = int(by_role[slot])
    return cat, cap


def register_fit(
    root_note: int | None,
    note_range_or_pitches: Iterable[int] | tuple[int, int] | None,
    entry: dict[str, Any] | None = None,
    role: str | None = None,
) -> dict[str, Any]:
    """Vérifie `S_max = max |n - root_note| <= cap` (même métrique que `blueprint_safety`)."""
    pitches = [int(p) for p in (note_range_or_pitches or []) if isinstance(p, (int, float)) and not isinstance(p, bool)]
    cat, cap = effective_shift_cap(entry, role=role)
    if root_note is None or not pitches:
        return {"ok": True, "s_max": 0, "cap": cap, "category": cat, "ambitus": 0, "action": "ok"}
    rn = int(root_note)
    lo, hi = min(pitches), max(pitches)
    s_max = max(abs(lo - rn), abs(hi - rn))
    ambitus = hi - lo
    if s_max <= cap:
        action = "ok"
    elif ambitus <= DEFAULT_MULTIZONE_AMBITUS_ST.get(cat, 20):
        action = "multizone"
    else:
        action = "swap"
    return {
        "ok": s_max <= cap,
        "s_max": int(s_max),
        "cap": int(cap),
        "category": cat,
        "ambitus": int(ambitus),
        "action": action,
    }


def resolve_catalog_path(
    project_dir: str | None = None,
    bp: dict[str, Any] | None = None,
    root_dir: str | None = None,
) -> str | None:
    """Cascade F1 de résolution de `samples_catalog.json` :
    1. `<projet>/samples_catalog.json`
    2. `<bp.palette.project_samples_dir>/samples_catalog.json` et `../samples_catalog.json`
    3. `<racine>/samples_catalog.json`."""
    candidates: list[str] = []
    if project_dir:
        p = os.path.abspath(os.path.expanduser(project_dir))
        candidates.append(os.path.join(p, "samples_catalog.json"))
    pal = (bp or {}).get("palette") if isinstance(bp, dict) else None
    if isinstance(pal, dict) and pal.get("project_samples_dir"):
        psd = os.path.abspath(os.path.expanduser(str(pal["project_samples_dir"])))
        candidates.append(os.path.join(psd, "samples_catalog.json"))
        candidates.append(os.path.join(os.path.dirname(psd), "samples_catalog.json"))
    rdir = os.path.abspath(os.path.expanduser(root_dir or ROOT))
    candidates.append(os.path.join(rdir, "samples_catalog.json"))
    seen: set[str] = set()
    for c in candidates:
        norm = os.path.normpath(c)
        if norm in seen:
            continue
        seen.add(norm)
        if os.path.isfile(norm):
            return norm
    return None


def notes_by_track_from_score_ir(proj_dir_or_sir: str | dict[str, Any] | None) -> dict[str, list[int]]:
    """Extrait `{track_name: [midi_pitch, ...]}` depuis `<projet>/score_ir.json` ou un dict ScoreIR."""
    sir: dict[str, Any] | None = None
    if isinstance(proj_dir_or_sir, dict):
        sir = proj_dir_or_sir
    elif isinstance(proj_dir_or_sir, str) and proj_dir_or_sir:
        p = os.path.abspath(os.path.expanduser(proj_dir_or_sir))
        sir_path = p if p.endswith(".json") else os.path.join(p, "score_ir.json")
        if os.path.isfile(sir_path):
            try:
                with open(sir_path, encoding="utf-8") as fh:
                    sir = json.load(fh)
            except Exception:
                sir = None
    if not isinstance(sir, dict):
        return {}
    out: dict[str, list[int]] = {}
    tracks = sir.get("tracks")
    if isinstance(tracks, dict):
        for tname, tdata in tracks.items():
            if not isinstance(tdata, dict):
                continue
            ps = [
                int(n["pitch"])
                for n in (tdata.get("notes") or [])
                if isinstance(n, dict)
                and isinstance(n.get("pitch"), (int, float))
                and not isinstance(n.get("pitch"), bool)
            ]
            if ps:
                out[str(tname)] = ps
    return out


def note_ranges_by_slot(
    notes_by_track: dict[str, Iterable[int]] | None,
    slot_for: dict[str, str] | None = None,
) -> dict[str, tuple[int, int]]:
    """Union `(min_pitch, max_pitch)` par slot tonal depuis `notes_by_track` (F3)."""
    if not notes_by_track:
        return {}
    ranges: dict[str, list[int]] = {}
    for tname, pitches in notes_by_track.items():
        slot = normalize_slot_role(tname, slot_for=slot_for)
        if slot not in TONAL_SLOTS:
            continue
        ps = [int(p) for p in (pitches or []) if isinstance(p, (int, float)) and not isinstance(p, bool)]
        if not ps:
            continue
        if slot not in ranges:
            ranges[slot] = [min(ps), max(ps)]
        else:
            ranges[slot][0] = min(ranges[slot][0], min(ps))
            ranges[slot][1] = max(ranges[slot][1], max(ps))
    return {k: (v[0], v[1]) for k, v in ranges.items()}


def _file_sha12(path: str | None) -> str:
    if not path or not os.path.isfile(path):
        return ""
    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()[:12]
    except Exception:
        return ""


def _lookup_catalog(cat_idx: dict[str, dict[str, Any]], sample_path: str, cat_dir: str) -> dict[str, Any] | None:
    p = str(sample_path or "").strip()
    if not p or not cat_idx:
        return None
    cands = [os.path.normpath(os.path.abspath(p)).lower(), p.lower(), os.path.basename(p).lower()]
    if not os.path.isabs(p) and cat_dir:
        cands.insert(0, os.path.normpath(os.path.join(cat_dir, p)).lower())
    for c in cands:
        if c in cat_idx:
            return cat_idx[c]
    return None


def _load_catalog_map(catalog_path: str | None) -> tuple[dict[str, dict[str, Any]], str]:
    if not catalog_path or not os.path.isfile(catalog_path):
        return {}, ""
    try:
        with open(catalog_path, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return {}, ""
    cat_dir = os.path.dirname(os.path.abspath(catalog_path))
    idx: dict[str, dict[str, Any]] = {}
    for s in data.get("samples") or []:
        if not isinstance(s, dict):
            continue
        fp = str(s.get("filepath") or "")
        if not fp:
            continue
        ap = os.path.normpath(os.path.abspath(fp))
        idx[ap.lower()] = s
        try:
            rel = os.path.normpath(os.path.relpath(ap, cat_dir))
            idx[rel.lower()] = s
        except ValueError:
            pass
        idx.setdefault(os.path.basename(fp).lower(), s)
    return idx, cat_dir


def _soft_tonal_params(slot_role: str, existing: dict[str, Any] | None = None, valence: float = 0.0) -> dict[str, Any]:
    """Chaîne douce systématique sur pistes tonales sample (§5) : LPF 6.5–9 kHz, ADSR propre."""
    params = dict(existing or {})
    if slot_role in ("chord_keys", "pad_atmosphere"):
        default_cut = 5800.0 if valence < -0.2 else 6500.0
        params["cutoff_hz"] = min(float(params.get("cutoff_hz", default_cut)), 6500.0)
        params["attack_ms"] = max(8.0 if valence >= -0.2 else 4.0, float(params.get("attack_ms", 8.0)))
        params["release_ms"] = max(140.0, float(params.get("release_ms", 180.0)))
    elif slot_role == "lead_melody":
        default_cut = 6500.0 if valence < -0.2 else 9000.0
        params["cutoff_hz"] = min(float(params.get("cutoff_hz", default_cut)), 9000.0)
        params["attack_ms"] = max(2.0, float(params.get("attack_ms", 6.0)))
        params["release_ms"] = max(80.0, float(params.get("release_ms", 160.0)))
    elif slot_role == "bass_sub":
        params["attack_ms"] = max(2.0, float(params.get("attack_ms", 2.0)))
        params["release_ms"] = max(80.0, float(params.get("release_ms", 120.0)))
    return params


def _apply_synth_fallback(tr: dict[str, Any], key: str, slot_role: str, mix_profile: str, valence: float) -> str:
    """Remplace proprement une piste sample invalide par un synthé `gl.*` calibré (0 blocage)."""
    try:
        from timbre_pleasantness import pick_gl_plugin
        pg = pick_gl_plugin(key, mix_profile, valence)
    except Exception:
        fallback = {
            "kick": "gl.kick", "snare_clap": "gl.snare", "hihat_perc": "gl.clap" if "perc" in key else "gl.hat",
            "bass_sub": "gl.fingerbass", "chord_keys": "gl.keys", "pad_atmosphere": "gl.warmsynth",
            "lead_melody": "gl.warmsynth",
        }
        pg = {"plugin": fallback.get(slot_role, "gl.warmsynth"), "params": {}}
    tr["source_type"] = "gridline_native"
    plug = str(pg.get("plugin") or "gl.warmsynth")
    if plug == "gl.sampler":
        plug = "gl.fingerbass" if slot_role == "bass_sub" else "gl.warmsynth"
    tr["plugin"] = plug
    params = dict(pg.get("params") or {})
    if slot_role in TONAL_SLOTS:
        params = _soft_tonal_params(slot_role, params, valence=valence)
    if params:
        tr["plugin_params"] = params
    for stale_k in ("sample_path", "root_note", "fine_tune_ct"):
        tr.pop(stale_k, None)
    return str(tr["plugin"])


def _pick_healthy_replacement(
    *,
    track_name: str,
    slot_role: str,
    pool: list[dict[str, Any]],
    used_paths: set[str],
    pitches: list[int],
    percussive: bool,
    valence: float,
    cat_idx: dict[str, dict[str, Any]],
    cat_dir: str,
) -> dict[str, Any] | None:
    """Sélectionne la meilleure alternative saine et non utilisée dans `pool`."""
    try:
        from timbre_pleasantness import pleasantness_gate
    except Exception:
        pleasantness_gate = None

    lname = track_name.lower()
    ordered = list(pool)
    # Pour une piste `perc`/`rim`, préférer d'abord les vrais samples de catégorie `perc`
    if "perc" in lname or "rim" in lname:
        ordered.sort(
            key=lambda c: (
                0 if infer_sample_category(c.get("category"), c.get("filepath")) == "perc" else 1,
                -(float(c.get("role_score", 0.5)) * 0.6 + float(c.get("comfort_score", 0.5)) * 0.4),
            )
        )

    for cand in ordered:
        if cand.get("source_type") not in ("sample", "project_sample"):
            continue
        fp = str(cand.get("filepath") or "")
        if not fp:
            continue
        norm_fp = os.path.normpath(os.path.abspath(fp)).lower()
        if norm_fp in used_paths:
            continue
        if not os.path.isfile(fp):
            continue
        entry = _lookup_catalog(cat_idx, fp, cat_dir)
        merged = dict(entry or {})
        merged.update({k: v for k, v in cand.items() if v is not None})
        pol = candidate_policy(slot_role, merged, valence=valence)
        if not pol["allowed"]:
            continue
        if not percussive:
            if merged.get("playable_chromatically") is False:
                continue
            rn = merged.get("midi_root")
            if rn is None:
                continue
            if pitches:
                rf = register_fit(int(rn), pitches, merged, role=slot_role)
                if not rf["ok"]:
                    continue
        if pleasantness_gate is not None:
            g = pleasantness_gate({
                "r_harsh": merged.get("r_harsh"),
                "roughness": merged.get("roughness"),
                "peak_harsh_db": merged.get("peak_harsh_db"),
                "comfort_score": merged.get("comfort_score"),
                "centroid_hz": merged.get("centroid_hz"),
                "crest_factor_db": merged.get("crest_factor_db"),
                "sample_nature": merged.get("sample_nature"),
                "category": pol["category"],
                "inharmonicity": merged.get("inharmonicity"),
                "filepath": fp,
            }, slot_role)
            if g["verdict"] == "SWAP":
                continue
        return merged
    return None


def enforce_blueprint_sample_fit(
    bp: dict[str, Any],
    *,
    notes_by_track: dict[str, Iterable[int]] | None = None,
    catalog_path: str | None = None,
    pool_by_role: dict[str, list[dict[str, Any]]] | None = None,
    valence: float | None = None,
    mix_profile: str | None = None,
    slot_for: dict[str, str] | None = None,
    write_diag_dir: str | None = None,
) -> dict[str, Any]:
    """Garde finale (0 blocage, 0 token) sur `bp["tracks"]` :
    - F6 : dédoublonne les fichiers partagés entre pistes (ex. perc == hihat)
    - F4 : exclut les catégories/natures interdites (FX sur accords, kick sur basse, etc.)
    - F5 : vérifie le gate d'agréabilité (jamais SWAP)
    - F3/F11 : vérifie `S_max <= cap` sur les notes réelles + verrou `root_note == midi_root`
    - §5 : applique la chaîne douce (`cutoff_hz`, ADSR) sur pistes tonales sample.

    Remplace toute piste défectueuse par la meilleure alternative saine du pool, sinon par
    un synthé `gl.*` calibré. Modifie `bp` en place et retourne le rapport structuré."""
    mapping = slot_for or DEFAULT_SLOT_FOR
    eff_profile = str(mix_profile or bp.get("mix_profile") or "trap_rap")
    eff_valence = float(
        valence if valence is not None else (bp.get("emotion_vector") or {}).get("valence", 0.0)
    )
    cat_idx, cat_dir = _load_catalog_map(catalog_path)
    notes_ci: dict[str, list[int]] = {}
    for k, v in (notes_by_track or {}).items():
        ps = [int(p) for p in (v or []) if isinstance(p, (int, float)) and not isinstance(p, bool)]
        if ps:
            notes_ci[str(k)] = ps
            notes_ci[str(k).lower()] = ps

    try:
        from timbre_pleasantness import pleasantness_gate
    except Exception:
        pleasantness_gate = None

    GM_DRUM = {"kick": 36, "snare": 38, "clap": 39, "hihat": 42, "hat": 42, "perc": 39, "rim": 39}
    used_paths: set[str] = set()
    fixes: list[dict[str, Any]] = []
    n_swapped_sample = 0
    n_fallback_synth = 0
    n_deduped = 0

    for tr in bp.get("tracks") or []:
        if not isinstance(tr, dict):
            continue
        role = str(tr.get("role") or "")
        if role == "vocals":
            continue
        name = str(tr.get("name") or role)
        key = name.lower()
        slot_role = normalize_slot_role(key, slot_for=mapping)
        if slot_role == key and role:
            slot_role = normalize_slot_role(role, slot_for=mapping)
        percussive = is_percussive_track(role, name) or (slot_role in PERCUSSIVE_SLOTS)

        if tr.get("source_type") != "sample":
            if slot_role in TONAL_SLOTS and tr.get("source_type") == "gridline_native":
                tr["plugin_params"] = _soft_tonal_params(slot_role, tr.get("plugin_params"), valence=eff_valence)
            continue

        fp = str(tr.get("sample_path") or "")
        norm_fp = os.path.normpath(os.path.abspath(fp)).lower() if fp else ""
        pitches = notes_ci.get(name) or notes_ci.get(key) or []
        entry = _lookup_catalog(cat_idx, fp, cat_dir) if fp else None
        merged = dict(entry or {})
        merged.setdefault("filepath", fp)
        if tr.get("root_note") is not None:
            merged.setdefault("midi_root", tr.get("root_note"))

        violations: list[str] = []
        is_dup = False
        if not fp:
            violations.append("missing_path")
        elif norm_fp in used_paths:
            violations.append("duplicate_sample")
            is_dup = True

        pol = candidate_policy(slot_role, merged, valence=eff_valence)
        if not pol["allowed"]:
            violations.append(f"policy({pol['category']})")

        if not percussive:
            if merged.get("playable_chromatically") is False:
                violations.append(f"non_playable({merged.get('sample_nature')})")
            det_root = (entry or {}).get("midi_root")
            if det_root is not None and tr.get("root_note") is not None and int(tr["root_note"]) != int(det_root):
                # Recaler root_note sur la vraie fondamentale détectée avant de tester S_max
                tr["root_note"] = int(det_root)
            rn = tr.get("root_note")
            if rn is None:
                violations.append("missing_root")
            elif pitches:
                rf = register_fit(int(rn), pitches, merged, role=slot_role)
                if not rf["ok"]:
                    violations.append(f"S_max={rf['s_max']}>{rf['cap']}")

        if pleasantness_gate is not None and entry is not None:
            g = pleasantness_gate({
                "r_harsh": entry.get("r_harsh"),
                "roughness": entry.get("roughness"),
                "peak_harsh_db": entry.get("peak_harsh_db"),
                "comfort_score": entry.get("comfort_score"),
                "centroid_hz": entry.get("centroid_hz"),
                "crest_factor_db": entry.get("crest_factor_db"),
                "sample_nature": entry.get("sample_nature"),
                "category": pol["category"],
                "inharmonicity": entry.get("inharmonicity"),
                "filepath": fp,
            }, slot_role)
            if g["verdict"] == "SWAP":
                violations.append("pleasantness_SWAP")

        if not violations:
            if norm_fp:
                used_paths.add(norm_fp)
            if slot_role in TONAL_SLOTS:
                tr["plugin_params"] = _soft_tonal_params(slot_role, tr.get("plugin_params"), valence=eff_valence)
            continue

        # Tentative de substitution par une alternative saine du pool
        reason_str = ",".join(violations)
        repl = _pick_healthy_replacement(
            track_name=name,
            slot_role=slot_role,
            pool=(pool_by_role or {}).get(slot_role) or [],
            used_paths=used_paths,
            pitches=pitches,
            percussive=percussive,
            valence=eff_valence,
            cat_idx=cat_idx,
            cat_dir=cat_dir,
        )
        if repl is not None:
            new_fp = str(repl["filepath"])
            tr["source_type"] = "sample"
            tr["sample_path"] = new_fp
            tr["plugin"] = "gl.sampler"
            if percussive:
                notated = max(set(pitches), key=pitches.count) if pitches else None
                tr["root_note"] = int(notated or GM_DRUM.get(key) or repl.get("midi_root") or 36)
            else:
                tr["root_note"] = int(max(0, min(127, int(repl["midi_root"]))))
            ft = repl.get("recommended_fine_tune_ct") or repl.get("recommended_gl_fine_tune_ct") or 0
            if ft:
                tr["fine_tune_ct"] = int(ft)
            else:
                tr.pop("fine_tune_ct", None)
            if slot_role in TONAL_SLOTS:
                tr["plugin_params"] = _soft_tonal_params(slot_role, tr.get("plugin_params"), valence=eff_valence)
            used_paths.add(os.path.normpath(os.path.abspath(new_fp)).lower())
            n_swapped_sample += 1
            if is_dup:
                n_deduped += 1
            fixes.append({
                "track": name,
                "slot": slot_role,
                "action": "swap_sample",
                "from": os.path.basename(fp) if fp else None,
                "to": os.path.basename(new_fp),
                "reasons": violations,
                "short": f"{name}->{os.path.basename(new_fp)[:14]}({reason_str})",
            })
        else:
            plug = _apply_synth_fallback(tr, key, slot_role, eff_profile, eff_valence)
            n_fallback_synth += 1
            if is_dup:
                n_deduped += 1
            fixes.append({
                "track": name,
                "slot": slot_role,
                "action": "fallback_synth",
                "from": os.path.basename(fp) if fp else None,
                "to": plug,
                "reasons": violations,
                "short": f"{name}->{plug}({reason_str})",
            })

    report = {
        "status": "FIXED" if fixes else "OK",
        "n_fixed": len(fixes),
        "n_swapped_sample": n_swapped_sample,
        "n_fallback_synth": n_fallback_synth,
        "n_deduped": n_deduped,
        "catalog": catalog_path,
        "fixes": fixes,
    }
    report["summary_line"] = format_guard_line(report)

    if write_diag_dir:
        try:
            diag_dir = os.path.join(os.path.abspath(write_diag_dir), "renders", "diagnostics")
            os.makedirs(diag_dir, exist_ok=True)
            with open(os.path.join(diag_dir, "sound_guard.json"), "w", encoding="utf-8") as fh:
                json.dump(report, fh, ensure_ascii=False, indent=2)
                fh.write("\n")
        except Exception:
            pass
    return report


def format_guard_line(guard_report: dict[str, Any] | None, limit: int = 160) -> str:
    """Ligne compacte `sound_guard=OK` ou `sound_guard=FIXED ...` (<= 160 car.)."""
    if not guard_report:
        return "sound_guard=n/a"
    status = str(guard_report.get("status") or "OK")
    fixes = guard_report.get("fixes") or []
    if not fixes:
        return f"sound_guard={status}"
    summary = " ".join(str(f.get("short") or f"{f.get('track')}->{f.get('to')}") for f in fixes)
    line = f"sound_guard={status} {summary}"
    if len(line) > limit:
        line = line[: limit - 1] + "…"
    return line


def cached_note_ranges_and_catalog(
    project_dir: str,
    bp: dict[str, Any] | None = None,
    slot_for: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Cache léger par `(sha(score_ir.json), sha(catalog))` (§5 économie CPU)."""
    proj = os.path.abspath(os.path.expanduser(project_dir))
    sir_path = os.path.join(proj, "score_ir.json")
    cat_path = resolve_catalog_path(proj, bp=bp)
    key = (_file_sha12(sir_path), _file_sha12(cat_path))
    if key != ("", "") and key in _GUARD_CACHE:
        return _GUARD_CACHE[key]
    notes = notes_by_track_from_score_ir(sir_path)
    ranges = note_ranges_by_slot(notes, slot_for=slot_for)
    res = {"catalog_path": cat_path, "notes_by_track": notes, "note_ranges": ranges}
    if key != ("", ""):
        if len(_GUARD_CACHE) > 64:
            _GUARD_CACHE.clear()
        _GUARD_CACHE[key] = res
    return res
