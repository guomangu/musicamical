#!/usr/bin/env python3
"""Chantier 4 (spec v2.0 §4.2) — humanisation statistique du groove (pocket GrooVAE) :
`groove_lib.apply_pocket_humanization` — covariance vélocité-timing (fantômes retardées,
accents avancés) + caisse claire laid-back sur les temps 2 & 4 (Lo-Fi/Boom-Bap/R&B).
"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.environ.get("AI_STUDIO_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(ROOT, "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

from groove_lib import (  # noqa: E402
    ACCENT_VEL_MIN,
    GHOST_VEL_MAX,
    apply_pocket_humanization,
    ms_to_beats,
)

BPM = 120.0


def _notes(*specs):
    return [{"start": s, "length": 0.25, "pitch": 60, "velocity": v} for s, v in specs]


class TestPocketHumanization(unittest.TestCase):
    def test_ghost_note_delayed_accent_advanced(self):
        notes = _notes((0.0, 30), (1.0, 120), (2.0, 90))
        out = apply_pocket_humanization(notes, BPM, track_name="Perc", profile="trap_rap", seed=1)
        ghost_dt = out[0]["start"] - 0.0
        accent_dt = out[1]["start"] - 1.0
        self.assertGreaterEqual(ghost_dt, ms_to_beats(6.0, BPM) - 1e-6)
        self.assertLessEqual(ghost_dt, ms_to_beats(16.0, BPM) + 1e-6)
        self.assertLessEqual(accent_dt, ms_to_beats(-1.0, BPM) + 1e-6)
        self.assertGreaterEqual(accent_dt, ms_to_beats(-5.0, BPM) - 1e-6)
        self.assertEqual(out[2]["start"], 2.0)  # vélocité moyenne : inchangé

    def test_snare_laidback_only_in_laidback_genres(self):
        snare = _notes((1.0, 100), (3.0, 100))  # temps 2 et 4
        out_lofi = apply_pocket_humanization(snare, BPM, track_name="Snare", profile="lofi_chill", seed=3)
        out_edm = apply_pocket_humanization([dict(n) for n in snare], BPM, track_name="Snare", profile="edm_club", seed=3)
        self.assertGreater(out_lofi[0]["start"], 1.0, "snare temps 2 laid-back en lofi")
        self.assertGreater(out_lofi[1]["start"], 3.0, "snare temps 4 laid-back en lofi")
        self.assertEqual(out_edm[0]["start"], 1.0, "pas de laid-back en EDM")
        self.assertEqual(out_edm[1]["start"], 3.0)
        # bornes du laid-back : +4..+10 ms
        self.assertGreaterEqual(out_lofi[0]["start"] - 1.0, ms_to_beats(4.0, BPM) - 1e-6)
        self.assertLessEqual(out_lofi[0]["start"] - 1.0, ms_to_beats(10.0, BPM) + 1e-6)

    def test_deterministic_and_empty(self):
        notes = _notes((0.0, 30), (0.5, 120), (1.0, 100))
        a = apply_pocket_humanization(notes, BPM, "Hat", "trap_rap", seed=7)
        b = apply_pocket_humanization([dict(n) for n in notes], BPM, "Hat", "trap_rap", seed=7)
        self.assertEqual(a, b)
        self.assertEqual(apply_pocket_humanization([], BPM), [])
        # vélocités seuils
        self.assertGreaterEqual(GHOST_VEL_MAX, 50)
        self.assertLessEqual(ACCENT_VEL_MIN, 105)


if __name__ == "__main__":
    unittest.main()
