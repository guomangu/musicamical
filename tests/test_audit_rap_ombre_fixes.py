#!/usr/bin/env python3
"""Tests de non-régression de l'audit « rap_ombre_134 » (§6) + verrous des valeurs P0-P2 :

- §6(a) intégrité pitch percussif : 100 % des notes kick/snare/perc/open_hat == root_note
  du sampler (±2 st toléré sur les hi-hats) après `autofix_score_ir` / `lock_percussive_root_notes` ;
- §6(b) registre/accordage 808 : 100 % des notes bass dans MIDI [26, 41] et dans la classe
  de hauteur fondamentale de l'accord actif (`enforce_bass_register`) ;
- §6(c) anti-fuite taxonomique : aucun sample de `samples/other/` ni à tags tonaux
  (stab/chord/pluck/pad/synth/lead) assignable à snare_clap/kick (`role_allows_asset`,
  `filter_role_compatible`, `candidate_policy`) ;
- §6(d) seuils bloquants de palette : refus si S_palette < 0.60, cohérence < 0.60 ou
  max_bark_collision > 0.65 (`validate_palette_gate`, `select_palette`, `_resolve_palette`),
  + auto-correction de collision (`resolve_bark_collision` : pad +12 st, LPF, pan) ;
- valeurs P1-6/P2-7/P2-8 : archétypes trap_rap (cutoffs pad/lead, release 808), EQ kick,
  legato/glide 808, variation B du chorus.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

ROOT = os.environ.get("AI_STUDIO_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
for _d in (SCRIPTS, os.path.join(ROOT, "mcp-servers", "audio-index"), os.path.join(ROOT, "mcp-servers", "ai-studio-prod")):
    if _d not in sys.path:
        sys.path.insert(0, _d)

from score_ir import (  # noqa: E402
    BASS_REGISTER_MAX,
    BASS_REGISTER_MIN,
    autofix_score_ir,
    build_score_ir,
    enforce_bass_register,
    lock_percussive_root_notes,
    segment_index_for_beat,
)

BPM = 140.0


def _meta(bars: int = 8) -> dict:
    return {"bpm": BPM, "beats_per_bar": 4, "denominator": 4, "valence": -0.6,
            "total_bars": bars, "tonic": "C", "key": "C"}


def _score(tracks: dict, bars: int = 8):
    hc = {"key": "C", "mode": "minor", "degree_progression": ["i", "VI", "III", "VII"]}
    return build_score_ir(hc, _meta(bars), tracks)


class TestPercPitchIntegrity(unittest.TestCase):
    """§6(a) : invariant `pitch_note == root_note` sur toute piste percussive."""

    def test_autofix_locks_perc_notes_to_root(self):
        tracks = {
            "kick": [{"start": float(i), "length": 0.25, "pitch": 36 + (i % 3), "velocity": 100} for i in range(8)],
            "snare": [{"start": 1.0 + i * 2.0, "length": 0.25, "pitch": 38 + 7 * (i % 2), "velocity": 100} for i in range(4)],
            "perc": [{"start": 0.5 + i, "length": 0.125, "pitch": 37 + 13, "velocity": 90} for i in range(8)],
            "open_hat": [{"start": 3.5 + i * 4.0, "length": 0.5, "pitch": 46 + 5, "velocity": 90} for i in range(2)],
            "bass": [{"start": 0.0, "length": 4.0, "pitch": 36, "velocity": 100}],
            "lead": [{"start": 0.0, "length": 1.0, "pitch": 72, "velocity": 80}],
        }
        score = _score(tracks)
        score, report = autofix_score_ir(score)
        self.assertIn("perc_pitch_lock", report)
        self.assertGreaterEqual(report["perc_pitch_lock"]["locked"], 1)
        for tkey in ("kick", "snare", "perc", "open_hat"):
            root = max(set(n.pitch for n in score.tracks[tkey].notes),
                       key=lambda p: [n.pitch for n in score.tracks[tkey].notes].count(p))
            for n in score.tracks[tkey].notes:
                self.assertEqual(n.pitch, root, f"{tkey}: note {n.pitch} != root {root}")

    def test_lock_uses_sampler_root_note_when_provided(self):
        tracks = {
            "snare": [{"start": float(i), "length": 0.25, "pitch": 43 + i, "velocity": 100} for i in range(4)],
            "hihat": [{"start": float(i) * 0.5, "length": 0.25, "pitch": 44, "velocity": 80} for i in range(8)],
        }
        score = _score(tracks)
        score.tracks["hihat"].role = "hihat"  # rôle explicite (comme via channels)
        rep = lock_percussive_root_notes(score, root_notes={"snare": 38, "hihat": 42})
        self.assertEqual(rep["locked"], 4)          # snare 43..46 -> 38
        self.assertEqual(rep["kept_hat_shift"], 8)   # hihat 44 = 42 + 2 st : toléré
        self.assertTrue(all(n.pitch == 38 for n in score.tracks["snare"].notes))
        self.assertTrue(all(abs(n.pitch - 42) <= 2 for n in score.tracks["hihat"].notes))

    def test_enforce_bass_register_called_by_autofix(self):
        tracks = {"bass": [{"start": float(i), "length": 1.0, "pitch": 49 + i, "velocity": 100} for i in range(8)]}
        score = _score(tracks)
        score, report = autofix_score_ir(score)
        self.assertIn("bass_register", report)
        for n in score.tracks["bass"].notes:
            self.assertGreaterEqual(n.pitch, BASS_REGISTER_MIN)
            self.assertLessEqual(n.pitch, BASS_REGISTER_MAX)


class TestBassRegisterAndRootPc(unittest.TestCase):
    """§6(b) : 100 % des notes bass dans MIDI [26,41] et pc = fondamentale de l'accord actif."""

    def test_bass_notes_in_26_41_and_root_pc(self):
        # bug audit : basse écrasée sur MIDI 49 (C#3) hors gamme D mineur
        tracks = {"bass": [
            {"start": 0.0, "length": 4.0, "pitch": 49, "velocity": 100},
            {"start": 4.0, "length": 4.0, "pitch": 53, "velocity": 100},
            {"start": 8.0, "length": 4.0, "pitch": 38, "velocity": 100},
            {"start": 12.0, "length": 4.0, "pitch": 26, "velocity": 100},
            {"start": 16.0, "length": 4.0, "pitch": 61, "velocity": 100},
        ]}
        score = _score(tracks, bars=8)
        rep = enforce_bass_register(score)
        self.assertGreaterEqual(rep["clamped"] + rep["root_locked"], 1)
        for n in score.tracks["bass"].notes:
            self.assertGreaterEqual(n.pitch, 26, f"hors [26,41] : {n.pitch}")
            self.assertLessEqual(n.pitch, 41, f"hors [26,41] : {n.pitch}")
            cidx = segment_index_for_beat(score.harmonic_grid, n.grid_beat)
            seg = score.harmonic_grid[cidx]
            root_pc = int(seg.get("bass_pc", seg.get("root_pc", 0))) % 12
            self.assertEqual(n.pitch % 12, root_pc,
                             f"beat {n.grid_beat}: pitch {n.pitch} pc {n.pitch % 12} != root pc {root_pc}")

    def test_root_pc_formula_handles_lo_not_pc0(self):
        # lo=26 -> 26%12=2 : la cible pour root_pc=0 (C) doit être 36, pas 26
        tracks = {"bass": [{"start": 0.0, "length": 4.0, "pitch": 49, "velocity": 100}]}
        score = _score(tracks, bars=8)  # progression i = Cm -> root_pc 0
        enforce_bass_register(score)
        p = score.tracks["bass"].notes[0].pitch
        self.assertEqual(p % 12, 0)
        self.assertEqual(p, 36)


