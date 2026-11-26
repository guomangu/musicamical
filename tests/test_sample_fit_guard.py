#!/usr/bin/env python3
"""v4.2 — Tests de non-régression complets des failles F1–F11 (`docs/ANALYSE_FAILLES_SONORITES.md`)
et CI banc réel (trap_rap, lofi_chill, classical_ambient)."""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.environ.get("AI_STUDIO_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
INDEX_DIR = os.path.join(ROOT, "mcp-servers", "audio-index")
PROD_DIR = os.path.join(ROOT, "mcp-servers", "ai-studio-prod")
for _p in (SCRIPTS, INDEX_DIR, PROD_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402

import sample_fit_guard as sfg  # noqa: E402
from blueprint_safety import blueprint_musical_safety_issues, summarize_musical_safety  # noqa: E402
from timbre_pleasantness import pleasantness_gate  # noqa: E402

SR = 22050
NODE = shutil.which("node")
HAVE_STRUDEL = bool(NODE and os.path.isdir(os.path.join(SCRIPTS, "node_modules", "@strudel", "core")))


def _write_wav(path: str, samples: np.ndarray, sr: int = SR) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    sf.write(path, np.asarray(samples, dtype=np.float32), sr)
    return path


def _saturated_808(f0: float = 55.0, dur: float = 0.8, sr: int = SR) -> np.ndarray:
    """808 saturé avec partiels forts 2*f0, 3*f0, 4*f0 (même fondamental)."""
    t = np.arange(int(dur * sr)) / sr
    env = np.minimum(1.0, t / 0.006) * np.exp(-t / 0.45)
    sig = (
        0.75 * np.sin(2 * np.pi * f0 * t)
        + 0.45 * np.sin(2 * np.pi * 2 * f0 * t)
        + 0.35 * np.sin(2 * np.pi * 3 * f0 * t)
        + 0.18 * np.sin(2 * np.pi * 4 * f0 * t)
    )
    return (0.7 * sig * env).astype(np.float32)


def _cm_chord(dur: float = 1.2, sr: int = 44100) -> np.ndarray:
    """Vrai accord Cm (C4 + Eb4 + G4) — non harmonique mutuellement."""
    t = np.arange(int(dur * sr)) / sr
    sig = (
        0.35 * np.sin(2 * np.pi * 261.63 * t)
        + 0.35 * np.sin(2 * np.pi * 311.13 * t)
        + 0.35 * np.sin(2 * np.pi * 392.00 * t)
    )
    return sig.astype(np.float32)


def _catalog_entry(
    filepath: str,
    role: str,
    category: str,
    midi_root: int,
    *,
    playable: bool = True,
    sample_nature: str = "mono_oneshot",
    inharmonicity: float = 0.01,
    centroid_hz: float = 900.0,
    r_harsh: float = 0.08,
    roughness: float = 0.05,
    peak_harsh_db: float = 4.0,
    crest_factor_db: float = 8.5,
    comfort: float = 0.88,
    role_score: float = 0.82,
    timbre_card: str = "",
) -> dict:
    return {
        "filepath": filepath,
        "filename": os.path.basename(filepath),
        "category": category,
        "is_loop": False,
        "bpm": None,
        "duration_sec": 0.9,
        "compatible_roles": [role],
        "role_scores": {
            r: (role_score if r == role else 0.1)
            for r in ("kick", "snare_clap", "hihat_perc", "bass_sub", "chord_keys", "pad_atmosphere", "lead_melody")
        },
        "comfort_score": comfort,
        "sample_nature": sample_nature,
        "playable_chromatically": playable,
        "midi_root": midi_root,
        "recommended_gl_fine_tune_ct": 0,
        "centroid_hz": centroid_hz,
        "attack_ms": 10.0,
        "r_harsh": r_harsh,
        "roughness": roughness,
        "peak_harsh_db": peak_harsh_db,
        "crest_factor_db": crest_factor_db,
        "inharmonicity": inharmonicity,
        "hard_reject": False,
        "timbre_card": timbre_card or os.path.basename(filepath),
        "bark_24": None,
    }


class TestF1AndF7BlueprintSafetyAndCompile(unittest.TestCase):
    """F1 : gate S_max alimenté (score_ir + cascade catalogue) ; F7 : zéro faux positif drums."""

    def test_f1_compile_song_blocks_on_fx_root24_chords(self):
        from compile_song import compile_song
        with tempfile.TemporaryDirectory() as td:
            fx_path = _write_wav(os.path.join(td, "samples", "fx", "fx_bell.wav"), _saturated_808(65.4))
            cat = {"samples": [
                _catalog_entry(fx_path, "chord_keys", "fx", midi_root=24,
                               sample_nature="inharmonic_bell_or_fx", inharmonicity=0.35),
            ]}
            with open(os.path.join(td, "samples_catalog.json"), "w", encoding="utf-8") as fh:
                json.dump(cat, fh)
            # score_ir : accords joués en 50..72 -> S_max = 48 st >> cap 7
            with open(os.path.join(td, "score_ir.json"), "w", encoding="utf-8") as fh:
                json.dump({"tracks": {"chords": {"notes": [{"pitch": 50}, {"pitch": 60}, {"pitch": 72}]}}}, fh)
            bp = {
                "title": "t", "genre": "trap_rap", "mix_profile": "trap_rap", "has_drums": True,
                "seed": 1, "emotion_vector": {"valence": 0.0, "tension": 0.5, "energy": 0.7, "depth": 0.5},
                "bpm": 128.0, "time_signature": {"numerator": 4, "denominator": 4},
                "key": "C",
                "harmonic_core": {
                    "key": "C", "mode": "minor", "progression_bars": 1,
                    "chord_progression": [{"bar": 1, "symbol": "Cm7", "bass_midi": 36, "voicing_midi": [60, 63, 67]}],
                    "motif_cell": {"intervals_from_tonic": [0, 3, 7], "rhythm_beats": [0, 2], "apex_midi": 72},
                    "phrase_plan": "A-A'-A''-B",
                },
                "structure": [{"section": "verse", "start_bar": 1, "bars": 8, "energy": 0.7}],
                "tracks": [
                    {"name": "chords", "role": "chords", "source_type": "sample", "plugin": "gl.sampler",
                     "sample_path": fx_path, "root_note": 24, "mixer_track": 1},
                ],
            }
            with open(os.path.join(td, "song_blueprint.json"), "w", encoding="utf-8") as fh:
                json.dump(bp, fh)
            rep = compile_song(td)
            self.assertFalse(rep["ok"])
            self.assertEqual(rep["stage"], "blueprint_safety")
            codes = {i["code"] for i in rep["musical_safety"]["issues"] if i["severity"] == "error"}
            self.assertIn("max_semitone_shift_exceeded", codes)
            self.assertIn("inharmonic_bell_confined", codes)

    def test_f1_catalog_cascade_and_missing_warning(self):
        with tempfile.TemporaryDirectory() as td:
            sub = os.path.join(td, "proj")
            os.makedirs(sub)
            bp = {"key": "C minor", "bpm": 128, "tracks": [
                {"name": "lead", "role": "lead", "source_type": "sample",
                 "sample_path": "/nonexistent/lead.wav", "root_note": 60, "mixer_track": 1},
            ]}
            summary = summarize_musical_safety(bp, catalog_path=None)
            self.assertTrue(summary["catalog_missing"])
            self.assertTrue(any(i["code"] == "catalog_missing" for i in summary["issues"]))
            # Cascade vers palette.project_samples_dir/../samples_catalog.json
            bank = os.path.join(td, "bank")
            os.makedirs(os.path.join(bank, "samples"))
            root_cat = os.path.join(bank, "samples_catalog.json")
            with open(root_cat, "w", encoding="utf-8") as fh:
                json.dump({"samples": []}, fh)
            resolved = sfg.resolve_catalog_path(
                sub, bp={"palette": {"project_samples_dir": os.path.join(bank, "samples")}}, root_dir=td
            )
            self.assertEqual(resolved, root_cat)

    def test_f7_drums_no_false_positive_root_note_mismatch(self):
        with tempfile.TemporaryDirectory() as td:
            entries = []
            tracks = []
            for idx, (name, notated, detected) in enumerate(
                [("kick", 36, 48), ("snare", 38, 67), ("hihat", 42, 84), ("perc", 39, 75)], 1
            ):
                p = os.path.join(td, "samples", name, f"{name}.wav")
                entries.append(_catalog_entry(p, "kick", name if name != "hihat" else "hat",
                                              midi_root=detected, playable=False, sample_nature="percussion"))
                tracks.append({"name": name, "role": "drums", "source_type": "sample", "plugin": "gl.sampler",
                               "sample_path": p, "root_note": notated, "mixer_track": idx})
            cat_path = os.path.join(td, "samples_catalog.json")
            with open(cat_path, "w", encoding="utf-8") as fh:
                json.dump({"samples": entries}, fh)
            bp = {"key": "C minor", "bpm": 128, "tracks": tracks}
            issues = blueprint_musical_safety_issues(bp, catalog_path=cat_path)
            self.assertEqual([i for i in issues if i["code"] == "root_note_mismatch"], [])


class TestF2Saturated808VsChord(unittest.TestCase):
    """F2 : un 808 saturé reste mono_oneshot jouable ; un vrai accord Cm reste poly_chord_or_dyad."""

    def test_f2_saturated_808_playable_and_cm_chord_rejected(self):
        from audio_metrics import analyze_single_file
        with tempfile.TemporaryDirectory() as td:
            p808 = _write_wav(os.path.join(td, "808_sat_a1.wav"), _saturated_808(55.0))
            pcm = _write_wav(os.path.join(td, "chord_cm.wav"), _cm_chord(), sr=44100)
            e808, _ = analyze_single_file(p808, with_clap=False)
            ecm, _ = analyze_single_file(pcm, with_clap=False)
            self.assertEqual(e808["dsp"]["sample_nature"], "mono_oneshot", e808["dsp"])
            self.assertTrue(e808["dsp"]["playable_chromatically"], e808["dsp"])
            self.assertIn(ecm["dsp"]["sample_nature"], ("poly_chord_or_dyad", "melodic_sequence"), ecm["dsp"])
            self.assertFalse(ecm["dsp"]["playable_chromatically"], ecm["dsp"])


class TestF3F4F5F6F8PolicyAndPalette(unittest.TestCase):
    """F3 registre, F4 politique catégorie/nature, F5 gate non vacuous, F6 dédup perc/hihat, F8 bornes."""

    def test_f4_policy_rules(self):
        # Jamais de gl.sampler en repli synthé natif (sans fichier -> silence)
        from timbre_pleasantness import pick_gl_plugin
        for prof in ("trap_rap", "lofi_chill", "pop_rnb"):
            self.assertNotEqual(pick_gl_plugin("bass", prof, 0.0)["plugin"], "gl.sampler")
        issues_ns = blueprint_musical_safety_issues({
            "key": "C minor", "bpm": 128,
            "tracks": [{"name": "bass", "role": "bass", "source_type": "gridline_native", "plugin": "gl.sampler"}],
        })
        self.assertTrue(any(i["code"] == "native_sampler_without_sample" and i["severity"] == "error" for i in issues_ns))
        # FX interdit sur accords
        pol_fx = sfg.candidate_policy("chord_keys", {"category": "fx", "filepath": "/x/fx/sxj_fx4.wav", "sample_nature": "inharmonic_bell_or_fx"})
        self.assertFalse(pol_fx["allowed"])
        # Kick interdit sur basse
        pol_kick_bass = sfg.candidate_policy("bass_sub", {"category": "kick", "filepath": "/x/kick/kick04.wav", "sample_nature": "mono_oneshot"})
        self.assertFalse(pol_kick_bass["allowed"])
        # Perc melodic_sequence interdit sur kick
        pol_perc_kick = sfg.candidate_policy("kick", {"category": "perc", "filepath": "/x/perc/loop.wav", "sample_nature": "melodic_sequence"})
        self.assertFalse(pol_perc_kick["allowed"])
        # 808 préféré sur basse
        pol_808 = sfg.candidate_policy("bass_sub", {"category": "808", "filepath": "/x/808/sub_c1.wav", "sample_nature": "mono_oneshot"})
        self.assertTrue(pol_808["allowed"])
        self.assertEqual(pol_808["tier"], "preferred")
        # Nom métallique pénalisé sur lead
        pen, hits = sfg.electronic_penalty("lead_melody", "/x/other/steel_stab_d5_clean.wav")
        self.assertLess(pen, 0.0)
        self.assertIn("stab", hits)

    def test_f5_pleasantness_gate_unmeasured_is_fix_not_pass(self):
        # Métriques manquantes sur rôle tonal -> FIX (jamais PASS)
        g = pleasantness_gate({"comfort_score": 0.9, "centroid_hz": 900.0}, "chord_keys")
        self.assertEqual(g["verdict"], "FIX")
        self.assertTrue(g["unmeasured"])
        # Catégorie FX sur accords -> SWAP
        g_fx = pleasantness_gate({
            "r_harsh": 0.02, "roughness": 0.01, "peak_harsh_db": 2.0, "crest_factor_db": 8.0,
            "comfort_score": 0.9, "centroid_hz": 900.0, "category": "fx",
            "sample_nature": "inharmonic_bell_or_fx",
        }, "chord_keys")
        self.assertEqual(g_fx["verdict"], "SWAP")

    def test_f3_f4_f8_propose_palettes_excludes_bad_and_bounds_score(self):
        from palette_builder import propose_palettes
        with tempfile.TemporaryDirectory() as td:
            fx_wav = _write_wav(os.path.join(td, "samples", "fx", "sxj_fx4.wav"), _saturated_808(65.4))
            piano_low = _write_wav(os.path.join(td, "samples", "piano", "piano_c1.wav"), _saturated_808(65.4))
            piano_ok = _write_wav(os.path.join(td, "samples", "piano", "piano_c4.wav"), _saturated_808(261.6))
            kick_wav = _write_wav(os.path.join(td, "samples", "kick", "kick04.wav"), _saturated_808(55.0))
            sub_wav = _write_wav(os.path.join(td, "samples", "808", "sub_c2.wav"), _saturated_808(65.4))
            cat = {"samples": [
                _catalog_entry(fx_wav, "chord_keys", "fx", midi_root=24, sample_nature="inharmonic_bell_or_fx", role_score=0.99),
                _catalog_entry(piano_low, "chord_keys", "piano", midi_root=24, role_score=0.95),
                _catalog_entry(piano_ok, "chord_keys", "piano", midi_root=60, role_score=0.80),
                _catalog_entry(kick_wav, "bass_sub", "kick", midi_root=36, role_score=0.99),
                _catalog_entry(sub_wav, "bass_sub", "808", midi_root=36, role_score=0.82),
            ]}
            with open(os.path.join(td, "samples_catalog.json"), "w", encoding="utf-8") as fh:
                json.dump(cat, fh)
            res = propose_palettes(
                mix_profile="trap_rap",
                emotion_vector={"valence": 0.0, "energy": 0.7},
                target_key="C minor",
                roles=["bass_sub", "chord_keys"],
                project_samples_dir=td,
                note_ranges={"chord_keys": (58, 64), "bass_sub": (34, 41)},
                include_pool=True,
            )
            trace = res["selection_trace"]
            self.assertGreaterEqual(trace["policy_excluded"]["chord_keys"], 1)  # fx exclu
            self.assertGreaterEqual(trace["policy_excluded"]["bass_sub"], 1)    # kick exclu
            self.assertGreaterEqual(trace["range_excluded"]["chord_keys"], 1)   # piano_c1 exclu
            self.assertIn("gate_unmeasured", trace)
            self.assertIn("candidate_pool", res)
            for p in res["palettes"]:
                self.assertLessEqual(p["score"], 1.0)
                self.assertIn("quality_score", p)
                self.assertIn("sample_ratio", p)
                by_role = {s["role"]: s for s in p["slots"]}
                if by_role["chord_keys"]["source_type"] != "gridline_native":
                    self.assertIn("piano_c4", by_role["chord_keys"]["filepath"])
                if by_role["bass_sub"]["source_type"] != "gridline_native":
                    self.assertIn("sub_c2", by_role["bass_sub"]["filepath"])

    def test_f6_enforce_guard_dedups_perc_and_hihat_and_replaces_fx(self):
        with tempfile.TemporaryDirectory() as td:
            hat_wav = _write_wav(os.path.join(td, "samples", "hat", "hat1.wav"), _saturated_808(500.0, 0.2))
            perc_wav = _write_wav(os.path.join(td, "samples", "perc", "perc1.wav"), _saturated_808(400.0, 0.2))
            fx_wav = _write_wav(os.path.join(td, "samples", "fx", "fx1.wav"), _saturated_808(65.4, 0.4))
            cat_path = os.path.join(td, "samples_catalog.json")
            with open(cat_path, "w", encoding="utf-8") as fh:
                json.dump({"samples": [
                    _catalog_entry(hat_wav, "hihat_perc", "hat", 42, playable=False, sample_nature="percussion"),
                    _catalog_entry(perc_wav, "hihat_perc", "perc", 39, playable=False, sample_nature="percussion"),
                    _catalog_entry(fx_wav, "chord_keys", "fx", 24, sample_nature="inharmonic_bell_or_fx"),
                ]}, fh)
            bp = {
                "mix_profile": "trap_rap",
                "emotion_vector": {"valence": 0.0},
                "tracks": [
                    {"name": "hihat", "role": "drums", "source_type": "sample", "plugin": "gl.sampler", "sample_path": hat_wav, "root_note": 42},
                    {"name": "perc", "role": "drums", "source_type": "sample", "plugin": "gl.sampler", "sample_path": hat_wav, "root_note": 39},
                    {"name": "chords", "role": "chords", "source_type": "sample", "plugin": "gl.sampler", "sample_path": fx_wav, "root_note": 24},
                ],
            }
            pool = {"hihat_perc": [
                {"source_type": "sample", "filepath": hat_wav, "category": "hat", "midi_root": 42, "role_score": 0.9, "comfort_score": 0.9},
                {"source_type": "sample", "filepath": perc_wav, "category": "perc", "midi_root": 39, "role_score": 0.8, "comfort_score": 0.9},
            ]}
            rep = sfg.enforce_blueprint_sample_fit(
                bp, notes_by_track={"chords": [50, 60, 72]}, catalog_path=cat_path, pool_by_role=pool, write_diag_dir=td
            )
            self.assertEqual(rep["status"], "FIXED")
            self.assertGreaterEqual(rep["n_deduped"], 1)
            by_name = {t["name"]: t for t in bp["tracks"]}
            self.assertNotEqual(by_name["hihat"].get("sample_path"), by_name["perc"].get("sample_path"))
            self.assertIn("perc1.wav", by_name["perc"]["sample_path"])
            # chords FX root 24 -> remplacé par synthé gl.* calibré
            self.assertEqual(by_name["chords"]["source_type"], "gridline_native")
            self.assertNotIn("sample_path", by_name["chords"])
            self.assertTrue(os.path.isfile(os.path.join(td, "renders", "diagnostics", "sound_guard.json")))
            self.assertLessEqual(len(rep["summary_line"]), 160)

    def test_f8_fallback_gate_fails_on_multiple_s_max_violations(self):
        from audit_project_sources import sample_fallback_gate
        bp = {"tracks": [
            {"name": "chords", "role": "chords", "source_type": "sample", "sample_path": "/a.wav", "root_note": 24},
            {"name": "bass", "role": "bass", "source_type": "sample", "sample_path": "/b.wav", "root_note": 31},
        ]}
        pal = {"slots": [
            {"role": "chord_keys", "source_type": "sample", "filepath": "/a.wav"},
            {"role": "bass_sub", "source_type": "sample", "filepath": "/b.wav"},
        ]}
        res = sample_fallback_gate(bp, pal, s_max_violations=2)
        self.assertEqual(res["gate"], "FAIL")
        self.assertFalse(res["passed"])


class TestF9Deduplication(unittest.TestCase):
    """F9 : index_project_samples dédoublonne par hash de contenu et garde le dossier spécifique."""

    def test_f9_dedup_keeps_kick_over_other(self):
        from index_project_samples import index_project_samples
        with tempfile.TemporaryDirectory() as td:
            sig = _saturated_808(55.0, dur=0.35)
            _write_wav(os.path.join(td, "Samples", "kick", "kick04.wav"), sig)
            _write_wav(os.path.join(td, "Samples", "other", "kick04.wav"), sig)
            _write_wav(os.path.join(td, "Samples", "kick", "kick04-2.wav"), sig)
            cat = index_project_samples(td, with_clap=False, write_catalog=True)
            self.assertEqual(cat["sample_count"], 1)
            self.assertEqual(cat["deduplicated_count"], 2)
            kept = cat["samples"][0]
            self.assertEqual(kept["category"], "kick")
            self.assertEqual(kept["filename"], "kick04.wav")
            # F5 : métriques complètes présentes dans le catalogue
            for k in ("r_harsh", "roughness", "peak_harsh_db", "crest_factor_db"):
                self.assertIn(k, kept)
                self.assertIsNotNone(kept[k])


@unittest.skipUnless(HAVE_STRUDEL, "node + @strudel requis")
class TestF10AndF11ProdServer(unittest.TestCase):
    """F10 : purge master.wav périmé ; F11 : studio_refine revalide les sons après patch."""

    def test_f10_stale_master_purged_when_not_rendered(self):
        import prod_server
        with tempfile.TemporaryDirectory() as td:
            renders = os.path.join(td, "renders")
            os.makedirs(renders)
            fake_master = os.path.join(renders, "master.wav")
            fake_origin = os.path.join(renders, "master.wav.origin.json")
            with open(fake_master, "wb") as fh:
                fh.write(b"RIFFxxxxWAVE")
            with open(fake_origin, "w", encoding="utf-8") as fh:
                json.dump({"source": "kick/fake.wav"}, fh)
            with open(os.path.join(ROOT, "assets", "composition_trap_example.strudel"), encoding="utf-8") as fh:
                src = fh.read()
            rep = prod_server.compose_project(td, src, palette_id="auto", auto_master=False)
            self.assertTrue(rep.get("ok"))
            self.assertFalse(os.path.exists(fake_master), "ancien master.wav doit être purgé")
            self.assertFalse(os.path.exists(fake_origin))
            with open(os.path.join(td, "handoff.txt"), encoding="utf-8") as fh:
                handoff = fh.read()
            self.assertIn("sound_guard=", handoff)
            self.assertIn("safety=", handoff)

    def test_f11_refine_revalidates_after_octave_shift(self):
        import prod_server
        with tempfile.TemporaryDirectory() as td:
            lead_wav = _write_wav(os.path.join(td, "Samples", "piano", "lead_c4.wav"), _saturated_808(261.63, 0.5))
            cat = {"samples": [
                _catalog_entry(lead_wav, "lead_melody", "piano", midi_root=60, role_score=0.9),
            ]}
            with open(os.path.join(td, "samples_catalog.json"), "w", encoding="utf-8") as fh:
                json.dump(cat, fh)
            with open(os.path.join(ROOT, "assets", "composition_trap_example.strudel"), encoding="utf-8") as fh:
                src = fh.read()
            rep = prod_server.compose_project(td, src, palette_id="auto", auto_master=False)
            self.assertTrue(rep.get("ok"))
            # Injecter un sample lead à root 60 puis décaler le lead de +2 octaves via semantic_patch
            bp_path = os.path.join(td, "song_blueprint.json")
            with open(bp_path, encoding="utf-8") as fh:
                bp = json.load(fh)
            for tr in bp["tracks"]:
                if tr["name"] == "lead":
                    tr["source_type"] = "sample"
                    tr["plugin"] = "gl.sampler"
                    tr["sample_path"] = lead_wav
                    tr["root_note"] = 60
            with open(bp_path, "w", encoding="utf-8") as fh:
                json.dump(bp, fh)
            out = prod_server.refine_project(td, semantic_patch={"lead": {"oct": 2}}, export_mp3=False)
            self.assertTrue(out.get("ok"))
            self.assertEqual(out.get("sound_guard", {}).get("status"), "FIXED")
            self.assertEqual(out.get("musical_safety", {}).get("errors"), 0)
            with open(bp_path, encoding="utf-8") as fh:
                bp_after = json.load(fh)
            lead_after = next(t for t in bp_after["tracks"] if t["name"] == "lead")
            self.assertEqual(lead_after["source_type"], "gridline_native")


class TestCIRealBankPalettes(unittest.TestCase):
    """§5 Test CI « palette sur banc réel » : aucun slot tonal avec S_max > cap, ni FX/kick interdit."""

    @unittest.skipUnless(os.path.isfile(os.path.join(ROOT, "samples_catalog.json")), "samples_catalog.json absent")
    def test_real_bank_profiles_have_zero_policy_or_shift_violations(self):
        from palette_builder import propose_palettes
        ranges = {"bass_sub": (35, 43), "chord_keys": (50, 72), "pad_atmosphere": (52, 72), "lead_melody": (59, 71)}
        for prof in ("trap_rap", "lofi_chill", "classical_ambient"):
            res = propose_palettes(
                mix_profile=prof,
                emotion_vector={"valence": 0.0, "tension": 0.4, "energy": 0.6, "depth": 0.5},
                target_key="C minor",
                project_samples_dir=os.path.join(ROOT, "samples"),
                user_samples_locked=False,
                note_ranges=ranges,
                bpm=128.0,
            )
            self.assertTrue(res["palettes"], f"aucune palette pour {prof}")
            best = res["palettes"][0]
            self.assertLessEqual(best["score"], 1.0)
            for s in best["slots"]:
                if s["source_type"] not in ("sample", "project_sample"):
                    continue
                pol = sfg.candidate_policy(s["role"], s)
                self.assertTrue(pol["allowed"], f"{prof}/{s['role']} sample interdit: {s.get('filepath')} ({pol})")
                if s["role"] in ranges:
                    rf = sfg.register_fit(s.get("midi_root"), ranges[s["role"]], s, role=s["role"])
                    self.assertTrue(rf["ok"], f"{prof}/{s['role']} S_max dépassé: {rf} ({s.get('filepath')})")


if __name__ == "__main__":
    unittest.main()
