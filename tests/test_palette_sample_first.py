#!/usr/bin/env python3
"""Chantier P0/P1/P2 — politique « Sample-First », mapping batterie, propagation
plugin_params, bornes EQ/garde-fous DSP, hygiène des automations et structure.

Couvre les critères d'acceptation du document de spécifications « correction et
amélioration — qualité sonore, sélection de samples et mixage » (DoD §7.1).
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

ROOT = os.environ.get("AI_STUDIO_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
AUDIO_INDEX = os.path.join(ROOT, "mcp-servers", "audio-index")
PROD = os.path.join(ROOT, "mcp-servers", "ai-studio-prod")
for _p in (SCRIPTS, AUDIO_INDEX, PROD):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np

from gl_dsl import resolve_archetype, sanitize_automations
from dsp_interaction_guards import (
    EQ_BUS_GAIN_BOUNDS,
    EQ_TRACK_GAIN_BOUNDS,
    KICK_FUNDAMENTAL_MAX_CUT_DB,
    clamp_eq_band_gain,
    cumulative_mid_attenuation_db,
    enforce_mid_budget,
    find_duplicate_plugins,
    protect_kick_fundamental,
)


def _sample_entry(filename: str, role: str, midi_root: int = 48, playable: bool = True,
                  timbre_card: str = "", centroid_hz: float = 900.0, comfort: float = 0.85,
                  role_score: float = 0.8) -> dict:
    return {
        "filepath": f"/tmp/fakebank/{filename}",
        "filename": filename,
        "category": role,
        "is_loop": False,
        "bpm": None,
        "duration_sec": 1.2,
        "compatible_roles": [role],
        "role_scores": {r: (role_score if r == role else 0.1) for r in
                        ("kick", "snare_clap", "hihat_perc", "bass_sub", "chord_keys", "pad_atmosphere", "lead_melody")},
        "comfort_score": comfort,
        "playable_chromatically": playable,
        "midi_root": midi_root,
        "recommended_gl_fine_tune_ct": 0,
        "centroid_hz": centroid_hz,
        "attack_ms": 8.0,
        "hard_reject": False,
        "timbre_card": timbre_card or filename,
        "inharmonicity": 0.0,
        "bark_24": None,
    }


def _write_catalog(proj: str, samples: list[dict]) -> str:
    os.makedirs(proj, exist_ok=True)
    path = os.path.join(proj, "samples_catalog.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"samples": samples, "user_samples_locked": True}, fh)
    return path


class TestPaletteSampleFirst(unittest.TestCase):
    """DoD 7.1.1 — couverture samples sur tous les rôles + trace + préférence basse calme."""

    ROLES = ["kick", "snare_clap", "hihat_perc", "bass_sub", "chord_keys", "lead_melody"]

    def _bank(self):
        return [
            _sample_entry("kick_deep.wav", "kick", midi_root=36, playable=False, centroid_hz=120.0),
            _sample_entry("snare_dry.wav", "snare_clap", midi_root=38, playable=False, centroid_hz=1800.0),
            _sample_entry("hat_closed.wav", "hihat_perc", midi_root=42, playable=False, centroid_hz=7000.0),
            _sample_entry("sub_808_clean.wav", "bass_sub", midi_root=36, timbre_card="sub 808 propre ronde"),
            _sample_entry("chords_piano_soft.wav", "chord_keys", midi_root=48, timbre_card="piano doux feutré"),
            _sample_entry("lead_flute.wav", "lead_melody", midi_root=60, timbre_card="flute chaude organique"),
        ]

    def test_all_roles_get_samples_and_trace(self):
        from palette_builder import propose_palettes
        with tempfile.TemporaryDirectory() as td:
            _write_catalog(td, self._bank())
            res = propose_palettes(
                mix_profile="trap_rap",
                emotion_vector={"valence": -0.5, "tension": 0.4, "energy": 0.5, "depth": 0.5},
                target_key="C minor",
                roles=self.ROLES,
                project_samples_dir=td,
                bpm=90.0,
            )
        best = res["palettes"][0]
        slots = best["slots"]
        self.assertEqual(len(slots), len(self.ROLES))
        n_samples = sum(1 for s in slots if s["source_type"] in ("sample", "project_sample") and s.get("filepath"))
        self.assertEqual(n_samples, len(self.ROLES), f"tous les rôles doivent charger un sample: {[(s['role'], s['source_type']) for s in slots]}")
        self.assertIn("selection_trace", res)
        self.assertIn("n_sample_candidates", res["selection_trace"])

    def test_perc_role_gets_sample_too(self):
        from palette_builder import propose_palettes
        bank = self._bank() + [_sample_entry("rim_acoustic.wav", "hihat_perc", midi_root=37, playable=False,
                                             timbre_card="rimshot sec", centroid_hz=2500.0)]
        # perc partage le rôle hihat_perc ; on vérifie via un rôle perc dédié au catalogue
        bank[-1]["compatible_roles"] = ["hihat_perc"]
        with tempfile.TemporaryDirectory() as td:
            _write_catalog(td, bank)
            res = propose_palettes(mix_profile="trap_rap",
                                   emotion_vector={"valence": -0.5, "energy": 0.5},
                                   target_key="C minor", roles=["hihat_perc", "bass_sub"],
                                   project_samples_dir=td, bpm=90.0)
        self.assertTrue(any(s["source_type"] == "project_sample" for s in res["palettes"][0]["slots"]))

    def test_calm_context_prefers_sub_over_reese(self):
        from palette_builder import propose_palettes
        bank = self._bank() + [
            _sample_entry("reese_a1_bright.wav", "bass_sub", midi_root=33,
                          timbre_card="reese distorted aggressive metallic saw", comfort=0.9, role_score=0.95),
        ]
        with tempfile.TemporaryDirectory() as td:
            _write_catalog(td, bank)
            res = propose_palettes(mix_profile="trap_rap",
                                   emotion_vector={"valence": -0.6, "tension": 0.3, "energy": 0.4, "depth": 0.5},
                                   target_key="C minor", roles=["bass_sub"],
                                   project_samples_dir=td, bpm=90.0)
        best_slot = res["palettes"][0]["slots"][0]
        self.assertIn("sub_808_clean", best_slot["filepath"])

    def test_fallback_trace_when_no_sample(self):
        from palette_builder import propose_palettes
        with tempfile.TemporaryDirectory() as td:
            _write_catalog(td, [])  # banque vide : fallback gridline_native tracé
            res = propose_palettes(mix_profile="trap_rap",
                                   emotion_vector={"valence": -0.5, "energy": 0.5},
                                   target_key="C minor", roles=["lead_melody"],
                                   project_samples_dir=td, bpm=90.0)
        slot = res["palettes"][0]["slots"][0]
        self.assertEqual(slot["source_type"], "gridline_native")
        self.assertIsNone(slot.get("fallback_reason"))  # pas de sample dispo -> pas un "fallback contraint"
        self.assertIn("selection_trace", res)


class TestArchetypesAndDrumMapping(unittest.TestCase):
    """DoD 7.1.2 — archétypes explicites y compris batterie ; perc/rim jamais cloné sur HiHat."""

    def test_drum_tracks_get_archetype(self):
        from blueprint_enricher import enrich_blueprint
        bp = {
            "title": "t", "bpm": 90, "key": "C", "mix_profile": "trap_rap",
            "time_signature": {"numerator": 4, "denominator": 4},
            "has_drums": True,
            "harmonic_core": {"key": "C", "mode": "minor", "chord_progression": []},
            "structure": [{"section": "Intro", "start_bar": 1, "bars": 4}],
            "tracks": [
                {"name": "kick", "role": "drums", "source_type": "gridline_native", "plugin": "gl.kick", "mixer_track": 1},
                {"name": "snare", "role": "drums", "source_type": "gridline_native", "plugin": "gl.snare", "mixer_track": 2},
                {"name": "hihat", "role": "drums", "source_type": "gridline_native", "plugin": "gl.hat", "mixer_track": 3},
                {"name": "perc", "role": "drums", "source_type": "gridline_native", "plugin": "gl.clap", "mixer_track": 4},
                {"name": "bass", "role": "bass", "source_type": "gridline_native", "plugin": "gl.warmsynth", "mixer_track": 5},
                {"name": "chords", "role": "chords", "source_type": "gridline_native", "plugin": "gl.keys", "mixer_track": 6},
                {"name": "lead", "role": "lead", "source_type": "gridline_native", "plugin": "gl.warmsynth", "mixer_track": 7},
            ],
        }
        bp2, _report = enrich_blueprint(bp)
        by_name = {t["name"]: t for t in bp2["tracks"]}
        for name, expected in (("kick", "trap_rap:kick"), ("snare", "trap_rap:snare"),
                               ("hihat", "trap_rap:hihat"), ("perc", "trap_rap:perc"),
                               ("lead", "trap_rap:lead")):
            self.assertEqual(by_name[name].get("archetype"), expected, f"archétype manquant pour {name}")

    def test_perc_channel_is_distinct_from_hihat(self):
        import song_builder
        plan = {"channels": {}}
        roles = ["kick", "snare", "hihat", "perc", "bass", "chords", "lead"]
        with tempfile.TemporaryDirectory() as td:
            chans = song_builder._default_channels_for_plan(plan, roles, td, valence=-0.5)
        by_key = {c.track_key: c for c in chans}
        hat, perc = by_key["hihat"], by_key["perc"]
        self.assertNotEqual(perc.name, "HiHat")
        self.assertNotEqual(perc.name, hat.name)
        self.assertNotEqual(perc.plugin, hat.plugin)
        self.assertNotEqual(perc.pan, hat.pan)
        self.assertEqual(hat.pan, 0.22)
        self.assertEqual(perc.pan, -0.18)

    def test_no_calm_profile_lead_defaults_to_metallic_fm(self):
        for profile in ("trap_rap", "lofi_chill", "pop_rnb", "jazz_acoustic", "classical_ambient"):
            arch = resolve_archetype(profile, "lead", valence=-0.4)
            self.assertIsNotNone(arch)
            self.assertNotEqual(arch["plugin"], "gl.fm",
                                f"{profile}:lead ne doit plus tomber sur gl.fm (ratio inharmonique)")
            cutoff = float((arch.get("params") or {}).get("cutoff_hz", 16000.0))
            self.assertLessEqual(cutoff, 9000.0, f"{profile}:lead cutoff trop ouvert: {cutoff}")


class TestSamplerParamPropagation(unittest.TestCase):
    """DoD 7.1.3 — cutoff_hz du blueprint réellement appliqué au Cutoff de gl.sampler."""

    def test_cutoff_hz_propagates_to_sampler_cutoff(self):
        import song_builder
        ch = song_builder.ChannelSpec("Bass", "bass", 4, "bass", plugin="gl.sampler",
                                      sample_file="/tmp/fakebank/reese_a1_clean.wav", root_note=33)
        ch.plugin_params.update({"cutoff_hz": 9000.0, "attack_ms": 2, "release_ms": 120, "glide_ms": 60})
        patches, params = song_builder._sampler_param_changes(1, ch, "/tmp", {})
        d = dict(params)
        self.assertEqual(d["channel/1/Filter"], 1)
        self.assertEqual(d["channel/1/Cutoff"], 9000.0)
        self.assertAlmostEqual(d["channel/1/Attack"], 0.002, places=4)
        self.assertAlmostEqual(d["channel/1/Release"], 0.120, places=4)
        self.assertAlmostEqual(d["channel/1/Glide"], 0.060, places=4)
        self.assertEqual(d["channel/1/Root note"], 33)
        self.assertNotIn("channel/1/cutoff_hz", d)  # jamais de nom de param brut
        self.assertEqual(patches[0]["set"]["plugin"]["data"], "/tmp/fakebank/reese_a1_clean.wav")

    def test_effective_cutoff_is_most_restrictive(self):
        import song_builder
        ch = song_builder.ChannelSpec("Bass", "bass", 4, "bass", plugin="gl.sampler",
                                      sample_file="x.wav", lpf_hz=20000.0)
        ch.plugin_params.update({"cutoff_hz": 900.0})
        self.assertEqual(song_builder.sampler_effective_cutoff_hz(ch), 900.0)
        ch2 = song_builder.ChannelSpec("Bass", "bass", 4, "bass", plugin="gl.sampler", sample_file="x.wav")
        self.assertIsNone(song_builder.sampler_effective_cutoff_hz(ch2))


class TestEqBoundsAndDspGuards(unittest.TestCase):
    """DoD 7.1.4 — bornes EQ bus/piste, budget cumulatif médiums, doublons, gl.dist organic."""

    def test_clamp_eq_band_gain_bounds(self):
        self.assertEqual(clamp_eq_band_gain(-12.0, is_bus=True), EQ_BUS_GAIN_BOUNDS[0])  # -4.5
        self.assertEqual(clamp_eq_band_gain(9.0, is_bus=True), EQ_BUS_GAIN_BOUNDS[1])      # +3.0
        self.assertEqual(clamp_eq_band_gain(-12.0, is_bus=False), EQ_TRACK_GAIN_BOUNDS[0])  # -6.0
        self.assertEqual(clamp_eq_band_gain(9.0, is_bus=False), EQ_TRACK_GAIN_BOUNDS[1])    # +4.0

    def test_cumulative_mid_budget(self):
        track = [{"type": "peak", "freq_hz": 300.0, "gain_db": -3.0},
                 {"type": "peak", "freq_hz": 480.0, "gain_db": -2.8},
                 {"type": "peak", "freq_hz": 1600.0, "gain_db": -3.2}]
        bus = [{"type": "peak", "freq_hz": 315.0, "gain_db": -12.0},
               {"type": "peak", "freq_hz": 630.0, "gain_db": -12.0},
               {"type": "peak", "freq_hz": 1250.0, "gain_db": -7.89},
               {"type": "hpf", "freq_hz": 135.0, "gain_db": 0.0}]
        cur = cumulative_mid_attenuation_db(track, bus)
        self.assertLess(cur, -7.0)  # le cas destructeur du spec est bien détecté
        t2, b2, total = enforce_mid_budget(track, bus)
        self.assertGreaterEqual(total, -7.0 - 1e-6)

    def test_kick_fundamental_protection(self):
        bands = [{"type": "peak", "freq_hz": 50.0, "gain_db": -9.5},
                 {"type": "peak", "freq_hz": 95.0, "gain_db": 3.0}]
        out = protect_kick_fundamental(bands)
        self.assertEqual(out[0]["gain_db"], KICK_FUNDAMENTAL_MAX_CUT_DB)  # -3.0
        self.assertEqual(out[1]["gain_db"], 3.0)

    def test_duplicate_plugin_detection(self):
        dups = find_duplicate_plugins({"gl.stereo": [4, 5], "gl.eq": [1], "gl.dist": [0]})
        self.assertEqual(dups, {"gl.stereo": [5]})

    def test_kick_eq_helper_never_cuts_fundamental(self):
        import song_builder
        d = dict(song_builder.eq_kick_clean(1, sub_cut_db=-9.5))
        self.assertGreaterEqual(d["mixer/1/slot/1/Band 2 gain"], -3.0)

    def test_solver_eq_bands_within_track_bounds_and_kick_protected(self):
        from scipy import signal
        from math_mix_solver import solve_mix_parameters
        sr = 22050
        rng = np.random.default_rng(7)
        dur = 8.0
        n = int(sr * dur)
        t = np.arange(n) / sr
        stems = {}
        meta = {}
        for name, f0 in (("kick", 50.0), ("bass", 55.0), ("chords", 220.0), ("lead", 880.0)):
            x = np.sin(2 * np.pi * f0 * t) * (0.5 + 0.5 * np.sin(2 * np.pi * 2.0 * t))
            x += 0.05 * rng.standard_normal(n)
            stems[name] = (np.stack([x, x], axis=1), sr)
            meta[name] = {"role": name, "wetness_score": 0.5, "initial_pan": 0.0}
        sol = solve_mix_parameters(stems, meta, "trap_rap",
                                   {"valence": -0.5, "tension": 0.4, "energy": 0.5, "depth": 0.5}, 90.0)
        for nm, tr in sol["tracks"].items():
            for b in tr["eq_bands"]:
                if b["type"] in ("peak", "low_shelf", "high_shelf"):
                    self.assertGreaterEqual(b["gain_db"], EQ_TRACK_GAIN_BOUNDS[0], f"{nm} {b}")
                    self.assertLessEqual(b["gain_db"], EQ_TRACK_GAIN_BOUNDS[1], f"{nm} {b}")
                    if nm == "kick" and 45.0 <= b["freq_hz"] <= 70.0:
                        self.assertGreaterEqual(b["gain_db"], KICK_FUNDAMENTAL_MAX_CUT_DB)


class _FakeMCP:
    def __init__(self):
        self.added: list[dict] = []
        self.params: list[tuple] = []

    def call(self, tool, args):
        if tool == "get_mixer":
            return {"tracks": [{"mixer": i, "name": f"Insert {i}", "slots": []} for i in range(23)]}
        return {}

    def batch(self, calls):
        for c in calls:
            if c.get("tool") == "add_plugin":
                self.added.append(c["arguments"])

    def set_params(self, changes):
        self.params.extend(changes)

    def close(self):
        pass


class TestBusChainHygiene(unittest.TestCase):
    """DoD 7.1.4 (suite) — BUS Music : pas de gl.dist non sollicité, pas de double gl.stereo."""

    def _plugins_on(self, mcp, mixer):
        return [a["plugin"] for a in mcp.added if a.get("mixer") == mixer]

    def test_organic_theme_has_no_dist_and_single_stereo(self):
        from studio_lib import setup_standard_buses
        mcp = _FakeMCP()
        setup_standard_buses(mcp, bpm=90.0, profile="trap_rap", valence=-0.5,
                             theme="organic", energy=0.5)
        bus19 = self._plugins_on(mcp, 19)
        self.assertNotIn("gl.dist", bus19)
        self.assertEqual(bus19.count("gl.stereo"), 1)
        self.assertEqual(self._plugins_on(mcp, 18).count("gl.stereo"), 1)

    def test_rock_metal_keeps_dist(self):
        from studio_lib import setup_standard_buses
        mcp = _FakeMCP()
        setup_standard_buses(mcp, bpm=140.0, profile="rock_metal", valence=0.4, theme="", energy=0.9)
        bus19 = self._plugins_on(mcp, 19)
        self.assertIn("gl.dist", bus19)
        self.assertEqual(bus19.count("gl.stereo"), 1)

    def test_bus_bass_lpf_applied_when_requested(self):
        from studio_lib import setup_standard_buses
        mcp = _FakeMCP()
        setup_standard_buses(mcp, bpm=90.0, profile="trap_rap", valence=-0.5,
                             bus_bass_lpf_hz=1200.0)
        band7 = [v for k, v in mcp.params if k == "mixer/18/slot/3/Band 7 type"]
        freq7 = [v for k, v in mcp.params if k == "mixer/18/slot/3/Band 7 freq"]
        self.assertIn(3, band7)  # lpf
        self.assertIn(1200.0, freq7)


class TestAutomationHygiene(unittest.TestCase):
    """DoD 7.1.5 — 0 collision (owner, index), 0 clip plat."""

    def test_flat_clips_dropped(self):
        flat = {"name": "chords vel->cutoff", "mixer": 3, "slot": 1, "param": "Band 7 freq",
                "start": 0.0, "length": 16.0,
                "points": [{"position": 0.0, "value": 0.4895}, {"position": 16.0, "value": 0.4895}]}
        kept, rep = sanitize_automations([flat])
        self.assertEqual(kept, [])
        self.assertEqual(rep["dropped_flat"], ["chords vel->cutoff"])

    def test_overlapping_same_target_deduped(self):
        prosody = {"name": "Prosody LPF chords", "mixer": 3, "slot": 1, "param": "Band 7 freq",
                   "start": 0.0, "length": 16.0,
                   "points": [{"position": 0.0, "value": 0.3}, {"position": 16.0, "value": 0.8}]}
        dupe = {"name": "chords vel->cutoff", "mixer": 3, "slot": 1, "param": "Band 7 freq",
                "start": 4.0, "length": 8.0,
                "points": [{"position": 4.0, "value": 0.5}, {"position": 12.0, "value": 0.6}]}
        other = {"name": "Bass Gain", "mixer": 4, "param": "volume",
                 "start": 0.0, "length": 16.0,
                 "points": [{"position": 0.0, "value": 0.4}, {"position": 16.0, "value": 0.9}]}
        kept, rep = sanitize_automations([prosody, dupe, other])
        names = [k["name"] for k in kept]
        self.assertIn("Prosody LPF chords", names)
        self.assertIn("Bass Gain", names)
        self.assertNotIn("chords vel->cutoff", names)
        self.assertEqual(len(rep["dropped_collisions"]), 1)

    def test_velocity_cutoff_skips_degenerate_range(self):
        from phrasing_engine import velocity_cutoff_automation
        autos: list[dict] = []
        tracks = {"chords": [{"start": 0.0, "length": 1.0, "pitch": 60, "velocity": 90}]}
        rep = velocity_cutoff_automation(tracks, [("Main", 0, 4, "chorus")], autos, 90.0,
                                         track_key="chords", mixer_idx=3, lo_hz=6500.0, hi_hz=6500.0)
        self.assertEqual(rep["applied"], 0)
        self.assertEqual(autos, [])


class TestStructureStartBar(unittest.TestCase):
    """DoD 7.1.5 — structure[*].start_bar cumulatif (base 1)."""

    def test_cumulative_start_bar(self):
        import prod_server
        sections = [{"name": "Intro", "bars": 4}, {"name": "Verse", "bars": 16}, {"name": "Chorus", "bars": 8},
                    {"name": "Verse2", "bars": 16}, {"name": "Chorus2", "bars": 8}, {"name": "Bridge", "bars": 8},
                    {"name": "Outro", "bars": 8}]
        st = prod_server.blueprint_structure(sections)
        self.assertEqual([s["start_bar"] for s in st], [1, 5, 21, 29, 45, 53, 61])
        self.assertEqual([s["bars"] for s in st], [4, 16, 8, 16, 8, 8, 8])


class TestSampleFallbackGate(unittest.TestCase):
    """DoD 7.1.1 (gate) — ratio samples_used / tracks_with_available_samples >= 0.7."""

    def _bp(self, sources):
        return {"tracks": [{"name": n, "role": "drums" if n in ("kick", "snare", "hihat", "perc") else n,
                            "source_type": s, "mixer_track": i + 1,
                            **({"sample_path": f"/tmp/{n}.wav", "root_note": 36} if s == "sample" else {"plugin": "gl.hat"})}
                           for i, (n, s) in enumerate(sources)]}

    def test_gate_fails_below_70_percent(self):
        from audit_project_sources import sample_fallback_gate
        bp = self._bp([("kick", "sample"), ("snare", "gridline_native"), ("hihat", "gridline_native"),
                       ("perc", "gridline_native"), ("bass", "gridline_native"), ("chords", "gridline_native"),
                       ("lead", "gridline_native")])
        palette = {"slots": [{"role": r, "source_type": "project_sample", "filepath": f"/tmp/{r}.wav"}
                             for r in ("kick", "snare_clap", "hihat_perc", "bass_sub", "chord_keys", "lead_melody")]}
        res = sample_fallback_gate(bp, palette)
        self.assertEqual(res["gate"], "FAIL")
        self.assertLess(res["sample_coverage_ratio"], 0.7)
        self.assertTrue(res["fallback_tracks"])
        res_ok = sample_fallback_gate(bp, palette, allow_override=True)
        self.assertEqual(res_ok["gate"], "PASS")

    def test_gate_passes_when_all_samples(self):
        from audit_project_sources import sample_fallback_gate
        names = ["kick", "snare", "hihat", "perc", "bass", "chords", "lead"]
        bp = self._bp([(n, "sample") for n in names])
        palette = {"slots": [{"role": r, "source_type": "project_sample", "filepath": f"/tmp/{r}.wav"}
                             for r in ("kick", "snare_clap", "hihat_perc", "bass_sub", "chord_keys", "lead_melody")]}
        res = sample_fallback_gate(bp, palette)
        self.assertEqual(res["gate"], "PASS")
        self.assertEqual(res["n_samples"], 7)


if __name__ == "__main__":
    unittest.main()