class TestTaxonomyCloisoning(unittest.TestCase):
    """§6(c) : aucun sample de `samples/other/` ou à tags tonaux assignable à snare_clap/kick."""

    def test_role_allows_asset_rejects_other_dir_and_tonal_tokens(self):
        import unified_indexer as ui
        # le sample fautif de l'audit : Steel Stab One Shot_D.wav dans samples/other/
        ok, reason = ui.role_allows_asset("snare", "samples/other/Steel Stab One Shot_D.wav",
                                          is_pitched=1, pitch_confidence=0.97, ac_roughness=12.0, air_band_energy=0.02)
        self.assertFalse(ok, reason)
        ok, _ = ui.role_allows_asset("kick", "samples/other/kick_synth.wav", is_pitched=0)
        self.assertFalse(ok)
        ok, _ = ui.role_allows_asset("snare_clap" if "snare_clap" in ui.PERC_ROLE_CATEGORIES else "snare",
                                     "samples/snare/Steel Stab.wav", is_pitched=1)
        self.assertFalse(ok)
        # un vrai snare (bruit large bande, non pitché) passe
        ok, _ = ui.role_allows_asset("snare", "samples/snare/clap_acoustic.wav",
                                     is_pitched=0, pitch_confidence=0.1, ac_roughness=60.0, air_band_energy=0.4)
        self.assertTrue(ok)
        # un vrai kick passe
        ok, _ = ui.role_allows_asset("kick", "samples/kick/808_kick.wav", is_pitched=0)
        self.assertTrue(ok)

    def test_snare_acoustic_guard_rejects_pitched_stab(self):
        import unified_indexer as ui
        ok, reason = ui.role_allows_asset("snare", "samples/snare/stab_D.wav",
                                          is_pitched=1, pitch_confidence=0.95, ac_roughness=10.0, air_band_energy=0.03)
        self.assertFalse(ok, reason)
        # hihat/perc pitchés haute confiance rejetés
        ok, _ = ui.role_allows_asset("hihat", "samples/hihat/hh.wav", is_pitched=1, pitch_confidence=0.99)
        self.assertFalse(ok)

    def test_filter_role_compatible_and_candidate_policy(self):
        import unified_indexer as ui
        from sample_fit_guard import candidate_policy
        rows = [
            {"file_path": "samples/other/Steel Stab One Shot_D.wav", "is_pitched": 1, "pitch_confidence": 0.97,
             "ac_roughness": 12.0, "air_band_energy": 0.02, "category": "other"},
            {"file_path": "samples/snare/clap.wav", "is_pitched": 0, "pitch_confidence": 0.1,
             "ac_roughness": 60.0, "air_band_energy": 0.4, "category": "snare"},
        ]
        kept = ui.filter_role_compatible(rows, "snare")
        self.assertEqual([r["file_path"] for r in kept], ["samples/snare/clap.wav"])
        verdict = candidate_policy("snare_clap", rows[0])
        self.assertFalse(verdict["allowed"])
        self.assertEqual(verdict["tier"], "forbidden")
        self.assertTrue(any("taxonomy_lock" in r for r in verdict["reasons"]))
        verdict_kick = candidate_policy("kick", {"filepath": "samples/other/Steel Stab One Shot_D.wav",
                                                  "is_pitched": 1, "pitch_confidence": 0.97})
        self.assertFalse(verdict_kick["allowed"])


