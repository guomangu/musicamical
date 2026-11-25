#!/usr/bin/env python3
"""
v3.3 hotfix — Vérification finale post-installation AI-Studio (appelée par `install.sh`, étape 8/8).

Remplace l'ancien heredoc `python3 - <<PY` d'install.sh qui plantait avec un
`FileNotFoundError` sur `projects/active/song_blueprint.json` lors d'une installation
fraîche (aucun projet actif créé pour l'instant) — avec `set -euo pipefail`, toute
l'installation échouait à la dernière étape.

Comportement :
  - `validate_active_blueprint(studio)` : si `projects/active/song_blueprint.json`
    est ABSENT → validation ignorée proprement (normal à l'installation initiale) ;
    s'il est PRÉSENT → validation stricte contre `schemas/song_blueprint.schema.json`
    (échec = exit 1 avec message clair).
  - `handshake_servers(studio)` : handshake JSON-RPC `initialize` + `tools/list`
    sur les 6 serveurs MCP (5 bas niveau + façade `ai-studio-prod`), en parallèle avec
    délai par serveur, un rapport par serveur, exit 1 si l'un échoue.

Usage :
  .venv/bin/python scripts/verify_install.py --studio "$STUDIO_DIR"
  .venv/bin/python scripts/verify_install.py --skip-mcp          # tests / CI
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import select
import subprocess
import sys
import time

ROOT = os.environ.get("AI_STUDIO_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

MCP_SERVERS = (
    ("gridline", ("scripts/gridline-mcp.sh",)),
    ("audio-index", ("mcp-servers/audio-index/audio_index_server.py",)),
    ("audio-analyzer", ("mcp-servers/audio-analyzer/audio_analyzer_server.py",)),
    ("vocaloid", ("mcp-servers/vocaloid/vocaloid_server.py",)),
    ("reverse-engineer", ("mcp-servers/reverse-engineer/reverse_engineer_server.py",)),
    ("ai-studio-prod", ("mcp-servers/ai-studio-prod/prod_server.py",)),
)


def validate_active_blueprint(studio: str, schema_path: str | None = None) -> tuple[bool, str]:
    """Valide le blueprint actif s'il existe ; sinon signale un saut propre (pas d'exception).

    Retourne (ok, message). Lève jsonschema.ValidationError si le fichier existe mais est invalide.
    """
    import jsonschema

    schema_path = schema_path or os.path.join(studio, "schemas", "song_blueprint.schema.json")
    bp_path = os.path.join(studio, "projects", "active", "song_blueprint.json")
    if not os.path.isfile(bp_path):
        return True, (
            "SKIP: aucun projet actif (projects/active/song_blueprint.json absent) — "
            "validation de schéma ignorée, normale à l'installation initiale "
            "(créer un projet via studio_prepare_session pour la valider)."
        )
    with open(schema_path, encoding="utf-8") as fh:
        schema = json.load(fh)
    with open(bp_path, encoding="utf-8") as fh:
        bp = json.load(fh)
    jsonschema.validate(bp, schema)
    return True, f"OK: song_blueprint.json actif validé contre schemas/song_blueprint.schema.json ({bp_path})"


def _handshake_one(studio: str, venv_py: str, rel: tuple[str, ...], timeout_s: float = 60.0) -> tuple[str, str, str | None]:
    """Handshake un serveur MCP avec deadline (select sur le pipe stdout, Linux).

    Retourne (label, ligne OK, erreur|None). `stderr` est jeté : un serveur bavard ne doit
    pas bloquer sur un pipe plein pendant la validation.
    """
    label = os.path.basename(rel[0])
    cmd = [os.path.join(studio, rel[0])] if rel[0].endswith(".sh") else [venv_py, os.path.join(studio, rel[0])]
    init_msg = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                           "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                                      "clientInfo": {"name": "install-check", "version": "1.0"}}}) + "\n"
    notif_msg = json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n"
    tools_msg = json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}) + "\n"
    proc = None
    try:
        env = os.environ.copy()
        env["AI_STUDIO_DIR"] = studio
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, env=env, text=True, bufsize=1)
        deadline = time.time() + timeout_s

        def _readline_bounded() -> str:
            remaining = deadline - time.time()
            if remaining <= 0:
                raise TimeoutError("timeout handshake MCP")
            ready, _, _ = select.select([proc.stdout.fileno()], [], [], remaining)
            if not ready:
                raise TimeoutError("timeout handshake MCP")
            line = proc.stdout.readline()
            if not line:
                raise RuntimeError("serveur MCP fermé prématurément")
            return line

        proc.stdin.write(init_msg)
        proc.stdin.flush()
        r1 = json.loads(_readline_bounded())
        if "result" not in r1:
            raise RuntimeError(f"échec initialize: {r1}")
        proc.stdin.write(notif_msg + tools_msg)
        proc.stdin.flush()
        r2 = json.loads(_readline_bounded())
        t_count = len(r2.get("result", {}).get("tools", []))
        if t_count <= 0:
            raise RuntimeError(f"aucun outil exposé: {r2}")
        server_name = r1["result"].get("serverInfo", {}).get("name", "?")
        return label, f"MCP OK: {label} ({server_name}, tools={t_count})", None
    except Exception as exc:
        return label, "", f"{label}: {exc}"
    finally:
        if proc is not None:
            for stream in (proc.stdin, proc.stdout):
                try:
                    if stream is not None:
                        stream.close()
                except Exception:
                    pass
            try:
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass


def handshake_servers(studio: str, venv_py: str | None = None, timeout_s: float = 60.0) -> list[str]:
    """Handshake JSON-RPC initialize + tools/list sur les 6 serveurs MCP, en parallèle
    (un délai `timeout_s` par serveur). Retourne la liste des échecs."""
    venv_py = venv_py or os.path.join(studio, ".venv", "bin", "python")
    if not os.path.isfile(venv_py):
        venv_py = sys.executable

    with concurrent.futures.ThreadPoolExecutor(max_workers=min(6, len(MCP_SERVERS))) as ex:
        futs = [ex.submit(_handshake_one, studio, venv_py, rel, timeout_s) for _name, rel in MCP_SERVERS]
        outcomes = [f.result() for f in futs]
    failures: list[str] = []
    for _label, ok_line, err in outcomes:
        if err is not None:
            failures.append(err)
            print(f"MCP ÉCHEC: {err}")
        else:
            print(ok_line)
    return failures


def main() -> int:
    ap = argparse.ArgumentParser(description="Vérification finale post-installation AI-Studio")
    ap.add_argument("--studio", default=ROOT, help="Racine du studio (défaut: AI_STUDIO_DIR ou parent du script)")
    ap.add_argument("--skip-blueprint", action="store_true", help="Ne pas valider projects/active/song_blueprint.json")
    ap.add_argument("--skip-mcp", action="store_true", help="Ne pas faire le handshake des serveurs MCP")
    args = ap.parse_args()
    studio = os.path.abspath(os.path.expanduser(args.studio))

    rc = 0
    if not args.skip_blueprint:
        try:
            ok, msg = validate_active_blueprint(studio)
            print(msg)
            if not ok:
                rc = 1
        except Exception as exc:
            print(f"ÉCHEC validation song_blueprint.json actif: {exc}")
            rc = 1
    if not args.skip_mcp:
        failures = handshake_servers(studio)
        if failures:
            for f in failures:
                print(f"MCP ÉCHEC: {f}")
            print("Validation handshake & tools/list des serveurs MCP : ÉCHEC")
            rc = 1
        else:
            print("Validation JSON Schema + handshake & tools/list des 6 serveurs MCP : OK")
    return rc


if __name__ == "__main__":
    sys.exit(main())
