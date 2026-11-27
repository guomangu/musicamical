"""v4.1 — timbre_pleasantness.py — Pourquoi un morceau sonne « désagréable » et comment y remédier.

Diagnostic (le rythme est bon, le timbre est mauvais) : jusqu'ici le « confort » d'un son n'était
qu'un **score mou** (`compute_comfort_score`) — un sample dur/grinçant pouvait encore gagner sur le
fit de rôle. Il manquait : (1) des **plafonds durs** par rôle sur la dureté (r_harsh, peak_harsh_db,
roughness Plomp-Levelt, crest des sons tonaux) ; (2) une **dissonance sensorielle par paire** entre
les timbres choisis dans une même palette (deux sons proches en fréquence critique = rugueux) ;
(3) pour le scénario A (synthés `gl.*`), une carte de plugins **statique** ignorant le profil et la
valence (archetypes `config/timbre_archetypes.json` non appliqués à la façade).

Ce module fournit :
  - `plomp_levelt_roughness(freqs, mags)`   : rugosité sensorielle d'un spectre (paires de partiels
    dans une bande critique, courbe de dissonance Zwicker) ;
  - `interval_consonance(f1, f2)`           : consonance d'intervalle (octave > quinte > ... > triton) ;
  - `pleasantness_gate(metrics, role)`      : verdict dur PASS / FIX / SWAP + score + raisons
    (plafonds durs PLEASANTNESS_CAPS par rôle + plancher de confort) ;
  - `palette_timbre_coherence(items)`       : cohérence d'une palette (collision Bark24 pondérée graves
    via sample_quality_v2 + pénalité de rugosité par paire lead/chords/bass) ;
  - `pick_gl_plugin(role, mix_profile, valence)` : plugin `gl.*` agréable pour (rôle, profil,
    valence) depuis les archetypes calibrés (remplace la carte statique de la façade).

Câblage : `palette_builder.propose_palettes` (filtre les candidats SWAP + confort réel des synthés),
`select_sample` (exclusion SWAP), `prod_server._ensure_blueprint` (plugin par archetype).
"""
from __future__ import annotations

import json
import math
import os
import sys

