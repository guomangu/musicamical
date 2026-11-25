# Facette 6 — Pont Strudel Headless (v4.0) : « Strudel → ScoreIR → AI-Studio → Gridline »

> **Principe v4.0** : Strudel apporte un **frontend de composition ultra-compact** (économie de tokens LLM,
> ~300–600 tokens par morceau au lieu de ~3 000–6 000 en JSON) évalué **en Node.js headless pur**
> (`@strudel/core` + `queryArc`, 0 Chromium, 0 `snd-virmidi`, 0 temps réel). **Toute la couche de
> vérification musicale reste en Python local à 0 token LLM** (`ScoreIR`, voice leading + LIL,
> `midi_autofix`, clamp frontière 20 ms, `FullCoverageCoreValidator`) : on garde 100 % de la sécurité
> harmonique/acoustique de la v3.x avec ~85 % d'économie de tokens.
>
> Archive détaillée et comparatif : [REFERENCE_ARCHIVE.md](REFERENCE_ARCHIVE.md) (section v4.0).
> Index général : [00_SOMMAIRE.md](00_SOMMAIRE.md) (Phase 9). Règles normatives : [../.rules](../.rules) (addendum v4.0).

---

## 1. Architecture en 4 Couches (0 navigateur dans le pipeline IA)

| Couche | Fichier / Outil | Rôle | Coût |
| :--- | :--- | :--- | :--- |
| **1 — Strudel headless** | `scripts/strudel_eval.mjs` (`@strudel/core` + `@strudel/mini` + `@strudel/tonal`) | Évalue `<projet>/composition.strudel` avec `queryArc(0, bars)` + `seed` déterministe, sort un JSON de Haps sur stdout | < 100 ms, 0 token |
| **2 — Compilateur & garde-fous** | `scripts/strudel_to_score_ir.py` | Haps → pistes par rôle → `ScoreIR` + corrections (registre 808, apex Lead, LIL, b2, inertie) + `arrangement_plan.json` | < 150 ms, 0 token |
| **3 — AI-Studio DSP & Mix** | `compile_song.py`, `gl_dsl.py`, `gain_staging_lib.py`, `autofix_transaction.py`, `render_and_audit.py` | Identique v3.x : le plan généré est compilé par le pipeline existant | 0 token |
| **4 — Gridline 0.7.7** | `projet.gridline` headless (CI 7/7) | Rendu déterministe ; pré-écoute Live Strudel **optionnelle** hors boucle IA | — |

Flux : `[prompt]` → Tour 1 `studio_prepare_session` → **Tour 2 : le LLM écrit 1 seul `composition.strudel`**
→ `node scripts/strudel_eval.mjs composition.strudel --bars N --seed S` → `strudel_to_score_ir.py` →
`score_ir.json` + `arrangement_plan.json` → `compile_song.py <projet>` (inchangé) → Tours 3–4 (inchangés).

## 2. Contrat du Fichier `composition.strudel`

Exemple canonique : [`../assets/composition_trap_example.strudel`](../assets/composition_trap_example.strudel).

1. **Métadonnées déclaratives** en commentaire haut de fichier (JSON multi-lignes) :
   `// @strudel-meta {"bpm": 140, "key": "C", "mode": "minor", "time_signature": [4, 4], "seed": 42, "mix_profile": "trap_rap", "sections": [{"name": "intro", "bars": 4}, ...]}`.
2. **1 piste nommée `$role: <expression>` = 1 rôle atomique** (jamais 2 percussions sur le même canal) :
   `$kick: s("bd ~ ~ ~")`, `$snare: s("~ ~ cp ~")`, `$hihat: s("hh*8")`, `$bass: note("<c2 ...>")`,
   `$chords: chord("<Cm7 Abmaj7 Fm7 G7>").slow(2)`, `$lead: note("...").scale("C:minor")`.
3. **Composer en degrés et accords symboliques** (jamais de noms de notes bruts sur les pistes
   basse/mélodie susceptibles de se désynchroniser) : `chord("<...>")` pilote la `harmonic_grid`,
   `.scale("C:minor")` quantifie la mélodie, `arrange([4, intro], [8, verse], ...)` ou `sections` dans
   `@strudel-meta` définit la macro-forme.
