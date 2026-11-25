# 07 — Guide de Conception Strudel (anti-erreurs capitalisées)

Capitalise les **erreurs réelles** rencontrées en production (session `rap-serene-rythme`, C mineur)
pour concevoir vite et juste. Outil associé : `scripts/preflight_strudel_gate.py` (simulateur
pre-flight du MIDI Gate, **0 rendu, 0 LLM, ~2 s**) — à lancer **avant** tout rendu coûteux.

## 1. Dictionnaire degrés Strudel ↔ demi-tons (C mineur, octave 4)

`deg("<...>", oct)` utilise `SCALES.minor = [0,2,3,5,7,8,10]` et
`midi = tonic + 12*(deg//7) + SCALES[deg%7]` avec `tonic = 60`.
Reproduit en CLI : `python3 scripts/preflight_strudel_gate.py --degrees C:minor`.

| degré | pc | nom | rel. (st) | MIDI |
|------:|---:|-----|----------:|-----:|
| 0 | 0 | C | **+0** | 60 |
| 1 | 2 | D | **+2** | 62 |
| 2 | 3 | D# | **+3** | 63 |
| 3 | 5 | F | **+5** | 65 |
| 4 | 7 | G | **+7** | 67 |
| 5 | 8 | G# | **+8** | 68 |
| 6 | 10 | A# | **+10** | 70 |
| 7 | 0 | C | **+12** | 72 |
| 8 | 2 | D | **+14** | 74 |

**Piège n°1** : le degré 4 n'est PAS « +4 st », c'est **+7 st** (G en mineur). Concevoir les
motifs depuis cette table, jamais depuis l'intuition « degré = intervalle en tons ».

## 2. Sémantique de `slow(N)` dans le pont headless

`queryArc(0, bars)` retourne des événements en **cycles** (1 cycle = 1 mesure = 4 beats).
`.slow(N)` divise le nombre de degrés par mesure : `degrés/mesure = L/N`, `step = 4/(L/N)` beats.
Reproduit en CLI : `preflight_strudel_gate.py` (fonction `slow_step`).

| seq de L degrés | modificateur | degrés/mesure | step (beats) |
|---|---|---:|---:|
| 8 | aucun | 8 | 0.5 (croches) |
| 8 | `.slow(8)` | 1 | 4.0 (1/mesure) |
| 128 | `.slow(8)` | 16 | 0.25 (doubles-croches) |
| 256 | aucun | 256 | 0.015625 (1/64) — **dangereux** |

**Règle** : 16/mesure → seq de 128 avec `.slow(8)` ; 8/mesure → seq de 64 avec `.slow(8)` ou seq de 8 sans slow.

## 3. Le piège destructeur : `fix_lead_b2_clashes`

Dans `scripts/strudel_to_score_ir.py` (fonction `fix_lead_b2_clashes`), **après** `autofix_score_ir`,
le pont réécrit toute note du lead/counter située à **1 demi-ton d'une pc de l'accord actif** vers
la pc de gamme la plus proche. Sur la session fautive : **459–549 notes réécrites** (D→Eb, G→Ab…).

**Conséquence** : un motif leap+fill conçu à la main est cassé après fix → `gap_fill` s'effondre
(1.0 → 0.2–0.4). **Stratégie** : concevoir avec des degrés b2-safe (§4), et lancer le pre-flight
**avant** toute tentative de rendu pour voir le `gap_fill` réel post-fix (`b2_fixed_by_bridge` dans
le rapport dit combien de notes ont été réécrites).

## 4. Table des degrés b2-safe par accord (progression i-VI-III-VII en C mineur)

