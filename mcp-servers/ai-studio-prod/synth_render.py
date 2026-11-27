#!/usr/bin/env python3
"""Moteur de synthétiseur programmable pour les pistes tonales (Chantier 2 de la spec).

Objectif : éliminer le re-pitching destructif des one-shots WAV (`derived_samples/multizone/`)
pour toutes les parties mélodiques et harmoniques. Deux moteurs de rendu, séparés strictement :

  - Instruments virtuels (`vst3_preset` / `sfz_instrument`) : rendus NATIVEMENT à la hauteur
    MIDI exacte. Backend `DawDreamer` (VST3 headless, ex. Surge XT / Vital / Dexed) quand la
    librairie est installée ; sinon repli déterministe `builtin` (synthèse additive NumPy/SciPy
    avec ADSR + filtre passe-bas) qui produit chaque note à sa fréquence exacte — aucun
    pitch-shifting, donc aucun déplacement de formants.
  - Échantillons WAV (`wav_sample`) : réservés aux rôles percussifs (kick, snare, hihat, perc,
    fx) joués à leur hauteur native ; tolérance maximale de ±2 demi-tons (accord du kick/tom),
    appliquée par `check_render_policy`.

Usage :
  .venv/bin/python -c "from synth_render import render_notes; print(render_notes([(60,0.0,0.5,100)]))"
"""
from __future__ import annotations

import math
import os
from typing import Any, Iterable

import numpy as np

PERCUSSIVE_ROLES = {"kick", "snare", "hihat", "perc", "fx"}
TONAL_ROLES = {"bass_808", "pad", "keys", "lead", "arp"}
MAX_PERCUSSIVE_SHIFT_ST = 2  # tolérance d'accord kick/tom uniquement

try:
    import dawdreamer as _dawdreamer  # type: ignore

    _HAS_DAW_DREAMER = True
except Exception:  # pragma: no cover - DawDreamer optionnel (voir requirements.txt)
    _dawdreamer = None
    _HAS_DAW_DREAMER = False


class PitchShiftForbidden(RuntimeError):
    """Levée quand un one-shot WAV serait pitch-shifté au-delà de la tolérance (rôles tonaux)."""


def midi_to_hz(midi: float) -> float:
    return 440.0 * (2.0 ** ((midi - 69.0) / 12.0))


def check_render_policy(role: str, source_type: str, semitone_shift: float = 0.0) -> None:
    """Politique stricte de séparation des moteurs de rendu (spec §2.1).

    - rôle tonal + `wav_sample` -> interdit (utiliser vst3_preset / sfz_instrument) ;
    - rôle percussif + `wav_sample` -> |shift| <= 2 st (accord kick/tom) ;
    - rôle tonal + instrument virtuel -> toujours autorisé (rendu natif).
    """
    if role in TONAL_ROLES and source_type == "wav_sample":
        raise PitchShiftForbidden(
            f"rôle tonal '{role}' interdit en one-shot WAV pitch-shifté : "
            "utiliser un preset vst3_preset / sfz_instrument rendu à la hauteur native"
        )
    if source_type == "wav_sample" and abs(semitone_shift) > MAX_PERCUSSIVE_SHIFT_ST:
        raise PitchShiftForbidden(
            f"transposition de {semitone_shift:+.1f} st > ±{MAX_PERCUSSIVE_SHIFT_ST} st interdite sur WAV '{role}'"
        )


# ---------------------------------------------------------------------------
# v2 — Automation expressive déterministe (spec v2.0 §2.1, 3 couches synchronisées au tempo)
# ---------------------------------------------------------------------------

def velocity_cutoff_factor(vel: float, base: float = 0.5, delta_max: float = 0.5) -> float:
    """Couche 1 — couplage vélocité → brillance (micro-dynamique par note) :
    u(n) = base + delta_max * (v_n - 64) / 64, clampé [0, 1]. Les notes douces sont plus
    feutrées (filtre fermé), les accents s'ouvrent harmoniquement."""
    return float(min(1.0, max(0.0, base + delta_max * ((float(vel) - 64.0) / 64.0))))


def macro_breathing_curve(n_bars: int, role: str, bars_span: tuple[int, int] = (8, 16),
                          depth: float = 0.15) -> list[float]:
    """Couche 2 — respiration macro-structurelle (pad/lead/keys) : facteur de coupure du filtre
    modulé en sinusoïde de ±`depth` sur une période de 8 à 16 mesures (évite la fatigue auditive
    des boucles répétées). Retourne un facteur par mesure (1.0 ailleurs)."""
    n = max(1, int(n_bars))
    if role not in ("pad", "lead", "keys"):
        return [1.0] * n
    span = float(min(max(n, bars_span[0]), bars_span[1]))
    return [1.0 + depth * math.sin(2.0 * math.pi * (b / span)) for b in range(n)]


