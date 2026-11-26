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


class TestPhase2MusicalityAndTimbreFixes(unittest.TestCase):
    """Non-régression Phase 2 (audit projects/trap_lent_planant_energique) :
    - 68 mesures évaluées sans troncature à 16 mesures ;
    - bassline(mode='root') ancrée sur la fondamentale réelle de chaque accord ;
    - zéro cluster b2 interne (ex. G4+Ab4 dans Abmaj7) dans optimize_voicings_lil ;
    - $pad / $chords synchronisés sur la grille harmonique (zéro polytonalité) ;
    - fix_lead_b2_clashes ne déplace jamais une note d'accord ;
    - $arp classé `counter` (jamais `drums`) ;
    - cris vocaux (`Vox`) exclus des percussions ;
    - gl.keys / gl.warmsynth / gl.pluck / gl.stereo paramètres propres ;
    - NOISY_FLOOR ne rejette pas un one-shot hi-hat/snare décroissant propre."""

    def test_voicings_no_internal_b2_cluster_on_maj7(self):
        import strudel_to_score_ir as st
        # Abmaj7 = [0(C), 3(Eb), 7(G), 8(Ab)] : jamais G4(67)+Ab4(68) à 1 demi-ton !
        pcs_list = [[0, 3, 7, 10], [0, 3, 7, 8], [0, 3, 5, 8], [2, 5, 7, 11]]
        voicings = st.optimize_voicings_lil(pcs_list)
        for v in voicings:
            sv = sorted(v)
            for a, b in zip(sv, sv[1:]):
                self.assertGreater(b - a, 1, f"cluster b2 interne interdit dans voicing {sv}")

    def test_pad_split_at_chord_boundaries_no_polytonality(self):
        import strudel_to_score_ir as st
        # 8 mesures : Cm7 (0..8), Abmaj7 (8..16), Fm7 (16..24), G7 (24..32)
        prog = ["Cm7", "Cm7", "Abmaj7", "Abmaj7", "Fm7", "Fm7", "G7", "G7"]
        grid = st.build_grid(prog, "C", "minor", 4, 8)
        tracks = {"pad": [{"start": 0.0, "length": 16.0, "pitch": -1, "velocity": 80, "chord_symbol": "Cm7"},
                          {"start": 16.0, "length": 16.0, "pitch": -1, "velocity": 80, "chord_symbol": "Fm7"}]}
        st.expand_chord_symbols(tracks, grid)
        starts = sorted({ev["start"] for ev in tracks["pad"]})
        self.assertEqual(starts, [0.0, 8.0, 16.0, 24.0], "pad doit changer d'accord sur chaque changement de grille")

    def test_fix_lead_b2_preserves_chord_tones_on_maj7(self):
        import strudel_to_score_ir as st
        from score_ir import NoteEventIR, ScoreIR, TrackIR
        grid = st.build_grid(["Abmaj7"], "C", "minor", 4, 1)
        score = ScoreIR(bpm=92.0, time_signature={"numerator": 4, "denominator": 4},
                        key="C", mode="minor", valence=0.0, harmonic_grid=grid)
        # Ab4 (68) et G4 (67) sont TOUS DEUX dans Abmaj7 -> ne doivent PAS être déplacés !
        score.tracks["lead"] = TrackIR(role="lead", monophonic=True, notes=[
            NoteEventIR(grid_beat=0.0, duration_beats=1.0, pitch=68, velocity=90, chord_idx=0),
            NoteEventIR(grid_beat=1.0, duration_beats=1.0, pitch=67, velocity=90, chord_idx=0),
        ])
        n = st.fix_lead_b2_clashes(score)
        self.assertEqual(n, 0)
        self.assertEqual([x.pitch for x in score.tracks["lead"].notes], [68, 67])

    def test_arp_classified_as_counter_not_drums(self):
        from score_ir import classify_role
        self.assertEqual(classify_role("arp"), "counter")
        self.assertEqual(classify_role("arpeggio"), "counter")
        self.assertEqual(classify_role("piano"), "chords")
        self.assertEqual(sfg.normalize_slot_role("arp"), "lead_melody")

    def test_vocal_shout_excluded_from_perc(self):
        pol = sfg.candidate_policy("hihat_perc", {"category": "perc", "filepath": "/samples/perc/MZ Perc [Urban Vox].wav"})
        self.assertFalse(pol["allowed"], pol)

    def test_noisy_floor_passes_clean_decaying_percussive_hat(self):
        import sample_quality_v2 as q2
        sr = 44100
        rng = np.random.default_rng(7)
        t = np.arange(int(0.35 * sr)) / sr
        # Hi-hat synthétique propre : bruit filtré + résonance métallique à 8 kHz, décroissance exponentielle
        env = np.exp(-t / 0.045)
        hat = env * (0.45 * rng.standard_normal(len(t)) + 0.35 * np.sin(2 * np.pi * 8200.0 * t))
        hat = hat / max(float(np.max(np.abs(hat))), 1e-9) * 0.65
        res = q2.quality_gate_v2(hat, sr)
        self.assertEqual(res["status"], "PASS", f"hi-hat propre ne doit PAS être rejeté NOISY_FLOOR: {res}")

    @unittest.skipUnless(HAVE_STRUDEL, "node + @strudel requis")
    def test_full_68_bar_form_and_bass_root_lock(self):
        import strudel_to_score_ir as st
        src = (
            '// @meta 92 C:minor trap_rap V=0.0 form=I4-V12-P4-C8-V12-P4-C8-B4-C8-O4 seed=4209 ts=4/4\n'
            'kit("trap_half", {var: 2, hats: "rolls"})\n'
            '$chords: grid("Cm7 Abmaj7 Fm7 G7").slow(2).gain(0.68).muteIn("intro,outro")\n'
            '$pad: grid("Cm7 Abmaj7 Fm7 G7").slow(4).gain(0.55).in("intro,chorus,bridge,outro")\n'
            '$bass: bassline("<1 ~ 1 ~>", {mode: "root", glide: 40}).slow(4).in("verse,pre_chorus,chorus,outro")\n'
            '$lead: deg("0 3 2 1 ~ 1 2 1 0 ~ 2 3 2 1 0 ~", 4).slow(2).gain(0.62).in("verse,chorus")\n'
            '$arp: deg("0 3 6 7 6 3 0 ~", 4).slow(2).gain(0.45).in("pre_chorus,chorus,bridge")\n'
        )
        with tempfile.TemporaryDirectory() as td:
            sp = os.path.join(td, "composition.strudel")
            with open(sp, "w", encoding="utf-8") as fh:
                fh.write(src)
            rep = st.strudel_to_score_ir(td, run_eval=True, emit_plan=True)
            self.assertTrue(rep.get("ok"), rep)
            total_bars = sum(int(s.get("bars", 0)) for s in (rep.get("sections") or []))
            self.assertEqual(total_bars, 68, "doit évaluer les 68 mesures du @meta form=, pas tronquer à 16 !")
            with open(os.path.join(td, "composition.strudel.haps.json"), encoding="utf-8") as fh:
                self.assertEqual(json.load(fh).get("bars"), 68)
            with open(os.path.join(td, "arrangement_plan.json"), encoding="utf-8") as fh:
                plan = json.load(fh)
            for sec in plan["sections"]:
                self.assertTrue(sec["active_roles"], f"section {sec['name']} (bar {sec['start_bar']}) ne doit pas être vide !")
            with open(os.path.join(td, "score_ir.json"), encoding="utf-8") as fh:
                ir = json.load(fh)
            grid = ir["harmonic_grid"]
            # Chaque note de basse en mode=root doit jouer la fondamentale exacte de l'accord courant
            for n in ir["tracks"]["bass"]["notes"]:
                seg = grid[n["chord_idx"]]
                self.assertEqual(n["pitch"] % 12, seg["root_pc"], f"bass {n} != root {seg}")
            # $arp doit avoir le rôle counter et être présent dans pre_chorus/chorus/bridge
            self.assertEqual(ir["tracks"]["arp"]["role"], "counter")
            self.assertTrue(any("arp" in s["active_roles"] for s in plan["sections"]))

    def test_phase3_distinct_factory_presets_per_role(self):
        from timbre_pleasantness import pick_gl_plugin
        ch = pick_gl_plugin("chords", "trap_rap", 0.0)
        pd = pick_gl_plugin("pad", "trap_rap", 0.0)
        ld = pick_gl_plugin("lead", "trap_rap", 0.0)
        ap = pick_gl_plugin("arp", "trap_rap", 0.0)
        self.assertTrue(ch.get("preset"), ch)
        self.assertTrue(pd.get("preset"), pd)
        self.assertTrue(ld.get("preset"), ld)
        self.assertTrue(ap.get("preset"), ap)
        # Pad != Chords et Arp != Lead (zéro doublon timbral)
        self.assertNotEqual((ch["plugin"], ch.get("preset")), (pd["plugin"], pd.get("preset")))
        self.assertNotEqual((ld["plugin"], ld.get("preset")), (ap["plugin"], ap.get("preset")))

    def test_phase3_viterbi_voice_leading_smooth_and_cluster_free(self):
        import strudel_to_score_ir as st
        pcs = [[0, 3, 7, 10], [0, 3, 7, 8], [0, 3, 5, 8], [2, 5, 7, 11]] * 2
        vs = st.optimize_voicings_lil(pcs)
        dists = [sum(abs(a - b) for a, b in zip(vs[i], vs[i + 1])) / len(vs[i]) for i in range(len(vs) - 1)]
        self.assertLess(sum(dists) / len(dists), 1.5, f"voice leading trop brusque: {dists} -> {vs}")
        for v in vs:
            for a, b in zip(v, v[1:]):
                self.assertGreater(b - a, 1, f"cluster b2 dans {v}")

    def test_phase3_octave_up_808_resampling(self):
        with tempfile.TemporaryDirectory() as td:
            wav_c1 = _write_wav(os.path.join(td, "src_808_c1.wav"), _saturated_808(32.703, dur=1.6, sr=44100), sr=44100)
            rf = sfg.register_fit(24, [32, 36, 41, 43], {"category": "808", "filepath": wav_c1, "duration_s": 1.6}, role="bass_sub")
            self.assertTrue(rf["ok"], rf)
            self.assertTrue(rf.get("octave_up_needed"), rf)
            self.assertEqual(rf.get("effective_root"), 36)
            derived = sfg.prepare_octave_up_808(wav_c1, td, 24)
            self.assertIsNotNone(derived)
            out_path, new_root = derived
            self.assertEqual(new_root, 36)
            self.assertTrue(os.path.isfile(out_path))
            self.assertIn("derived_samples", out_path)

    def test_phase4_resolve_project_dir_locks_to_projects_folder(self):
        sys.path.insert(0, os.path.join(ROOT, "mcp-servers", "ai-studio-prod"))
        import prod_server
        p_bare = prod_server._resolve_project_dir("rap_2min")
        self.assertEqual(p_bare, os.path.abspath(os.path.join(prod_server.ROOT, "projects", "rap_2min")))
        p_rel = prod_server._resolve_project_dir("projects/mon_son")
        self.assertEqual(p_rel, os.path.abspath(os.path.join(prod_server.ROOT, "projects", "mon_son")))
        p_ext = prod_server._resolve_project_dir("/nonexistent_other_studio/projects/rap_2min")
        self.assertEqual(p_ext, os.path.abspath(os.path.join(prod_server.ROOT, "projects", "rap_2min")))
        p_tmp = prod_server._resolve_project_dir("/tmp/pytest_isolated_dir")
        self.assertEqual(p_tmp, "/tmp/pytest_isolated_dir")

    def test_phase4_octave_folded_mad_recovers_decaying_pluck(self):
        from audio_metrics import analyze_single_file
        sr = 44100
        t = np.arange(int(1.6 * sr)) / sr
        # Note unique B3 (246.94 Hz) avec queue décroissante et léger partiel d'octave 2*f0
        env = np.exp(-t / 0.35)
        sig = env * (0.75 * np.sin(2 * np.pi * 246.94 * t) + 0.35 * np.sin(2 * np.pi * 493.88 * t) + 0.12 * np.sin(2 * np.pi * 740.82 * t))
        sig = (sig / max(float(np.max(np.abs(sig))), 1e-9) * 0.8).astype(np.float32)
        with tempfile.TemporaryDirectory() as td:
            p = _write_wav(os.path.join(td, "guitar_pluck_b3.wav"), sig, sr=sr)
            entry, _ = analyze_single_file(p, with_clap=False)
            self.assertTrue(entry["dsp"]["playable_chromatically"], entry["dsp"])
            self.assertEqual(entry["dsp"]["sample_nature"], "mono_oneshot", entry["dsp"])

    def test_phase4_prepare_octave_shifted_sample_down_and_up(self):
        sr = 44100
        t = np.arange(int(1.2 * sr)) / sr
        pad_c5 = (0.7 * np.sin(2 * np.pi * 523.25 * t) * np.exp(-t / 0.9)).astype(np.float32)
        with tempfile.TemporaryDirectory() as td:
            wav_c5 = _write_wav(os.path.join(td, "glass_pad_c5.wav"), pad_c5, sr=sr)
            rf = sfg.register_fit(72, [53, 56, 60, 63, 67, 70], {"category": "synth", "filepath": wav_c5}, role="pad_atmosphere")
            self.assertTrue(rf["ok"], rf)
            self.assertEqual(rf.get("octave_shift_st"), -12, rf)
            self.assertEqual(rf.get("effective_root"), 60, rf)
            derived = sfg.prepare_octave_shifted_sample(wav_c5, td, 72, -1)
            self.assertIsNotNone(derived)
            out_path, new_root = derived
            self.assertEqual(new_root, 60)
            self.assertTrue(os.path.isfile(out_path))
            self.assertIn("derived_samples", out_path)

    def test_phase4_sampler_role_envelopes_avoid_voice_stealing(self):
        from song_builder import _default_channels_for_plan
        with tempfile.TemporaryDirectory() as td:
            bp = {
                "title": "t", "bpm": 92, "key": "C", "mix_profile": "trap_rap",
                "tracks": [
                    {"name": "kick", "role": "drums", "source_type": "sample", "sample_path": "/samples/kick/k.wav", "root_note": 36},
                    {"name": "bass", "role": "bass", "source_type": "sample", "sample_path": "/samples/808/b.wav", "root_note": 36},
                    {"name": "chords", "role": "chords", "source_type": "sample", "sample_path": "/samples/synth/c.wav", "root_note": 60},
                    {"name": "pad", "role": "chords", "source_type": "sample", "sample_path": "/samples/synth/p.wav", "root_note": 60},
                    {"name": "lead", "role": "lead", "source_type": "sample", "sample_path": "/samples/guitar/l.wav", "root_note": 60},
                ],
            }
            with open(os.path.join(td, "song_blueprint.json"), "w", encoding="utf-8") as fh:
                json.dump(bp, fh)
            chs = {c.track_key: c for c in _default_channels_for_plan({}, ["kick", "bass", "chords", "pad", "lead"], td, 0.0)}
            self.assertEqual(chs["kick"].one_shot, 1)
            self.assertEqual(chs["bass"].voices, 1)
            self.assertEqual(chs["chords"].voices, 8)
            self.assertEqual(chs["chords"].preset, "")
            self.assertEqual(chs["pad"].voices, 8)
            self.assertEqual(chs["pad"].preset, "")
            self.assertGreaterEqual(chs["pad"].attack, 0.05)
            self.assertGreaterEqual(chs["pad"].release, 0.35)
            self.assertEqual(chs["lead"].voices, 6)
            self.assertEqual(chs["lead"].preset, "")

    def test_phase4_bottom_up_analyzer_4_levels(self):
        import bottom_up_analyzer as bu
        with tempfile.TemporaryDirectory() as td:
            wav = _write_wav(os.path.join(td, "piano_c4.wav"), _saturated_808(261.63, dur=1.2, sr=44100), sr=44100)
            bp = {
                "title": "bu_test", "bpm": 120, "key": "C", "mix_profile": "trap_rap",
                "emotion_vector": {"valence": 0.0, "energy": 0.7},
                "tracks": [
                    {"name": "chords", "role": "chords", "source_type": "sample", "plugin": "gl.sampler",
                     "sample_path": wav, "root_note": 60, "mixer_track": 1},
                ],
                "structure": [{"section": "verse", "start_bar": 1, "bars": 4, "energy": 0.7}],
            }
            score_ir = {
                "meta": {"bpm": 120.0, "time_signature": {"numerator": 4, "denominator": 4}},
                "sections": [{"name": "verse", "bars": 4}],
                "harmonic_grid": [{"bar": 1, "symbol": "Cm7", "root_pc": 0, "pcs": [0, 3, 7, 10]}],
                "tracks": {
                    "chords": {
                        "role": "chords",
                        "notes": [
                            {"grid_beat": 0.0, "duration_beats": 4.0, "pitch": 60, "chord_idx": 0},
                            {"grid_beat": 0.0, "duration_beats": 4.0, "pitch": 63, "chord_idx": 0},
                            {"grid_beat": 0.0, "duration_beats": 4.0, "pitch": 67, "chord_idx": 0},
                        ],
                    },
                    "bass": {
                        "role": "bass",
                        "notes": [
                            {"grid_beat": 0.0, "duration_beats": 2.5, "pitch": 24, "chord_idx": 0},  # trop grave (<31) + overlap
                            {"grid_beat": 2.0, "duration_beats": 2.0, "pitch": 36, "chord_idx": 0},
                        ],
                    },
                    "lead": {
                        "role": "lead",
                        "notes": [
                            {"grid_beat": 0.0, "duration_beats": 1.0, "pitch": 61, "chord_idx": 0},  # frottement b2 vs C(0)
                            {"grid_beat": 2.0, "duration_beats": 1.0, "pitch": 79, "chord_idx": 0},  # apex > 74
                        ],
                    },
                },
            }
            with open(os.path.join(td, "song_blueprint.json"), "w", encoding="utf-8") as fh:
                json.dump(bp, fh)
            with open(os.path.join(td, "score_ir.json"), "w", encoding="utf-8") as fh:
                json.dump(score_ir, fh)
            rep = bu.run_bottom_up_loop(td, auto_fix=True, max_iterations=2, write_report=True)
            self.assertTrue(rep["passed"], rep)
            self.assertEqual(rep["levels_passed"], "4/4", rep)
            self.assertGreaterEqual(rep["total_fixes"], 3, rep)
            self.assertTrue(os.path.isfile(os.path.join(td, "renders", "diagnostics", "bottom_up_report.json")))


if __name__ == "__main__":
    unittest.main()