Un degré est « b2-unsafe » sur un accord si sa pc est à 1 st d'une pc de l'accord (mêmes qualités
d'accords que le pont, via `parse_chord_symbol`).
Reproduit en CLI : `preflight_strudel_gate.py --safe-degrees "Cm7 Abmaj7 Ebmaj7 Bb7" --mode minor`.

| Accord | pcs | degrés interdits (clash b2) | degrés sûrs |
|---|---|---|---|
| Cm7 | {0, 3, 7, 10} | 1 (D), 5 (Ab) | **0, 2, 3, 4, 6, 7** |
| Abmaj7 | {0, 3, 7, 8} | 1 (D), 4 (G), 5 (Ab) | **0, 2, 3, 6, 7** |
| Ebmaj7 | {2, 3, 7, 10} | 1 (D), 2 (Eb), 5 (Ab) | **0, 3, 4, 6, 7** |
| Bb7 | {2, 5, 8, 10} | 2 (Eb), 4 (G) | **0, 1, 3, 5, 6, 7** |

**Intersection (sûr partout)** : degrés **[0, 3, 6, 7]** = C, F, Bb, C' (deg 7 = octave de 0).
Conclusion : sur i-VI-III-VII, un lead 100 % b2-safe ne peut pas avoir `gap_fill = 1.0` sur les 4
accords simultanément. Solutions : (1) changer la grille (ex. Cm7 seul → sûrs = {0,2,3,4,6,7}),
(2) accepter quelques b2 et vérifier au gate final, (3) motif adapté par accord (§6).

## 5. Règle du clamp d'octave (LEAD_APEX_MAX = 74)

`apply_register_guards` fait `while p > 74: p -= 12` : degré max sans clamp = **8** (D' = 74),
au-delà −12 st. **Règle** : `base_max + motif_max ≤ 8`. Un clamp de −12 st crée un leap non
rempli → `gap_fill` s'effondre. Le pre-flight rapporte `lead_apex_clamped` (guards).

## 6. Stratégie : motif par section, frontières propres

Le gate mesure la **topline complète concaténée** (toutes sections à la suite) : les frontières
inter-sections comptent dans `gap_fill`. Règles pour `gap_fill = 1.0` :

1. Chaque motif commence et finit sur le **même degré** → diff de frontière = 0.
2. Tout leap (|d| ≥ 5 st) doit être suivi d'un mouvement **contraire de 1 à 3 st**
   (`(d * next < 0) and (1 <= abs(next) <= 3)`).
3. Pattern éprouvé (16 pas, `.slow(2)`, 2 rests) : `0 3 2 1 ~ 1 2 1 0 ~ 2 3 2 1 0 ~` →
   diffs +5, −2, −1, −1, +3, −2… le +5 est suivi de −2 ✓ (pre-flight : gf=1.0, sw=0.93, rest=0.42).
   C'est le lead de l'exemple canonique du [runbook V4](AGENT_RUNBOOK_V4.md).
4. `rest_ratio` ∈ [0.20, 0.50] : ~2 `~` sur 16 éléments avec durées < step → ~0.23–0.42.
   Ne pas mettre de `.legato()` (allonge les notes, `rest_ratio` peut tomber sous 0.20).

## 7. Checklist pre-flight avant tout rendu final

```bash
# 1. Pont (génère haps + score_ir + arrangement_plan + rapport preflight intégré)
.venv/bin/python scripts/strudel_to_score_ir.py projects/<nom> --eval
# 2. PRE-FLIGHT DU GATE (0 rendu, ~2 s) — l'étape qui aurait évité ~10 itérations
.venv/bin/python scripts/preflight_strudel_gate.py projects/<nom>
# Doit sortir "passed": true : gap_fill >= 0.60, stepwise >= 0.65, rest in [0.20, 0.50]
# 3. Seulement ensuite : le rendu final coûteux
```

Si le pre-flight échoue sur `gap_fill` : inspecter `bad_leaps` (avec `start_beat`) et corriger
le motif dans `composition.strudel` — jamais `arrangement_plan.json` à la main (régénéré).

## 8. Pièges déjà rencontrés (ne pas refaire)

| # | Piège | Symptôme | Fix |
|---|---|---|---|
| 1 | `V < -0.2` dans `@meta` | split de registre sombre plie le lead par octaves → leaps ±12 | Garder `V=0.0` |
| 2 | `sometimesBy(x>0, degradeBy)` sur lead/counter | clashes b2 bloquants au gate final | `sometimesBy(0.0, …)` ou supprimer sur le lead mélodique |
| 3 | `.slow(32).fast(2)` ou `.legato(1.2)` | notes trop denses/allongées → `rest_ratio` < 0.20 | durées < step, ~2 rests/16 |
| 4 | seq longue sans `.slow()` | notes de 1/64 de mesure | `.slow(8)` avec seq = 8×bars voulues |
| 5 | base montante degrés 0→4 | deg 11 = 79 → clamp −12 → gap_fill 0.2–0.4 | base max 1, motif max deg 7 |
| 6 | modifier `arrangement_plan.json` à la main | écrasé au prochain `--eval` | toujours éditer `composition.strudel` |
| 7 | motif conçu sans penser à `fix_lead_b2_clashes` | ~500 notes réécrites, gap_fill détruit | pre-flight + degrés b2-safe (§4) |

## 9. Références rapides

- Gate : `mcp-servers/audio-analyzer/musicality_metrics.py` (seuils : stepwise ≥ 0.65, gap_fill ≥ 0.60, rest ∈ [0.20, 0.50], contrary_or_oblique ≥ 0.55)
- Pont : `scripts/strudel_prelude.mjs` (`deg`, `bassline`, `kit`), `scripts/strudel_to_score_ir.py` (`fix_lead_b2_clashes`, `LEAD_APEX_MAX = 74`)
- Pre-flight : `scripts/preflight_strudel_gate.py` (`--degrees`, `--safe-degrees`, rapport `bad_leaps`)
- Tests : `tests/test_preflight_gate.py`, `tests/test_canonical_examples.py` (exemple canonique à 0 correction ET pre-flight PASS)