def adaptive_release_s(duration_s: float, next_gap_s: float, base_release_s: float,
                       min_release_s: float = 0.03, max_release_s: float = 0.8) -> float:
    """Couche 3 — release adapté à la durée de note et à l'intervalle inter-notes Δt_next :
    assez long pour respirer en fin de phrase, jamais assez long pour chevaucher la note
    suivante de façon boueuse (basses/accords rapides)."""
    gap = max(0.0, float(next_gap_s))
    return float(min(max_release_s, max(min_release_s, min(float(base_release_s), 0.9 * gap + 0.25 * float(duration_s)))))


def build_automation(
    notes: list[tuple[float, float, float, float]],
    bpm: float,
    role: str,
    timbre: dict[str, Any] | None = None,
    base_cutoff_norm: float = 0.5,
) -> dict[str, Any]:
    """Construit les 3 courbes d'automation pour une piste (0 rendu, déterministe).

    Retourne {"per_note": [{cutoff_u, release_s, bar}], "cc74_curve": [(t_s, u)],
              "macro_curve": [...], "bpm": bpm, "role": role} — prêt à être traduit en
    automation CC74 / paramètres VST3 par le worker (`prod_worker.py`)."""
    t = timbre or {}
    base_lp = float(t.get("lowpass_hz", 12000.0))
    base_rel = float(t.get("release_s", 0.08))
    beat_s = 60.0 / float(bpm) if bpm else 0.5
    bar_s = 4.0 * beat_s
    total_s = max((s + d for _m, s, d, _v in notes), default=0.0) if notes else 0.0
    n_bars = max(1, int(math.ceil(total_s / bar_s))) if bar_s > 0 else 1
    macro = macro_breathing_curve(n_bars, role)
    per_note = []
    cc74 = []
    for i, (midi, start, dur, vel) in enumerate(notes):
        next_gap = (notes[i + 1][1] - (start + dur)) if i + 1 < len(notes) else 4.0 * beat_s
        u_vel = velocity_cutoff_factor(vel, base=base_cutoff_norm)
        bar_idx = min(n_bars - 1, int(start / bar_s)) if bar_s > 0 else 0
        u = float(min(1.0, max(0.0, u_vel * macro[bar_idx])))
        rel = adaptive_release_s(dur, next_gap, base_rel)
        per_note.append({"note_index": i, "midi": midi, "start_s": round(float(start), 4),
                         "cutoff_u": round(u, 4), "release_s": round(rel, 4), "bar": bar_idx})
        cc74.append((round(float(start), 4), round(u, 4)))
    return {
        "bpm": float(bpm), "role": role, "base_lowpass_hz": base_lp, "base_release_s": base_rel,
        "n_notes": len(per_note),
        "per_note": per_note, "cc74_curve": cc74, "macro_curve": [round(x, 4) for x in macro],
        "n_bars": n_bars,
    }


def _cutoff_scale_to_lowpass(base_lowpass_hz: float, cutoff_u: float) -> float:
    """u=0 -> filtre très fermé (0.3x), u=1 -> ouvert (2.0x) : plage de brillance expressive."""
    return base_lowpass_hz * (0.3 + 1.7 * float(min(1.0, max(0.0, cutoff_u))))


def adsr(n_samples: int, sr: int, attack_s: float, decay_s: float, sustain_level: float,
         release_s: float, duration_s: float) -> np.ndarray:
    """Enveloppe ADSR déterministe (sustain maintenu jusqu'à la fin de la note)."""
    a = max(1, int(attack_s * sr))
    d = max(1, int(decay_s * sr))
    r = max(1, int(release_s * sr))
    total = min(n_samples, a + d + max(1, int((duration_s - attack_s - decay_s) * sr)) + r)
    env = np.zeros(total, dtype=np.float64)
    a_n = min(a, total)
    env[:a_n] = np.linspace(0.0, 1.0, a_n)
    d_n = min(d, total - a_n)
    if d_n > 0:
        env[a_n : a_n + d_n] = np.linspace(1.0, sustain_level, d_n)
    s_n = max(0, total - a_n - d_n - r)
    if s_n > 0:
        env[a_n + d_n : a_n + d_n + s_n] = sustain_level
    r_n = total - (a_n + d_n + s_n)
    if r_n > 0:
        env[a_n + d_n + s_n :] = np.linspace(sustain_level, 0.0, r_n)
    if total < n_samples:
        env = np.concatenate([env, np.zeros(n_samples - total)])
    return env[:n_samples]