4. **Déterminisme** : `--seed S` (ou `seed` du blueprint) est injecté via le contrôle `seed()` avant
   chaque `queryArc` → 2 exécutions identiques produisent le même JSON d'Haps (testé en CI).

## 3. Les 5 Règles d'Or (imposées par le pont et les tests)

1. **Évaluation Node.js headless obligatoire dans le pipeline IA** : `strudel_eval.mjs` sort les Haps
   (début/fin en cycles, `notes` MIDI, `s`, `gain`, `pan`, `cutoff`/`lpf` échantillonné, `chord`)
   en JSON (`schemas/strudel_haps.schema.json`). Chromium/VirMIDI reste un outil de jam humain optionnel.
2. **1 cycle Strudel = 1 mesure** : `start_beat = cycle * beats_per_bar`. Les courbes continues
   (`.lpf(sine.range(1200, 4200).slow(8))`) sont échantillonnées par hap et transmises comme
   `cutoff_hz` par section dans le plan (`role_modifiers`).
3. **1 piste `$` = 1 rôle atomique** : le pont mappe chaque piste sur son propre canal/insert/bus
   via `_default_channels_for_plan` (bus `17..22`, sidechain Kick → 808 préservés).
4. **La couche Python 0 token n'est JAMAIS supprimée** : `ScoreIR`, voice leading + LIL,
   `autofix_score_ir`, clamp frontière 20 ms, `sanitize_note_overlaps`, `FullCoverageCoreValidator`,
   `stem_matrix_evaluator`, `render_and_audit.py` restent actifs.
5. **Double frontend vers le même ScoreIR** : `arrangement_plan.json` (v3.x) et `composition.strudel`
   (v4.0) convergent vers le même `ScoreIR` ; le compilateur v3.x reste la cible tant que le pont
   n'a pas passé `tests/regression_multi_genre.py` 7/7 (PQ >= 7.8).

## 4. Garde-Fous Appliqués par `strudel_to_score_ir.py` (0 token, < 150 ms)

| Bug de l'exemple v4.0 | Garde-fou du pont | Constante |
| :--- | :--- | :--- |
| 808 infrason (c1=24, ab0=20…) | pli par octaves dans le registre basse | `BASS_REGISTER = (31, 48)` |
| Apex Lead perçant (eb6=87) | pli par octaves sous l'apex | `LEAD_APEX_MAX = 74` |
| Fausse relation Bb contre B (G7) | `fix_lead_b2_clashes` + `autofix_score_ir` (snap gamme) | — |
| Accords boueux (LIL) | `optimize_voicings_lil` (miroir de `optimize_voice_leading` + LIL) dans `[48, 72]` | `LOW_INTERVAL_LIMIT` |
| Chevauchement basse mono | dédup des onsets mono + `enforce_frequency_inertia` + `sanitize_note_overlaps` (10 ms) | — |
| Clash de frontière d'accord | clamp frontière 20 ms (`compile_score_ir_to_notes`) | `DEFAULT_GUARD_MS = 20` |
| Clone 8 mesures / arc plat / outro | `validate_full_score_ir` (FullCoverageCoreValidator) | `CLONE_SIMILARITY_MAX = 0.94` |

## 5. Commandes

```bash
# 1) Evaluer le morceau en headless (deps : npm install dans scripts/, voir scripts/package.json)
node scripts/strudel_eval.mjs <projet>/composition.strudel --bars 32 --seed 42 --out <projet>/composition.strudel.haps.json

# 2) Compiler vers ScoreIR + arrangement_plan.json (ré-évalue si --eval)
python3 scripts/strudel_to_score_ir.py <projet> --eval --bars 32

# 3) Pipeline v3.x inchangé ensuite
python3 scripts/compile_song.py <projet>            # arrangement_plan.json -> projet.gridline
python3 scripts/compile_song.py <projet> --probe    # sonde composite 12 mesures
```

## 6. Tests