class TestPaletteBlockingGate(unittest.TestCase):
    """§6(d) : le pipeline échoue/relance si palette.score < 0.60 ou max_bark_collision > 0.65."""

    def test_validate_palette_gate_thresholds(self):
        import palette_selector as ps
        # valeurs de l'audit : score 0.3805, cohérence 0.35, collision 0.846 -> refusé
        g = ps.validate_palette_gate({"S_palette": 0.3805, "M_spectral": 0.65, "max_bark_collision": 0.846})
        self.assertFalse(g["accepted"])
        self.assertEqual(len(g["reasons"]), 3)
        self.assertTrue(any(r.startswith("S_palette") for r in g["reasons"]))
        self.assertTrue(any(r.startswith("coherence") for r in g["reasons"]))
        self.assertTrue(any(r.startswith("max_bark_collision") for r in g["reasons"]))
        g = ps.validate_palette_gate({"S_palette": 0.5999, "M_spectral": 0.0, "max_bark_collision": 0.0})
        self.assertFalse(g["accepted"])
        g = ps.validate_palette_gate({"S_palette": 0.60, "M_spectral": 0.40, "max_bark_collision": 0.65})
        self.assertTrue(g["accepted"], g)

    def test_resolve_bark_collision_applies_octave_lpf_pan(self):
        import palette_selector as ps
        # pad et chords identiques en high-mid -> collision 1.0
        pad = {"file_path": "", "low_band_energy": 0.0, "low_mid_energy": 0.0, "high_mid_energy": 1.0, "air_band_energy": 0.0}
        chords = {"file_path": "", "low_band_energy": 0.0, "low_mid_energy": 0.0, "high_mid_energy": 1.0, "air_band_energy": 0.0}
        kick = {"file_path": "", "low_band_energy": 1.0, "low_mid_energy": 0.0, "high_mid_energy": 0.0, "air_band_energy": 0.0}
        palette = {"pad": pad, "chords": chords, "kick": kick}
        self.assertGreater(ps.max_bark_collision(palette), ps.PALETTE_MAX_BARK_COLLISION)
        fix = ps.resolve_bark_collision(palette)
        self.assertGreater(fix["max_bark_collision"], ps.PALETTE_MAX_BARK_COLLISION)
        roles = {a["role"] for a in fix["applied"]}
        self.assertIn("pad", roles)
        self.assertTrue(any(a.get("octave_up_st") == 12 for a in fix["applied"]))
        self.assertTrue(any(a.get("lpf_hz") == 1800.0 for a in fix["applied"]))
        self.assertTrue(any("pan" in a for a in fix["applied"]))

    def test_select_palette_reports_gate(self):
        import unified_indexer as ui
        import palette_selector as ps
        defaults = dict(
            source_type="wav_sample", preset_name=None, is_pitched=0, detected_midi_note=None,
            pitch_confidence=0.0, min_safe_midi=21, max_safe_midi=108, duration_ms=400.0,
            log_attack_ms=1.0, lufs_integrated=-18.0, crest_factor_db=10.0, spectral_centroid_hz=1000.0,
            low_band_energy=0.25, low_mid_energy=0.25, high_mid_energy=0.4, air_band_energy=0.1,
            ac_warmth=50.0, ac_brightness=50.0, ac_roughness=20.0, ac_hardness=50.0,
            ac_depth=50.0, ac_boomy=30.0, ac_sharpness=30.0, embedding_blob=None,
        )

        def _insert(conn, asset_id, role, **m):
            d = dict(defaults)
            d.update(m)
            row = {"asset_id": asset_id, "file_path": f"/nonexistent/{asset_id}.wav", "role_category": role, **d}
            cols = ", ".join(row.keys())
            conn.execute(f"INSERT OR REPLACE INTO sound_assets ({cols}) VALUES ({', '.join('?' * len(row))})",
                         tuple(row.values()))
            conn.commit()

        with tempfile.TemporaryDirectory() as td:
            db = os.path.join(td, "samples.db")
            conn = ui.init_db(db)
            _insert(conn, "k1", "kick", ac_hardness=80.0, crest_factor_db=15.0, spectral_centroid_hz=120.0,
                    low_band_energy=0.7, low_mid_energy=0.2)
            _insert(conn, "s1", "snare", spectral_centroid_hz=2500.0, ac_brightness=75.0,
                    air_band_energy=0.3, high_mid_energy=0.5)
            _insert(conn, "h1", "hihat", air_band_energy=0.5, ac_roughness=10.0, spectral_centroid_hz=7000.0)
            _insert(conn, "b1", "bass_808", spectral_centroid_hz=80.0, low_band_energy=0.8)
            _insert(conn, "p1", "pad", spectral_centroid_hz=900.0, ac_roughness=10.0, ac_warmth=80.0,
                    high_mid_energy=0.6)
            _insert(conn, "l1", "lead", is_pitched=1, spectral_centroid_hz=3000.0, ac_brightness=85.0,
                    air_band_energy=0.2)
            conn.close()
            res = ps.select_palette(db_path=db, profile="trap_rap")
        self.assertIn("gate", res)
        self.assertIn("accepted", res)
        self.assertEqual(res["accepted"], res["gate"]["accepted"])
        self.assertIn("max_bark_collision", res)
        if res["max_bark_collision"] > ps.PALETTE_MAX_BARK_COLLISION:
            self.assertIn("collision_fix", res)
        else:
            self.assertNotIn("collision_fix", res)

    def test_resolve_palette_rejects_all_weak_palettes(self):
        import palette_builder
        import prod_server
        bp = {
            "title": "t", "genre": "trap_rap", "mix_profile": "trap_rap", "has_drums": True,
            "seed": 1, "emotion_vector": {"valence": 0.0, "tension": 0.5, "energy": 0.7, "depth": 0.5},
            "bpm": 140.0, "key": "C", "harmonic_core": {"key": "C", "mode": "minor"},
            "tracks": [{"name": "kick", "role": "drums", "source_type": "gridline_native",
                        "plugin": "gl.kick", "mixer_track": 1}],
        }
        orig = palette_builder.propose_palettes
        weak = {"palettes": [{"palette_id": "palette_C", "score": 0.3805, "theme": "synthetic",
                              "spectral_overlap": 0.65, "max_bark_collision": 0.846,
                              "slots": [{"role": "kick", "source_type": "gridline_native", "plugin": "gl.kick",
                                         "filepath": None, "comfort_score": 0.9}]}]}

        def fake_propose(**kwargs):
            return weak

        palette_builder.propose_palettes = fake_propose
        try:
            with tempfile.TemporaryDirectory() as td:
                proj = os.path.join(td, "proj")
                os.makedirs(proj)
                out = prod_server._resolve_palette(proj, bp, "auto")
                self.assertFalse(out.get("resolved"), out)
                self.assertTrue(str(out.get("reason", "")).startswith("palette_gate_rejected"), out)
                self.assertFalse(out["palette_gate"]["accepted"])
                # échappatoire explicite ALLOW_WEAK_PALETTE=1
                os.environ["ALLOW_WEAK_PALETTE"] = "1"
                try:
                    out2 = prod_server._resolve_palette(proj, bp, "auto")
                    self.assertTrue(out2.get("resolved"), out2)
                finally:
                    del os.environ["ALLOW_WEAK_PALETTE"]
        finally:
            palette_builder.propose_palettes = orig


