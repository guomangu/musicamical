#!/usr/bin/env python3
"""v4.3 chantier D6 — Hot-Execution MCP : `prod_server.py` délègue chaque appel d'outil à un
worker subprocess éphémère (`prod_worker.py`) qui importe le code EXACTEMENT présent sur disque.

Critère d'acceptation du plan : modifier un script pendant que le serveur tourne, appeler
l'outil, et la modification est prise en compte SANS redémarrer le serveur MCP
(0 `RESTART session agent` hors changement de signature d'outil).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.environ.get("AI_STUDIO_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
PROD = os.path.join(ROOT, "mcp-servers", "ai-studio-prod")
for _d in (SCRIPTS, PROD):
    if _d not in sys.path:
        sys.path.insert(0, _d)

import prod_server  # noqa: E402

WORKER = os.path.join(PROD, "prod_worker.py")


class TestWorkerSubprocess(unittest.TestCase):
    def test_worker_file_exists(self):
        self.assertTrue(os.path.isfile(WORKER))

    def test_ping_runs_in_fresh_subprocess(self):
        res = prod_server._run_worker("ping", {})
        self.assertIsNotNone(res)
        self.assertTrue(res.get("ok"))
        self.assertNotEqual(res.get("pid"), os.getpid(), "le worker doit être un processus fils distinct")

    def test_worker_reads_disk_per_call(self):
        """Critère D6 : le worker lit le fichier SUR DISQUE à chaque appel (hot-execution)."""
        with tempfile.TemporaryDirectory() as tmp:
            f = os.path.join(tmp, "probe.py")
            with open(f, "w", encoding="utf-8") as fh:
                fh.write("VALUE = 1\n")
            r1 = prod_server._run_worker("file_sha", {"path": f})
            with open(f, "w", encoding="utf-8") as fh:
                fh.write("VALUE = 2  # modifie pendant que le serveur tourne\n")
            r2 = prod_server._run_worker("file_sha", {"path": f})
        self.assertTrue(r1["ok"] and r2["ok"])
        self.assertNotEqual(r1["result"]["sha256"], r2["result"]["sha256"],
                            "la modification sur disque doit être visible sans redémarrer")
        self.assertNotEqual(r1["pid"], r2["pid"], "chaque appel = nouveau processus éphémère")

    def test_worker_disabled_by_env(self):
        old = os.environ.get("AI_STUDIO_WORKER")
        os.environ["AI_STUDIO_WORKER"] = "0"
        try:
            self.assertIsNone(prod_server._run_worker("ping", {}))
        finally:
            if old is None:
                os.environ.pop("AI_STUDIO_WORKER", None)
            else:
                os.environ["AI_STUDIO_WORKER"] = old

    def test_worker_stdin_stdout_contract(self):
        proc = subprocess.run([sys.executable, WORKER], input=json.dumps({"action": "ping", "payload": {}}),
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0)
        res = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertTrue(res["ok"])
        self.assertTrue(res["result"]["pong"])


class TestToolDelegation(unittest.TestCase):
    def test_compose_tool_delegates_to_worker(self):
        captured: dict = {}

        def fake_worker(action, payload, timeout_s=3600.0):
            captured["action"] = action
            captured["payload"] = payload
            return {"ok": True, "result": "OK-WORKER"}

        orig = prod_server._run_worker
        prod_server._run_worker = fake_worker
        try:
            out = prod_server.studio_compose("proj_x", "$lead: deg(\"0 1\", 4)", intent="dark hypnotic",
                                             palette_strategy="dark", render_mode="preview", auto_master=False)
        finally:
            prod_server._run_worker = orig
        self.assertEqual(out, "OK-WORKER")
        self.assertEqual(captured["action"], "compose")
        self.assertEqual(captured["payload"]["intent"], "dark hypnotic")
        self.assertEqual(captured["payload"]["palette_strategy"], "dark")
        self.assertEqual(captured["payload"]["render_mode"], "preview")

    def test_refine_tool_delegates_to_worker(self):
        captured: dict = {}

        def fake_worker(action, payload, timeout_s=3600.0):
            captured["action"] = action
            captured["payload"] = payload
            return {"ok": True, "result": "OK-REFINE"}

        orig = prod_server._run_worker
        prod_server._run_worker = fake_worker
        try:
            out = prod_server.studio_refine("proj_x", semantic_patch={"lead": {"oct": -1}}, export_mp3=False)
        finally:
            prod_server._run_worker = orig
        self.assertEqual(out, "OK-REFINE")
        self.assertEqual(captured["action"], "refine")

    def test_fallback_in_process_when_worker_fails(self):
        orig = prod_server._run_worker
        prod_server._run_worker = lambda *a, **k: None  # worker indisponible -> repli in-process
        try:
            # prepare_project in-process ne plante pas sur un dossier temporaire
            out = prod_server.prepare_project(tempfile.mkdtemp(), intent="test")
            self.assertIsInstance(out, str)
        finally:
            prod_server._run_worker = orig

    def test_exactly_3_tools_still_exposed(self):
        import ast
        with open(os.path.join(PROD, "prod_server.py"), encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        def is_tool_dec(d):
            if isinstance(d, ast.Call):
                f = d.func
                return (isinstance(f, ast.Name) and f.id == "_tool") or getattr(f, "attr", "") == "tool"
            return isinstance(d, ast.Name) and d.id == "_tool"
        tools = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
                 and any(is_tool_dec(d) for d in n.decorator_list)]
        self.assertEqual(sorted(tools), ["studio_compose", "studio_prepare", "studio_refine"])


if __name__ == "__main__":
    unittest.main()