| Fichier | Commande | Couvre |
| :--- | :--- | :--- |
| `tests/test_strudel_bridge.py` | `.venv/bin/python -m unittest tests/test_strudel_bridge.py -v` | Garde-fous sur l'exemple fautif (registre, apex, b2, LIL, 1 piste = 1 rôle), plan valide schéma, déterminisme seed, eval Node reel + E2E (skip si `node`/`@strudel` absent) |
| `tests/test_token_budget_v4.py` | `.venv/bin/python -m unittest tests/test_token_budget_v4.py -v` | Façade `ai-studio-prod` = 3 outils (AST), budget schemas+runbook+retour prepare < 1 800 tokens, runbook V4 <= 750 tokens, patch `replace_track`, `handoff.txt` <= 350 tokens, prélude/lint/compose E2E (skip si `node` absent) |
| `tests/test_math_mix_solver.py` | `.venv/bin/python -m unittest tests/test_math_mix_solver.py -v` | Contrat §6.1, conflit spectral sévère, perte L/R+mono, parsimonie ±0,3 dB, source défectueuse -> `saturated_tracks_for_swap`, CPU < 1,0 s (8 stems stéréo x 12 mesures) |
| `tests/test_canonical_examples.py` | `.venv/bin/python -m unittest tests/test_canonical_examples.py -v` | **P1** exemples canoniques (bloc js du runbook V4 + asset) : lint → eval → pont avec **0 correction de garde-fou** (skip si `node` absent) |
| `tests/regression_multi_genre_strudel.py` | `.venv/bin/python -m unittest tests.regression_multi_genre_strudel.py -v` | **P6** parité CI **7/7** : fixtures `tests/fixtures/strudel_<profil>.strudel` (incl. `jazz_acoustic` 3/4 et `classical_ambient` 3/4 sans batterie) → lint → eval → pont → solveur sur stems synthétiques (0 erreur, mutation ≤ 25 %, 0 diagnostic `ERR_OVER_REPAIRED`, full_coverage OK, plan valide schéma, basse `[31,48]`, sections = `form=`, déterminisme seed) ; rendu + **PQ ≥ 7.8** seulement si binaire `gridline` présent |
| `tests/test_calibration_ownership.py` | `.venv/bin/python -m unittest tests/test_calibration_ownership.py -v` | **P3** calibration `gl.*` + propriété mixer (`SKIPPED_SOLVER_OWNERSHIP`) + `scalar_master_fix` |
| `tests/test_p4_corridors_pq.py` | `.venv/bin/python -m unittest tests/test_p4_corridors_pq.py -v` | **P4** corridors externes `external_pro_reference` + proxy PQ < 10 ms + `MUTATION_RATIO_MAX` |
| `tests/test_session_bugs.py` | `.venv/bin/python -m unittest tests/test_session_bugs.py -v` | **RETEX** régression des 3 crashs de session + propagation `seed` `@meta` |

## 7. Installation des Dépendances Node

```bash
cd scripts && npm install --no-audit --no-fund    # @strudel/core, @strudel/mini, @strudel/tonal, @tonaljs/tonal (scripts/package.json)
```

