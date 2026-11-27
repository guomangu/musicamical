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
) -> np.ndarray:
    """Une note rendue NATIVEMENT à sa hauteur exacte (aucun pitch-shift).

    Synthèse additive : partiels 1..N avec décroissance 1/k^rolloff, légère inharmonicité
    pour les timbres 'bell', vibrato optionnel, enveloppe ADSR, filtre passe-bas one-pole.
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
        release_s=float(t.get("release_s", 0.08)),
        duration_s=duration_s,
    )
    wave = wave * env
    cutoff = float(t.get("lowpass_hz", 18000.0))
    if cutoff < sr / 2.0 - 100.0:
        alpha = 1.0 - math.exp(-2.0 * math.pi * cutoff / sr)
        wave = lfilter([alpha], [1.0, -(1.0 - alpha)], wave)
    return wave.astype(np.float32)


def render_notes(
    notes: Iterable[tuple[float, float, float, float]],
    sr: int = 44100,
    timbre: dict[str, Any] | None = None,
) -> np.ndarray:
    """Rend une piste tonal note par note à hauteur native. `notes` = (midi, start_s, dur_s, vel 0-127)."""
    notes = list(notes)
    if not notes:
        return np.zeros(int(0.1 * sr), dtype=np.float32)
    total = int((max(start + dur for _m, start, dur, _v in notes) + 0.25) * sr)
    out = np.zeros(total, dtype=np.float64)
    for midi, start, dur, vel in notes:
        seg = render_note(midi, dur, sr=sr, timbre=timbre)
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
        return render_notes(notes, sr=sr, timbre=timbre), "builtin"

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
    ) -> dict[str, Any]:
        """Rendu d'une piste avec application stricte de la politique anti-pitch-shift."""
        check_render_policy(role, source_type, semitone_shift)
        if role in TONAL_ROLES:
            audio, backend = self.render_preset_notes(
                notes, vst3_path=vst3_path, sr=sr, timbre=timbre or self.default_timbre(role)
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
            "n_notes": len(list(notes)),
            "semitone_shift": 0.0,
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