ROOT = os.environ.get("AI_STUDIO_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(ROOT, "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

import numpy as np

# Plafonds durs d'agréabilité par rôle (au-delà -> SWAP). r_harsh/roughness en [0,1]-ish (métriques DSP du projet).
PLEASANTNESS_CAPS: dict[str, dict[str, float]] = {
    "kick":         {"r_harsh": 0.45, "roughness": 0.30, "peak_harsh_db": 14.0, "comfort_min": 0.45},
    "bass_sub":     {"r_harsh": 0.40, "roughness": 0.28, "peak_harsh_db": 13.0, "comfort_min": 0.50},
    "snare_clap":   {"r_harsh": 0.55, "roughness": 0.38, "peak_harsh_db": 16.0, "comfort_min": 0.40},
    "hihat_perc":   {"r_harsh": 0.60, "roughness": 0.45, "peak_harsh_db": 18.0, "comfort_min": 0.35},  # cymbales métalliques tolérées
    "chord_keys":   {"r_harsh": 0.35, "roughness": 0.22, "peak_harsh_db": 11.0, "comfort_min": 0.60},
    "pad_atmosphere": {"r_harsh": 0.30, "roughness": 0.20, "peak_harsh_db": 10.0, "comfort_min": 0.65},
    "lead_melody":  {"r_harsh": 0.35, "roughness": 0.22, "peak_harsh_db": 11.0, "comfort_min": 0.60},
}
DEFAULT_CAPS = {"r_harsh": 0.45, "roughness": 0.30, "peak_harsh_db": 14.0, "comfort_min": 0.45}
# Centroid max pour les rôles tonaux (au-delà = trop sifflant/aigre)
CENTROID_MAX_HZ = {"lead_melody": 6500.0, "chord_keys": 5500.0, "pad_atmosphere": 4500.0, "bass_sub": 900.0}
# Crest max pour les rôles tonaux (au-delà = trop percussif/claquant pour un son soutenu)
# Un vrai piano acoustique / pluck avec 3-5 s de décroissance naturelle a 14-19 dB de crest.
# v4.3 (D3) : valeurs lues dans le SSOT `config/mix_profiles.json`
# (`sample_guard_invariants.pleasantness_caps.crest_max_db`) — aucun doublon codé en dur.
def _crest_max_db_tonal() -> dict[str, float]:
    try:
        from mix_config import crest_max_db as _mc_crest
        out = {}
        for role in ("lead_melody", "chord_keys", "pad_atmosphere", "bass_sub"):
            v = _mc_crest(role)
            if v is not None:
                out[role] = v
        if out:
            return out
    except Exception:
        pass
    return {"lead_melody": 19.5, "chord_keys": 19.5, "pad_atmosphere": 14.5, "bass_sub": 15.0}


CREST_MAX_DB_TONAL = _crest_max_db_tonal()

TONAL_ROLES = ("lead_melody", "chord_keys", "pad_atmosphere", "bass_sub")

# P1-4 (audit rap_ombre_134 §P1-4) : rôles percussifs — un snare trap claquant / un kick punchy
# ont naturellement une rugosité, une brillance et un crest élevés : on ne pénalise PAS ces
# métriques (seuls les défauts physiques durs restent bloquants), sinon 24 snares et 22 kicks
# sont exclus à tort par `pleasantness_excluded`.
PERCUSSIVE_GATE_ROLES = ("kick", "snare_clap", "hihat_perc", "perc", "clap", "drums")


def _critical_bandwidth_hz(f_hz: float) -> float:
    """Bande critique Zwicker (ERB approché) : b = 25 + 75*(1 + 1.4*(f/1000)^2)^0.69."""
    return 25.0 + 75.0 * (1.0 + 1.4 * (f_hz / 1000.0) ** 2) ** 0.69


def plomp_levelt_roughness(freqs, mags) -> float:
    """Rugosité sensorielle d'un spectre (somme des dissonances de paires de partiels proches).
    Courbe de dissonance : pic à ~0.24*bande critique, nul au-delà d'une bande critique."""
    f = np.asarray(freqs, dtype=np.float64)
    m = np.asarray(mags, dtype=np.float64)
    if len(f) < 2 or float(np.sum(m)) <= 0:
        return 0.0
    order = np.argsort(f)
    f = f[order]
    m = m[order]
    tot = float(np.sum(m * m)) + 1e-18
    rough = 0.0
    n = len(f)
    for i in range(n):
        cbw = _critical_bandwidth_hz(float(f[i]))
        for j in range(i + 1, n):
            df = float(f[j] - f[i])
            if df >= cbw:
                break
            d = math.exp(-3.5 * (df / (0.24 * cbw)) ** 2) - math.exp(-5.5 * (df / (0.24 * cbw)) ** 2)
            d = max(0.0, d)
            rough += float(min(m[i], m[j]) ** 2) * d
    return round(float(min(1.0, rough / tot * 4.0)), 4)


_SIMPLE_RATIOS = ((1, 1, 1.0), (2, 1, 0.95), (3, 2, 0.85), (4, 3, 0.75), (5, 3, 0.65), (5, 4, 0.6),
                  (6, 5, 0.4), (8, 5, 0.3), (7, 4, 0.2), (7, 5, 0.12), (10, 7, 0.1), (7, 6, 0.15),
                  (2, 7, 0.08), (1, 7, 0.05), (0, 0, 0.05))  # (0,0) = triton sqrt(2)
_TRITONE = 2.0 ** 0.5


def interval_consonance(f1: float, f2: float) -> float:
    """Consonance sensorielle d'un intervalle : score du ratio simple le PLUS PROCHE
    (1.0 = unisson/octave, ~0.05 = triton)."""
    if f1 <= 0 or f2 <= 0:
        return 0.0
    r = max(f1, f2) / min(f1, f2)
    while r > 2.0:
        r /= 2.0
    best_dist = None
    best_score = 0.0
    for num, den, score in _SIMPLE_RATIOS:
        ratio = _TRITONE if (num == 0 and den == 0) else num / den
        d = abs(r - ratio)
        if best_dist is None or d < best_dist:
            best_dist = d
            best_score = score
    return round(best_score * max(0.0, 1.0 - (best_dist or 0.0) / 0.25), 3)


def pleasantness_gate(metrics: dict, role: str = "lead_melody") -> dict:
    """Verdict dur d'agréabilité d'un son pour un rôle (v4.2 F4+F5).
    SWAP si un plafond dur est dépassé ou si la politique de catégorie/nature (F4) exclut le son ;
    FIX si on approche des plafonds (> 80 %) ou si une métrique clé manque sur rôle tonal (F5) ;
    PASS sinon.
    `metrics` : champs DSP usuels (r_harsh, roughness, peak_harsh_db, comfort_score, centroid_hz,
    crest_factor_db) + optionnels (category, sample_nature, inharmonicity, filepath)."""
    caps = dict(DEFAULT_CAPS)
    caps.update(PLEASANTNESS_CAPS.get(role, {}))
    if role in PERCUSSIVE_GATE_ROLES:
        # P1-4 : seuils percussifs relâchés (rugosité/brillance naturelles d'une frappe)
        for _k in ("r_harsh", "roughness", "peak_harsh_db"):
            caps[_k] = float(caps[_k]) * 4.0
        caps["comfort_min"] = min(float(caps["comfort_min"]), 0.05)
    reasons: list[str] = []
    worst = 0.0
    policy_rejected = False

    # F4 — politique catégorie / nature / inharmonicité (si fournies dans metrics)
    if any(metrics.get(k) is not None for k in ("category", "sample_nature", "inharmonicity", "filepath")):
        try:
            from sample_fit_guard import candidate_policy
            pol = candidate_policy(role, metrics)
            if not pol["allowed"]:
                policy_rejected = True
                reasons.extend(pol["reasons"])
        except Exception:
            pass

    missing_metrics: list[str] = []
    raw_r_harsh = metrics.get("r_harsh")
    eff_r_harsh = float(raw_r_harsh) if raw_r_harsh is not None else None
    for key in ("r_harsh", "roughness", "peak_harsh_db"):
        raw = metrics.get(key)
        if raw is None:
            if role in TONAL_ROLES:
                missing_metrics.append(key)
            v = 0.0
        else:
            v = float(raw)
        # Un instrument tonal chaud/harmonique (piano, guitare, voix, pad) a r_harsh < 0.06
        # (quasi zéro énergie dans 2-5 kHz) : un pic harmonique discret au-dessus d'un plancher
        # silencieux à -80 dB donne peak_harsh_db = 18-28 dB SANS aucune dureté audible.
        if key == "peak_harsh_db" and eff_r_harsh is not None and eff_r_harsh < 0.06:
            continue
        cap = caps[key]
        worst = max(worst, v / max(cap, 1e-9))  # ratio même sous le plafond (zone FIX > 0.8)
        if v > cap:
            reasons.append(f"{key}={v:.2f} > plafond {cap:.2f}")
    cent = float(metrics.get("centroid_hz") or 0.0)
    cmax = CENTROID_MAX_HZ.get(role)
    if cmax and cent > cmax:
        reasons.append(f"centroid={cent:.0f}Hz > {cmax:.0f}Hz (trop sifflant)")
        worst = max(worst, cent / cmax)
    if role in TONAL_ROLES:
        raw_cf = metrics.get("crest_factor_db")
        if raw_cf is None:
            missing_metrics.append("crest_factor_db")
            cf = 0.0
        else:
            cf = float(raw_cf)
        cfmax = CREST_MAX_DB_TONAL.get(role, 15.0)
        worst = max(worst, cf / cfmax)
        if cf > cfmax:
            reasons.append(f"crest={cf:.1f}dB > {cfmax:.1f}dB (trop claquant pour un rôle tonal)")
    raw_conf = metrics.get("comfort_score")
    conf = float(raw_conf) if raw_conf is not None else 1.0
    if raw_conf is None and role in TONAL_ROLES:
        missing_metrics.append("comfort_score")
    worst = max(worst, caps["comfort_min"] / max(conf, 1e-9))
    if conf < caps["comfort_min"]:
        reasons.append(f"comfort={conf:.2f} < plancher {caps['comfort_min']:.2f}")

    unmeasured = bool(missing_metrics and role in TONAL_ROLES)
    if policy_rejected or worst > 1.0:
        verdict = "SWAP"
    elif worst > 0.8 or unmeasured:
        verdict = "FIX"
        if unmeasured:
            reasons.append(f"unmeasured({','.join(missing_metrics)})")
    else:
        verdict = "PASS"
    eff_worst = max(worst, 1.2 if policy_rejected else (0.85 if unmeasured else 0.0))
    score = round(float(max(0.0, min(1.0, 1.0 - max(0.0, eff_worst - 0.8) * 5.0))), 3)
    return {
        "verdict": verdict,
        "score": score,
        "reasons": reasons,
        "role": role,
        "unmeasured": unmeasured,
        "missing_metrics": missing_metrics,
    }


def palette_timbre_coherence(items: list[dict]) -> dict:
    """Cohérence timbrale d'une palette : collision Bark24 pondérée graves (sample_quality_v2)
    entre les rôles tenus + rugosité par paire (centroids proches dans les médiums = dissonance).
    items : dicts avec 'role' et 'bark_24'/'centroid_hz' optionnels."""
    from sample_quality_v2 import spectral_collision
    barks = [it.get("bark_24") for it in items if isinstance(it.get("bark_24"), list) and len(it.get("bark_24")) == 24]
    collision = None
    if len(barks) >= 2:
        cmax = 0.0
        for i in range(len(barks)):
            for j in range(i + 1, len(barks)):
                c = spectral_collision(barks[i], barks[j])
                if c.get("collision") is not None:
                    cmax = max(cmax, float(c["collision"]))
        collision = round(cmax, 6)
    # rugosité par paire via centroids (médiums proches = dissonance perçue)
    cents = [(str(it.get("role", "")), float(it.get("centroid_hz") or 0.0)) for it in items if it.get("centroid_hz")]
    pair_rough = 0.0
    notes = []
    for i in range(len(cents)):
        for j in range(i + 1, len(cents)):
            r1, c1 = cents[i]
            r2, c2 = cents[j]
            if c1 <= 0 or c2 <= 0:
                continue
            df = abs(c1 - c2)
            cbw = _critical_bandwidth_hz((c1 + c2) / 2.0)
            if df < cbw and 300.0 < (c1 + c2) / 2.0 < 6000.0:
                pair_rough = max(pair_rough, float(math.exp(-3.5 * (df / (0.24 * cbw)) ** 2)))
                notes.append(f"{r1}~{r2} centroids {c1:.0f}/{c2:.0f} Hz (bande critique {cbw:.0f} Hz)")
    score = 1.0
    if collision is not None:
        # Durcissement §5 : au-delà de 0.35 de collision Bark24, pénalité doublée
        extra = max(0.0, collision - 0.35) * 3.0
        score -= min(0.65, collision * 3.0 + extra)
    score -= min(0.4, pair_rough * 0.8)
    return {"score": round(float(max(0.0, min(1.0, score))), 3),
            "max_bark_collision": collision, "pair_roughness": round(pair_rough, 3), "notes": notes}


def pick_gl_plugin(role: str, mix_profile: str = "trap_rap", valence: float = 0.0) -> dict:
    """Plugin `gl.*` + preset factory calibré pour (rôle, profil, valence) : archetype
    (`gl_dsl.resolve_archetype`, dark_overrides si valence < -0.2), avec séparation stricte
    entre pad/chords et counter/lead. Repli : carte statique ROLE_PLUGIN de la façade."""
    from gl_dsl import SYNTH_REGISTRY, resolve_archetype
    tonal_map = {
        "bass_sub": "bass", "bass": "bass", "808": "bass", "sub": "bass",
        "chord_keys": "chords", "chords": "chords", "keys": "chords", "piano": "chords",
        "pad_atmosphere": "pad", "pad": "pad", "strings": "pad",
        "lead_melody": "lead", "lead": "lead", "melody": "lead", "vocals": "lead",
        "counter": "counter", "arp": "counter", "arpeggio": "counter", "pluck": "counter", "bell": "counter",
    }
    # rôles percussifs (kick/snare/hihat/clap/perc/fx) : PAS d'archetype tonal -> repli direct gl.*
    arch = resolve_archetype(mix_profile, tonal_map[role], valence=valence) if role in tonal_map else None
    if arch is None and role in tonal_map and tonal_map[role] in ("pad", "counter"):
        arch = resolve_archetype(mix_profile, "chords" if tonal_map[role] == "pad" else "lead", valence=valence)
    fallback = {
        "kick": ("gl.kick", "Deep 808"), "snare": ("gl.snare", "Tight"), "clap": ("gl.clap", "Room"),
        "hihat": ("gl.hat", "Closed"), "perc": ("gl.clap", "Room"), "rim": ("gl.snare", "Rim"),
        "bass": ("gl.analog", ""), "808": ("gl.analog", ""), "sub": ("gl.analog", ""),
        "chords": ("gl.keys", "Classic tine"), "pad": ("gl.analog", "Velvet Pad"),
        "lead": ("gl.analog", "Glass Pluck"), "counter": ("gl.additive", "Harp pluck"),
        "arp": ("gl.additive", "Harp pluck"), "arpeggio": ("gl.additive", "Harp pluck"),
        "pluck": ("gl.additive", "Harp pluck"), "vocals": ("gl.sampler", ""), "fx": ("gl.wavetable", "Formant Choir"),
    }
    fb_plug, fb_preset = fallback.get(role) or fallback.get(tonal_map.get(role, role), ("gl.warmsynth", "Soft Brass"))
    plugin = (arch or {}).get("plugin") or fb_plug
    preset = str((arch or {}).get("preset") or "")
    # Jamais renvoyer `gl.sampler` comme repli synthé natif (sans fichier sample -> silence !)
    if plugin not in SYNTH_REGISTRY or (plugin == "gl.sampler" and role != "vocals"):
        plugin = fb_plug
        preset = fb_preset
    elif not preset and plugin == fb_plug:
        preset = fb_preset
    params = dict((arch or {}).get("params") or {})
    # garde-fou de confort FORCÉ sur les rôles tonaux : attaque >= 2 ms, release >= 60 ms,
    # cutoff <= 9 kHz — et sur chords/pad en valence >= -0.2 : attaque >= 8 ms, cutoff <= 6.5 kHz (§5)
    if role in tonal_map:
        min_att = 8.0 if (valence >= -0.2 and tonal_map[role] in ("chords", "pad")) else 2.0
        max_cut = 6500.0 if (valence >= -0.2 and tonal_map[role] in ("chords", "pad")) else 9000.0
        params["attack_ms"] = max(min_att, float(params.get("attack_ms", min_att)))
        params["release_ms"] = max(60.0, float(params.get("release_ms", 60.0)))
        params["cutoff_hz"] = min(float(params.get("cutoff_hz", max_cut)), max_cut)
    out = {"plugin": plugin, "params": params, "archetype": bool(arch)}
    if preset:
        out["preset"] = preset
    return out


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Gate d'agréabilité des timbres (PASS/FIX/SWAP) + cohérence de palette")
    ap.add_argument("--role", default="lead_melody")
    ap.add_argument("--metrics", default="{}", help='JSON ex. {"r_harsh":0.6,"roughness":0.3}')
    ap.add_argument("--plugin", nargs=3, metavar=("ROLE", "PROFILE", "VALENCE"), default=None)
    args = ap.parse_args()
    if args.plugin:
        role, prof, val = args.plugin[0], args.plugin[1], float(args.plugin[2])
        print(json.dumps(pick_gl_plugin(role, prof, val), ensure_ascii=False, indent=2))
        return 0
    print(json.dumps(pleasantness_gate(json.loads(args.metrics), args.role), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