class TestTrapArchetypesAndMixValues(unittest.TestCase):
    """P1-6/P2-7/P2-8 : valeurs idiomatiques trap_rap verrouillées."""

    def test_trap_archetype_cutoffs_and_808_release(self):
        import json
        with open(os.path.join(ROOT, "config", "timbre_archetypes.json"), encoding="utf-8") as fh:
            arch = json.load(fh)["archetypes"]
        pad = arch["trap_rap:pad"]["params"]
        self.assertGreaterEqual(pad["cutoff_hz"], 900)
        self.assertLessEqual(pad["cutoff_hz"], 2200)
        self.assertGreaterEqual(arch["trap_rap:pad"]["dark_overrides"]["cutoff_hz"], 900)
        lead = arch["trap_rap:lead"]["params"]
        self.assertGreaterEqual(lead["cutoff_hz"], 1800)
        self.assertLessEqual(lead["cutoff_hz"], 3500)
        bass = arch["trap_rap:bass"]["params"]
        self.assertGreaterEqual(bass["release_ms"], 250)
        self.assertLessEqual(bass["release_ms"], 450)
        self.assertGreaterEqual(bass["glide_ms"], 45)
        self.assertLessEqual(bass["glide_ms"], 60)

    def test_kick_eq_no_sub_cut_hpf_30(self):
        import song_builder
        d = dict(song_builder.eq_kick_clean(1))
        self.assertEqual(d["mixer/1/slot/1/Band 2 gain"], 0.0)   # plus de cut -3 dB @ 50 Hz
        self.assertEqual(d["mixer/1/slot/1/Band 1 freq"], 30.0)   # HPF 28-30 Hz
        self.assertEqual(d["mixer/1/slot/1/Band 4 gain"], -2.0)   # 210 Hz allégé
        clamped = dict(song_builder.eq_kick_clean(1, sub_cut_db=-9.5))
        self.assertGreaterEqual(clamped["mixer/1/slot/1/Band 2 gain"], -3.0)

    def test_808_legato_glide_marks_pitch_jumps(self):
        import strudel_to_score_ir as bridge
        tracks = {"bass": [
            {"start": 0.0, "length": 1.0, "pitch": 38, "velocity": 100},
            {"start": 1.0, "length": 1.0, "pitch": 45, "velocity": 100},  # legato + saut de quinte -> glide
            {"start": 2.0, "length": 1.0, "pitch": 38, "velocity": 100},
            {"start": 4.0, "length": 1.0, "pitch": 45, "velocity": 100},  # espacé 2 temps -> legato 1.98
        ]}
        score = _score(tracks, bars=2)
        haps = {"tracks": {"bass": {"opts": {"glide": 50}}}}
        rep = bridge.apply_808_legato_glide(score, haps)
        self.assertGreaterEqual(rep["glide_marked"], 1)
        glides = [n for n in score.tracks["bass"].notes if n.expression.articulation == "glide_to_next"]
        self.assertTrue(glides)
        for n in glides:
            self.assertGreaterEqual(n.expression.glide_ms, 35.0)
            self.assertLessEqual(n.expression.glide_ms, 90.0)
        # legato : durées allongées vers 1.5-3.5 temps (cut-itself)
        self.assertGreaterEqual(rep["legato_extended"], 1)

    def test_chorus_variation_b_breaks_repetition(self):
        from narrative_harmony import chorus_variation_b
        notes = [{"start": float(i), "length": 1.0, "pitch": 72, "velocity": 72} for i in range(128)]
        tracks = {"lead": notes, "chords": [{"start": 0.0, "length": 128.0, "pitch": 60, "velocity": 70}]}
        score = _score(tracks, bars=32)
        spans = [("Chorus", 16, 16, "chorus")]
        before = [n.grid_beat for n in score.tracks["lead"].notes]
        n = chorus_variation_b(score, section_spans=spans)
        self.assertGreaterEqual(n, 1)
        after = sorted(n.grid_beat for n in score.tracks["lead"].notes)
        # 2e moitié du chorus (bars 24-31) modifiée : syncope et/ou notes d'approche ajoutées
        self.assertNotEqual(before, after)
        moved = [n for n in score.tracks["lead"].notes if n.grid_beat not in before]
        self.assertTrue(moved)


if __name__ == "__main__":
    unittest.main()