`install.sh` tente cette installation automatiquement si `node` est présent (désactivable via `--skip-strudel`).
`scripts/strudel_loader.mjs` mappe `@kabelsalat/web` vers son bundle ESM pour rendre `@strudel/core`
importable en Node pur (son `main` CJS casse l'import ESM). `node_modules/` est gitignoré.

## 8. Façade MCP `ai-studio-prod` & économie maximale de tokens (Spec B)

En production v4.0, un **seul** serveur MCP est exposé à l'agent : `mcp-servers/ai-studio-prod/prod_server.py`
(enregistré par `install.sh` dans Antigravity et Zed), **exactement 3 outils**, schémas ~600 tokens au total :

| Outil | Rôle | Retour |
| :--- | :--- | :--- |
| `studio_prepare(project_dir, intent)` | Indexe les samples, propose palettes + grooves | tabulaire `CTX\|PALETTES\|GROOVES_AVAIL` <= 450 cars |
| `studio_compose(project_dir, strudel_code, palette_id="auto", auto_master=True)` | Écrit `composition.strudel`, évalue Node headless, compile `ScoreIR` + garde-fous 0 token, chaîne le master | résumé <= 400 cars + `handoff.txt` |
| `studio_refine(project_dir, delta_json, strudel_patch, export_mp3)` | Retouche stateless ; patch ciblé `{"replace_track": "<role>", "code": "..."}` | résumé <= 400 cars |

- **Écriture atomique + chaînage auto** : `studio_compose` enchaîne écriture → eval → compile → master
  (quand `gridline` est présent) en 1 seul appel LLM.
- **Escalade locale 0 token** (avant tout nouvel appel LLM) : `scripts/strudel_lint.mjs` renvoie <= 2 lignes
  `ERR L<ligne>:<col> <msg>. Fix: <hint>.` (exit 4 — jamais de stacktrace transmis au LLM) ; le prélude
  `scripts/strudel_prelude.mjs` installe `grid()` (1 symbole d'accord = 1 mesure + progression `__strudel_grid`),
  `track(role, pat)`, `deg(degrés, octave)` (degrés → noms de notes via key/mode), `glide(ms)` (legato),
  `arrange()` ; 4 paliers d'escalade incl. `AUTO_SWAP_SAMPLE` quand le solveur de mix détecte une source défectueuse.
- **Stateless via `handoff.txt`** (<= 350 tokens) : `studio_refine` relit le contexte sans recharger le projet.
- **Prompt caching** : préfixe 100 % statique (system + 3 schémas d'outils + runbook) — runbook dédié
  [AGENT_RUNBOOK_V4.md](AGENT_RUNBOOK_V4.md) (<= 750 tokens, exemple canonique 22 lignes, flux en 2 appels).
- **Budget vérifié en CI** : `tests/test_token_budget_v4.py` exige schemas + runbook + retour `studio_prepare`
  < **1 800 tokens** combinés.

## 9. Solveur de mix mathématique conjoint — `scripts/math_mix_solver.py` (0 token, < 0,8 s)

Après le **1er rendu stems**, plus aucune boucle d'auto-fix LLM : `solve_mix_parameters(stems_audio, tracks_meta,
mix_profile, emotion_vector, bpm, corridors_path, user_overrides)` résout en une passe les **gains, EQ, pan,
width, sends FX, sidechain et limiteur master** optimaux :

- DSP surrogate déterministe NumPy/SciPy : PSD sur K = 512 fréquences, projection 24 bandes de Bark (pondérée
  df), biquads RBJ analytiques vectorisés, corrélation L/R, modèle de perte mono `10·log10(1 + w²(1-c)/(1+c))`.
- Coût = corridor spectral (dead-zone) + masquage Bark (SMR) + bornes de hiérarchie + stéréo/mono + dynamique +
  parsimonie ; seuils SSOT depuis `config/mix_profiles.json` (via `mix_config.py`) et
  `golden_references/spectral_corridors.json`.
- 4 blocs couplés x 2 passes : (1) EQ L-BFGS-B, (2) gains `lsq_linear`, (3) pan/width/DRR en forme close
  (`least_squares`), (4) temporel `sosfilt` + sidechain + bissection du limiteur (<= 8 itérations).
- Sortie = dict §6.1 du spec + `saturated_tracks_for_swap` → escalade `AUTO_SWAP_SAMPLE` (0 token).
- Tests bloquants : `tests/test_math_mix_solver.py` (6 tests : contrat §6.1, conflit spectral sévère, perte
  L/R + mono, parsimonie ±0,3 dB, source défectueuse, CPU < 1,0 s sur 8 stems stéréo x 12 mesures).

## 10. Scénarios C (Remix) et D (Chant) dans la façade v4.1

- `studio_prepare(project_dir, intent, remix="<chemin>", lyrics={...})` : comme en v3.3, `remix`
  renvoie BPM/clé/grille du morceau source (Scénario C) et `lyrics` les syllabes/pauses (Scénario D).
- `studio_compose(..., remix_source_path="...", lyrics_spec={...})` : consigne `scenario: "C"|"D"` +
  la source/les paroles dans `song_blueprint.json` (généré automatiquement, valide schéma).
- Piste vocale dans `composition.strudel` : déclarez simplement `$vocals: ...` (1 piste = 1 rôle) ;
  le pont classe le rôle `vocals` et `song_builder` applique `vocal_arrangement_sync.py` (P6) dès
  que `.ustx`/`vocals.wav` est présent. Stems découpés d'un remix : pistes `$stem_<nom>` (rôle `remix_stem`).
