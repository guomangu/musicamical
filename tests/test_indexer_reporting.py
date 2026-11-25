#!/usr/bin/env python3
"""v3.3 hotfix — phase d'indexation des samples (install.sh étape 8/8) :
  (a) `_iter_media_files` remonte désormais les rejets AVEC raison (dotfile, .wav.zpa,
      bad_magic_bytes, too_small, symlink_duplicate) au lieu de les laisser invisibles ;
  (b) `index_directory` rapporte `scanned` / `rejected` / `rejected_reasons` en plus des
      compteurs historiques (total/indexed/skipped) — `skipped` = déjà en base (incrémental) ;
  (c) `verify_install.validate_active_blueprint` ne plante plus quand
      `projects/active/song_blueprint.json` est absent (installation fraîche) et valide
      strictement quand il est présent.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
import warnings

ROOT = os.environ.get("AI_STUDIO_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in (os.path.join(ROOT, "scripts"), os.path.join(ROOT, "mcp-servers", "audio-analyzer"), os.path.join(ROOT, "mcp-servers", "audio-index")):
    if _d not in sys.path:
        sys.path.insert(0, _d)

import numpy as np


def _write_wav(path: str, freq: float = 440.0, sr: int = 22050, dur: float = 0.1) -> None:
    import soundfile as sf
    t = np.arange(int(dur * sr)) / sr
    sf.write(path, 0.4 * np.sin(2 * np.pi * freq * t), sr)


def _build_samples_tree(root: str) -> None:
    os.makedirs(os.path.join(root, "sfz"), exist_ok=True)
    _write_wav(os.path.join(root, "kick.wav"), 55.0)
    _write_wav(os.path.join(root, "sfz", "inner.wav"), 220.0)          # répertoire sfz exclu
    with open(os.path.join(root, "loop.mid"), "wb") as fh:               # MIDI accepté tel quel
        fh.write(b"MThd\x00\x00\x00\x06\x00\x01\x01\xe0MTrk\x00\x00\x00\x04\x00\xff\x2f\x00")
    with open(os.path.join(root, "fake.wav"), "w", encoding="utf-8") as fh:  # > 1 Ko mais pas de magic audio
        fh.write("not audio at all " * 200)
    with open(os.path.join(root, "tiny.wav"), "wb") as fh:               # < 1 Ko
        fh.write(b"RIFF")
    with open(os.path.join(root, "pack.wav.zpa"), "w", encoding="utf-8") as fh:  # suffixe composite
        fh.write("PK" * 600)
    with open(os.path.join(root, ".DS_Store"), "wb") as fh:              # parasite OS (dotfile)
        fh.write(b"\x00" * 64)
    with open(os.path.join(root, "readme.txt"), "w", encoding="utf-8") as fh:  # non média : ignoré
        fh.write("hello")


class TestIterMediaFilesReporting(unittest.TestCase):
    def test_accepted_and_rejected_with_reasons(self):
        from indexer import _iter_media_files
        with tempfile.TemporaryDirectory() as tmp:
            _build_samples_tree(tmp)
            files, rejected = _iter_media_files(tmp)
            names = sorted(os.path.basename(f) for f, _rel in files)
            self.assertEqual(names, ["kick.wav", "loop.mid"])  # sfz/ exclu, readme.txt ignoré
            rnames = {os.path.basename(r["path"]): r["reason"] for r in rejected}
            self.assertIn("fake.wav", rnames)
            self.assertEqual(rnames["fake.wav"], "bad_magic_bytes")
            self.assertTrue(rnames["tiny.wav"].startswith("too_small"))
            self.assertEqual(rnames["pack.wav.zpa"], "non_pcm_suffix:.zpa")
            self.assertEqual(rnames[".DS_Store"], "dotfile")
            self.assertNotIn("inner.wav", rnames)  # sfz/ pruned, pas compté


class TestIndexDirectoryReport(unittest.TestCase):
    def test_report_keys_and_incremental_skip(self):
        from indexer import index_directory
        with tempfile.TemporaryDirectory() as tmp:
            samples = os.path.join(tmp, "samples")
            os.makedirs(samples)
            _build_samples_tree(samples)
            db = os.path.join(tmp, "samples.db")
            r1 = index_directory(samples, db, force=False, with_clap=False, with_dsp=False)
            for k in ("total", "indexed", "skipped", "errors", "scanned", "rejected", "rejected_reasons"):
                self.assertIn(k, r1)
            self.assertEqual(r1["indexed"], 2)          # kick.wav + loop.mid
            self.assertEqual(r1["skipped"], 0)
            self.assertEqual(r1["rejected"], 4)          # fake/tiny/.zpa/.DS_Store
            self.assertEqual(r1["scanned"], 6)           # 2 acceptés + 4 rejetés
            self.assertIn("bad_magic_bytes", r1["rejected_reasons"])
            # 2e passage incrémental : tout est déjà en base -> skipped, indexed=0
            r2 = index_directory(samples, db, force=False, with_clap=False, with_dsp=False)
            self.assertEqual(r2["indexed"], 0)
            self.assertEqual(r2["skipped"], 2)
            self.assertEqual(r2["errors"], 0)


class TestVerifyInstallBlueprintGuard(unittest.TestCase):
    def test_missing_active_blueprint_is_a_clean_skip(self):
        from verify_install import validate_active_blueprint
        with tempfile.TemporaryDirectory() as tmp:
            ok, msg = validate_active_blueprint(tmp)  # pas de projects/active -> pas d'exception
            self.assertTrue(ok)
            self.assertIn("SKIP", msg)

    def test_invalid_blueprint_fails_loudly(self):
        import jsonschema
        from verify_install import validate_active_blueprint
        schema_src = os.path.join(ROOT, "schemas", "song_blueprint.schema.json")
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "projects", "active"))
            bp = os.path.join(tmp, "projects", "active", "song_blueprint.json")
            with open(bp, "w", encoding="utf-8") as fh:
                json.dump({"title": "x"}, fh)  # champs required manquants
            with self.assertRaises(jsonschema.ValidationError):
                validate_active_blueprint(tmp, schema_path=schema_src)

    def test_valid_blueprint_passes(self):
        from verify_install import validate_active_blueprint
        schema_src = os.path.join(ROOT, "schemas", "song_blueprint.schema.json")
        src_bp = os.path.join(ROOT, "projects", "validation_fm_dark", "song_blueprint.json")
        if not os.path.isfile(src_bp):
            self.skipTest("fixture projet absent")
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "projects", "active"))
            with open(src_bp, encoding="utf-8") as fh:
                bp = json.load(fh)
            dst = os.path.join(tmp, "projects", "active", "song_blueprint.json")
            with open(dst, "w", encoding="utf-8") as fh:
                json.dump(bp, fh)
            ok, msg = validate_active_blueprint(tmp, schema_path=schema_src)
            self.assertTrue(ok)
            self.assertIn("OK", msg)


class TestSummarizeIndexReport(unittest.TestCase):
    """v4.0 hotfix — `scripts/summarize_index_report.py` (appelé par install.sh étape 8/8) :
    l'ancien heredoc `python - <<PYEOF` prenait le stdin du pipe, le rapport n'arrivait jamais
    (json.loads("") -> JSONDecodeError char 0, ast.literal_eval("") -> SyntaxError) et
    `set -euo pipefail` avortait install.sh. Le résumé doit lire VRAIMENT stdin et ne jamais
    planter, quelle que soit l'entrée."""

    def _script(self):
        from summarize_index_report import parse_report, summarize
        return parse_report, summarize

    def test_preset_dict_repr(self):
        _, summarize = self._script()
        raw = "{'total_presets': 211, 'indexed': 0, 'skipped': 211, 'errors': 0, 'clap_embedded': 0}"
        line = summarize("presets", raw)
        self.assertIn("211 au total", line)
        self.assertIn("211 déjà à jour", line)

    def test_samples_json_with_rejected_reasons(self):
        _, summarize = self._script()
        raw = json.dumps({"scanned": 6, "total": 2, "indexed": 2, "skipped": 0, "rejected": 4,
                          "rejected_reasons": {"bad_magic_bytes": 1}, "errors": 1,
                          "dsp_total": 2, "clap_embedded": 0, "cards_total": 2})
        line = summarize("samples", raw)
        self.assertIn("6 scannés", line)
        self.assertIn("4 rejetés (bad_magic_bytes: 1)", line)
        self.assertIn("1 erreurs", line)

    def test_empty_input_never_crashes(self):
        parse_report, summarize = self._script()
        self.assertIsNone(parse_report(""))
        self.assertIn("illisible", summarize("presets", ""))  # cas exact du crash install.sh

    def test_multiline_with_log_prefix(self):
        parse_report, summarize = self._script()
        raw = "[   INFO   ] MusicExtractorSVM: no classifier models\n{'total_presets': 211, 'indexed': 3, 'skipped': 208, 'errors': 0, 'clap_embedded': 3}\n"
        r = parse_report(raw)
        self.assertIsNotNone(r)
        self.assertEqual(r["indexed"], 3)
        self.assertIn("3 nouveaux", summarize("presets", raw))

    def test_garbage_never_crashes(self):
        _, summarize = self._script()
        self.assertIn("illisible", summarize("samples", "garbage total"))

    def test_cli_pipe_exit_zero_like_install(self):
        import subprocess
        script = os.path.join(ROOT, "scripts", "summarize_index_report.py")
        for payload in ("", "{'total_presets': 211, 'indexed': 0, 'skipped': 211, 'errors': 0, 'clap_embedded': 0}"):
            proc = subprocess.run([sys.executable, script, "presets"], input=payload,
                                  capture_output=True, text=True, timeout=30)
            self.assertEqual(proc.returncode, 0, f"stdin={payload!r} stderr={proc.stderr}")
            self.assertIn("[AI-Studio]", proc.stdout)

    def test_install_sh_delegates_to_script_no_heredoc_stdin(self):
        import re
        with open(os.path.join(ROOT, "install.sh"), encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn("scripts/summarize_index_report.py", src)
        m = re.search(r"summarize_index_report\(\)\s*\{(.*?)\n\}", src, re.S)
        self.assertIsNotNone(m, "fonction summarize_index_report introuvable dans install.sh")
        body = m.group(1)
        self.assertNotIn("<<", body)  # plus d'heredoc qui volerait le stdin du pipe
        self.assertIn("summarize_index_report.py", body)


FAKE_MCP_SERVER = """import json, sys
while True:
    line = sys.stdin.readline()
    if not line:
        break
    line = line.strip()
    if not line:
        continue
    try:
        msg = json.loads(line)
    except Exception:
        continue
    if msg.get("method") == "initialize":
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": msg.get("id"), "result": {"protocolVersion": "2024-11-05", "capabilities": {}, "serverInfo": {"name": "fake", "version": "0.0.1"}}}) + "\\n")
        sys.stdout.flush()
    elif msg.get("method") == "tools/list":
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": msg.get("id"), "result": {"tools": [{"name": "t1"}]}}) + "\\n")
        sys.stdout.flush()
"""


class TestAudioMetricsWarningGuards(unittest.TestCase):
    """v4.0 hotfix — plus de UserWarning librosa à l'indexation : `n_fft=8192 trop grand
    pour un signal court` (chroma_stft) et `empty frequency set` (pitch_tuning via chroma_cqt
    sur une composante harmonique silencieuse)."""

    def test_mean_chroma_cqt_silent_component_no_warning(self):
        import audio_metrics
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            out = audio_metrics.mean_chroma_cqt(np.zeros(8192, dtype=np.float32), 22050)
        self.assertEqual(out, [0.0] * 12)
        self.assertFalse(any("empty frequency set" in str(x.message) for x in w))

    def test_short_noisy_sample_no_user_warning(self):
        import audio_metrics
        import soundfile as sf
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "short.wav")
            rng = np.random.default_rng(3)
            t = np.arange(int(0.12 * 44100)) / 44100  # ~5292 samples, dans [4096, 8192)
            y = (0.4 * np.sin(2 * np.pi * 440.0 * t) + 0.05 * rng.standard_normal(len(t))).astype(np.float32)
            sf.write(path, y, 44100)
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                entry, _vec = audio_metrics.analyze_single_file(path, with_clap=False, max_seconds=8.0)
            self.assertIn("dsp", entry)
            user_warnings = [str(x.message) for x in w if issubclass(x.category, UserWarning)]
            self.assertEqual(user_warnings, [], user_warnings)


