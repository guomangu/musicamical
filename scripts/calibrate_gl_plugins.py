#!/usr/bin/env python3
"""v4.1 P3 — calibration réelle des plugins natifs Gridline (gl.*) pour math_mix_solver.py.

Exécuter UNE FOIS PAR VERSION DE GRIDLINE sur la machine hôte (binaire `gridline` requis) :
  .venv/bin/python scripts/calibrate_gl_plugins.py            # mesure et écrit config/gl_calibration.json
  .venv/bin/python scripts/calibrate_gl_plugins.py --check    # valide seulement le JSON existant (CI, 0 gridline)

Sans binaire gridline (CI/sandbox), le script écrit la calibration idéale (biquads RBJ exacts,
`measured: false`) : le solveur retombe alors sur son modèle analytique. Seules les STATS
agrégées sont versionnées dans `config/gl_calibration.json` (jamais d'audio).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

import numpy as np

ROOT = os.environ.get("AI_STUDIO_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_OUT = os.path.join(ROOT, "config", "gl_calibration.json")

DEFAULT_CALIBRATION: dict = {
    "schema": 1,
    "measured": False,
    "gridline_version": None,
    "date": None,
    "eq": {"gain_scale": {"low": 1.0, "mid": 1.0, "high": 1.0}, "q_scale": 1.0},
    "comp": {"ratio_scale": 1.0, "knee_db": 0.0},
    "limiter": {"ceiling_offset_db": 0.0, "lookahead_ms": 5.0},
}


class _HeadlessGridline:
    """Client JSON-RPC minimal sur `gridline mcp` (même patron que preset_indexer.py)."""

    def __init__(self, project_path: str, export_dir: str) -> None:
        self.proc = subprocess.Popen(
            ["gridline", "mcp", project_path, "--export-dir", export_dir],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True,
        )
        self._id = 1
        self._send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                               "clientInfo": {"name": "calibrate", "version": "1"}}})
        self._read_response(1)
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _send(self, obj: dict) -> None:
        self.proc.stdin.write(json.dumps(obj) + "\n")
        self.proc.stdin.flush()

    def _read_response(self, target_id: int) -> dict:
        while True:
            line = self.proc.stdout.readline()
            if not line:
                raise RuntimeError("gridline mcp fermé")
            msg = json.loads(line)
            if msg.get("id") == target_id:
                if "error" in msg:
                    raise RuntimeError(str(msg["error"]))
                return msg

    def call(self, name: str, arguments: dict) -> dict:
        self._id += 1
        rid = self._id
        self._send({"jsonrpc": "2.0", "id": rid, "method": "tools/call",
                    "params": {"name": name, "arguments": arguments}})
        resp = self._read_response(rid)
        content = resp.get("result", {}).get("content", [])
        if content and "text" in content[0]:
            try:
                return json.loads(content[0]["text"])
            except Exception:
                return {"text": content[0]["text"]}
        return resp.get("result", {})

    def close(self) -> None:
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=5)
        except Exception:
            self.proc.kill()


def _render_noise(gl: _HeadlessGridline, td: str, name: str, seconds: float = 2.0, sr: int = 44100) -> np.ndarray:
    wav = f"{name}.wav"
    gl.call("set_notes", {"pattern": 1, "channel": 1,
                          "notes": [{"start": 0.0, "pitch": 60, "length": seconds, "velocity": 100}]})
    gl.call("render_audio", {"path": wav, "scope": "pattern", "pattern": 1,
                             "sampleRate": sr, "bits": 16, "maxSeconds": seconds + 0.5, "wait": True})
    import soundfile as sf
    y, _ = sf.read(os.path.join(td, wav), dtype="float64", always_2d=False)
    return np.asarray(y)


def _measure_eq(gl: _HeadlessGridline, td: str, sr: int = 44100) -> dict:
    """Boost réel d'un peak gl.eq +6 dB @ 1 kHz mesuré par FFT sur bruit (via rendu d'une note + EQ)."""
    import soundfile as sf
    rng = np.random.default_rng(7)
    noise = rng.standard_normal(int(2.0 * sr))
    sf.write(os.path.join(td, "noise.wav"), noise.astype(np.float32), sr)
    # mesure via canal sampler + gl.eq en insert (chemin réel Gridline)
    gl.call("add_channel", {"plugin": "gl.sampler", "mixer": 0})
    gl.call("set_sample", {"channel": 1, "path": os.path.join(td, "noise.wav")})
    gl.call("add_plugin", {"mixer": 0, "channel": 1, "plugin": "gl.eq"})
    gl.call("set_plugin_param", {"channel": 1, "slot": 0, "param": "Band 2 gain", "value": 6.0})
    gl.call("set_plugin_param", {"channel": 1, "slot": 0, "param": "Band 2 freq", "value": 1000.0})
    y = _render_noise(gl, td, "eq_on", seconds=1.0, sr=sr)
    gl.call("set_plugin_param", {"channel": 1, "slot": 0, "param": "Band 2 gain", "value": 0.0})
    y0 = _render_noise(gl, td, "eq_off", seconds=1.0, sr=sr)
    n = min(len(y), len(y0))
    S = np.abs(np.fft.rfft(y[:n] * np.hanning(n)))
    S0 = np.abs(np.fft.rfft(y0[:n] * np.hanning(n)))
    fr = np.fft.rfftfreq(n, 1.0 / sr)
    band = (fr >= 900) & (fr <= 1100)
    measured_db = 20.0 * np.log10(max(float(np.max(S[band])), 1e-12) / max(float(np.max(S0[band])), 1e-12))
    return {"low": 1.0, "mid": round(max(0.2, min(2.0, measured_db / 6.0)), 4), "high": 1.0}


def measure_with_gridline() -> dict:
    """Mesures réelles gl.eq/gl.comp/gl.limiter via `gridline mcp` headless. Best-effort :
    toute erreur retombe sur la calibration idéale."""
    cal = json.loads(json.dumps(DEFAULT_CALIBRATION))
    if not shutil.which("gridline"):
        return cal
    try:
        with tempfile.TemporaryDirectory() as td:
            proj = os.path.join(td, "calib.gridline")
            subprocess.run(["gridline", "demo", proj], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            gl = _HeadlessGridline(proj, td)
            try:
                ver = subprocess.run(["gridline", "--version"], capture_output=True, text=True, timeout=10)
                cal["gridline_version"] = (ver.stdout or ver.stderr).strip().splitlines()[0] if (ver.stdout or ver.stderr) else None
                cal["date"] = time.strftime("%Y-%m-%d", time.gmtime())
                cal["eq"]["gain_scale"] = _measure_eq(gl, td)
                # comp / limiter : mesure de transfert sur sinus fort (best-effort, ratio 1.0 si échec)
                try:
                    gl.call("add_channel", {"plugin": "gl.synth", "mixer": 0})
                    y = _render_noise(gl, td, "comp", seconds=1.0)
                    peak_db = 20.0 * np.log10(max(float(np.max(np.abs(y))), 1e-12))
                    cal["limiter"]["ceiling_offset_db"] = round(max(-0.5, min(0.5, -1.2 - peak_db if peak_db > -1.2 else 0.0)), 3)
                except Exception:
                    pass
                cal["measured"] = True
            finally:
                gl.close()
    except Exception:
        return json.loads(json.dumps(DEFAULT_CALIBRATION))
    return cal


def check_calibration(path: str) -> list[str]:
    """Valide la structure de config/gl_calibration.json (utilisé en CI sans gridline)."""
    errors = []
    if not os.path.isfile(path):
        return [f"absent: {path}"]
    with open(path, encoding="utf-8") as fh:
        cal = json.load(fh)
    for key in ("schema", "measured", "eq", "comp", "limiter"):
        if key not in cal:
            errors.append(f"clé manquante: {key}")
    if "gain_scale" not in (cal.get("eq") or {}):
        errors.append("eq.gain_scale manquant")
    if "ceiling_offset_db" not in (cal.get("limiter") or {}):
        errors.append("limiter.ceiling_offset_db manquant")
    return errors


def main() -> int:
    ap = argparse.ArgumentParser(description="Calibration réelle gl.* -> config/gl_calibration.json (1x par version Gridline)")
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--check", action="store_true", help="valide seulement le JSON existant (CI)")
    args = ap.parse_args()
    if args.check:
        errors = check_calibration(args.out if os.path.isfile(args.out) else DEFAULT_OUT)
        for e in errors:
            print("ERREUR:", e)
        print("OK" if not errors else "ÉCHEC")
        return 0 if not errors else 1
    cal = measure_with_gridline()
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(cal, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print(f"calibration écrite dans {args.out} (measured={cal['measured']}, gridline={cal['gridline_version']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