def render_note(
    midi: float,
    duration_s: float,
    sr: int = 44100,
    timbre: dict[str, Any] | None = None,
    cutoff_u: float | None = None,
    release_s: float | None = None,
) -> np.ndarray:
    """Une note rendue NATIVEMENT à sa hauteur exacte (aucun pitch-shift).

    Synthèse additive : partiels 1..N avec décroissance 1/k^rolloff, légère inharmonicité
    pour les timbres 'bell', vibrato optionnel, enveloppe ADSR, filtre passe-bas one-pole.
    `cutoff_u` (automation v2) module l'ouverture du filtre, `release_s` surcharge le release.
    """
    from scipy.signal import lfilter

    t = timbre or {}
    f0 = midi_to_hz(midi)
    n = max(1, int(duration_s * sr))
    time = np.arange(n) / sr
    n_harm = int(t.get("harmonics", 12))
    rolloff = float(t.get("rolloff", 1.2))
    inharm = float(t.get("inharmonicity", 0.0))
    vib_rate = float(t.get("vibrato_rate_hz", 5.0))
    vib_depth_ct = float(t.get("vibrato_depth_ct", 0.0))
    if vib_depth_ct > 0:
        f_inst = f0 * (2.0 ** ((vib_depth_ct * np.sin(2.0 * math.pi * vib_rate * time)) / 1200.0))
    else:
        f_inst = np.full(n, f0)
    phase_f0 = 2.0 * math.pi * np.cumsum(f_inst) / sr
    wave = np.zeros(n, dtype=np.float64)
    for k in range(1, n_harm + 1):
        freq_k = f0 * k * (1.0 + inharm * k * k)
        if freq_k >= sr / 2.0:
            break
        amp = (1.0 / (k ** rolloff)) * float(t.get(f"h{k}_gain", 1.0))
        wave += amp * np.sin(k * phase_f0)
    wave /= max(1e-9, np.max(np.abs(wave)))
    env = adsr(
        n, sr,
        attack_s=float(t.get("attack_s", 0.005)),
        decay_s=float(t.get("decay_s", 0.1)),
        sustain_level=float(t.get("sustain", 0.7)),
        release_s=float(t.get("release_s", 0.08)) if release_s is None else float(release_s),
        duration_s=duration_s,
    )
    wave = wave * env
    base_lp = float(t.get("lowpass_hz", 18000.0))
    cutoff = _cutoff_scale_to_lowpass(base_lp, cutoff_u) if cutoff_u is not None else base_lp
    if cutoff < sr / 2.0 - 100.0:
        alpha = 1.0 - math.exp(-2.0 * math.pi * cutoff / sr)
        wave = lfilter([alpha], [1.0, -(1.0 - alpha)], wave)
    return wave.astype(np.float32)


def render_notes(
    notes: Iterable[tuple[float, float, float, float]],
    sr: int = 44100,
    timbre: dict[str, Any] | None = None,
    bpm: float | None = None,
    role: str | None = None,
) -> np.ndarray:
    """Rend une piste tonale note par note à hauteur native. `notes` = (midi, start_s, dur_s, vel 0-127).

    v2 : si `bpm` + `role` sont fournis, applique les 3 couches d'automation expressive
    (`build_automation`) : couplage vélocité→cutoff, respiration macro par mesure, release adaptatif.
    """
    notes = list(notes)
    if not notes:
        return np.zeros(int(0.1 * sr), dtype=np.float32)
    auto = None
    if bpm and role:
        auto = build_automation(notes, float(bpm), role, timbre=timbre)
    total = int((max(start + dur for _m, start, dur, _v in notes) + 0.25) * sr)
    out = np.zeros(total, dtype=np.float64)
    for i, (midi, start, dur, vel) in enumerate(notes):
        cutoff_u = auto["per_note"][i]["cutoff_u"] if auto else None
        rel = auto["per_note"][i]["release_s"] if auto else None
        seg = render_note(midi, dur, sr=sr, timbre=timbre, cutoff_u=cutoff_u, release_s=rel)
        i0 = int(start * sr)
        i1 = min(total, i0 + seg.size)
        out[i0:i1] += seg[: i1 - i0] * (float(vel) / 127.0)
    peak = float(np.max(np.abs(out))) + 1e-9
    if peak > 0.999:
        out = out / peak * 0.999
    return out.astype(np.float32)