class TestIndexDirectoryParallelAnalyze(unittest.TestCase):
    def test_parallel_analyze_matches_counts(self):
        from indexer import index_directory
        with tempfile.TemporaryDirectory() as tmp:
            samples = os.path.join(tmp, "samples")
            os.makedirs(samples)
            for i in range(8):
                _write_wav(os.path.join(samples, f"s{i}.wav"), freq=220.0 + 10 * i, sr=22050, dur=0.15)
            db = os.path.join(tmp, "samples.db")
            r = index_directory(samples, db, force=False, with_clap=False, with_dsp=False, workers=2)
            self.assertEqual(r["indexed"], 8)
            self.assertEqual(r["errors"], 0)
            self.assertEqual(r["total"], 8)
            r2 = index_directory(samples, db, force=False, with_clap=False, with_dsp=False, workers=2)
            self.assertEqual(r2["indexed"], 0)
            self.assertEqual(r2["skipped"], 8)


class TestVerifyInstallHandshake(unittest.TestCase):
    """v4.0 — validation post-install : 6 serveurs MCP (façade `ai-studio-prod` incluse) et
    handshake parallèle borné (plus de lecture bloquante ni de pipe stderr plein)."""

    def test_six_servers_incl_ai_studio_prod(self):
        import verify_install
        self.assertEqual(len(verify_install.MCP_SERVERS), 6)
        rels = [rel[0] for _n, rel in verify_install.MCP_SERVERS]
        self.assertTrue(any("ai-studio-prod" in r for r in rels))

    def test_handshake_parallel_ok_and_failure(self):
        import verify_install
        with tempfile.TemporaryDirectory() as tmp:
            good = os.path.join(tmp, "fake_ok.py")
            bad = os.path.join(tmp, "fake_bad.py")
            with open(good, "w", encoding="utf-8") as fh:
                fh.write(FAKE_MCP_SERVER)
            with open(bad, "w", encoding="utf-8") as fh:
                fh.write("import sys\nprint('boom', file=sys.stderr)\nsys.exit(1)\n")
            old = verify_install.MCP_SERVERS
            try:
                verify_install.MCP_SERVERS = (("fake-ok", ("fake_ok.py",)), ("fake-bad", ("fake_bad.py",)))
                failures = verify_install.handshake_servers(tmp, venv_py=sys.executable, timeout_s=20.0)
            finally:
                verify_install.MCP_SERVERS = old
            self.assertEqual(len(failures), 1)
            self.assertIn("fake_bad.py", failures[0])


if __name__ == "__main__":
    unittest.main()