class SynthEngine:
    """Moteur de rendu hybride : DawDreamer (VST3) si disponible, sinon synthé builtin native."""

    def __init__(self, prefer: str = "auto") -> None:
        self.backend = "dawdreamer" if (_HAS_DAW_DREAMER and prefer in ("auto", "dawdreamer")) else "builtin"

    @property
    def has_dawdreamer(self) -> bool:
        return _HAS_DAW_DREAMER

    def render_preset_notes(
        self,
        notes: Iterable[tuple[float, float, float, float]],
        *,
        vst3_path: str | None = None,
        preset_index: int = 0,
        sr: int = 44100,
        timbre: dict[str, Any] | None = None,
        bpm: float | None = None,
        role: str | None = None,
    ) -> tuple[np.ndarray, str]:
        """Rend via VST3 (DawDreamer) si possible, sinon builtin. Retourne (audio, backend_utilisé)."""
        notes = list(notes)
        if self.backend == "dawdreamer" and vst3_path and os.path.isfile(vst3_path):
            try:
                engine = _dawdreamer.DAW(sr=sr)
                instrument = engine.addVST3(vst3_path, preset_index)
                for midi, start, dur, vel in notes:
                    engine.addMidiNote(midi, start, dur, vel, track=instrument)
                audio = engine.render()
                mono = np.asarray(audio, dtype=np.float32)
                if mono.ndim > 1:
                    mono = mono.mean(axis=0)
                return mono, "dawdreamer"
            except Exception:
                pass  # repli builtin déterministe
        return render_notes(notes, sr=sr, timbre=timbre, bpm=bpm, role=role), "builtin"

    def render_role(
        self,
        role: str,
        notes: Iterable[tuple[float, float, float, float]],
        *,
        source_type: str = "vst3_preset",
        semitone_shift: float = 0.0,
        vst3_path: str | None = None,
        preset_name: str | None = None,
        sr: int = 44100,
        timbre: dict[str, Any] | None = None,
        bpm: float | None = None,
    ) -> dict[str, Any]:
        """Rendu d'une piste avec application stricte de la politique anti-pitch-shift.

        v2 : `bpm` active les 3 couches d'automation expressive (vélocité→filtre, respiration
        macro, release adaptatif) ; le rapport d'automation est joint au résultat."""
        check_render_policy(role, source_type, semitone_shift)
        if role in TONAL_ROLES:
            notes = list(notes)
            auto = build_automation(notes, bpm, role, timbre=timbre or self.default_timbre(role)) if bpm else None
            audio, backend = self.render_preset_notes(
                notes, vst3_path=vst3_path, sr=sr, timbre=timbre or self.default_timbre(role),
                bpm=bpm, role=role,
            )
        else:
            raise PitchShiftForbidden(
                f"rôle percussif '{role}' : utiliser un lecteur WAV à hauteur native, pas le synthétiseur"
            )
        return {
            "audio": audio,
            "sr": sr,
            "backend": backend,
            "preset_name": preset_name,
            "role": role,
            "n_notes": len(notes),
            "semitone_shift": 0.0,
            "automation": auto,
        }

    @staticmethod
    def default_timbre(role: str) -> dict[str, Any]:
        base = {
            "bass_808": {"harmonics": 6, "rolloff": 1.6, "attack_s": 0.003, "decay_s": 0.25,
                         "sustain": 0.85, "release_s": 0.05, "lowpass_hz": 900.0},
            "pad": {"harmonics": 10, "rolloff": 1.1, "attack_s": 0.35, "decay_s": 0.4,
                    "sustain": 0.8, "release_s": 0.6, "lowpass_hz": 5200.0},
            "keys": {"harmonics": 8, "rolloff": 1.4, "attack_s": 0.004, "decay_s": 0.5,
                     "sustain": 0.4, "release_s": 0.12, "lowpass_hz": 8000.0},
            "lead": {"harmonics": 12, "rolloff": 1.0, "attack_s": 0.01, "decay_s": 0.2,
                     "sustain": 0.75, "release_s": 0.15, "lowpass_hz": 12000.0,
                     "vibrato_rate_hz": 5.5, "vibrato_depth_ct": 8.0},
            "arp": {"harmonics": 9, "rolloff": 1.2, "attack_s": 0.005, "decay_s": 0.15,
                    "sustain": 0.5, "release_s": 0.08, "lowpass_hz": 9000.0},
        }
        return base.get(role, base["keys"])


def main() -> int:
    engine = SynthEngine()
    notes = [(60, 0.0, 0.4, 100), (64, 0.4, 0.4, 100), (67, 0.8, 0.4, 100), (72, 1.2, 0.8, 110)]
    out = engine.render_role("lead", notes, source_type="vst3_preset", sr=22050)
    print(
        f"backend={out['backend']} role={out['role']} notes={out['n_notes']} "
        f"samples={out['audio'].size} sr={out['sr']} peak={float(np.max(np.abs(out['audio']))):.3f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
