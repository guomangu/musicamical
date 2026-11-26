# Parcours de production — `trap_planant_energique`

Ce document retrace l'ensemble des étapes, outils, erreurs rencontrées, diagnostics et corrections effectués pour produire l'instrumental **trap lente, planante et énergique** dans [`projects/trap_planant_energique/`](file:///home/gamo/Bureau/musicamical/projects/trap_planant_energique/).

---

## 1. Contexte & Conception musicale

- **Demande** : instrumental de rap trap lente, planante et énergique.
- **Documentation consultée** :
  - [`DOCUMENTATION_IA/README.md`](file:///home/gamo/Bureau/musicamical/DOCUMENTATION_IA/README.md)
  - [`DOCUMENTATION_IA/01_PRODUIRE_UN_MORCEAU_DE_QUALITE.md`](file:///home/gamo/Bureau/musicamical/DOCUMENTATION_IA/01_PRODUIRE_UN_MORCEAU_DE_QUALITE.md)
  - Schémas MCP `ai-studio-prod` (`studio_prepare`, `studio_compose`, `studio_refine`).
- **Paramètres `@meta` retenus** dans [`composition.strudel`](file:///home/gamo/Bureau/musicamical/projects/trap_planant_energique/composition.strudel) :
  - **Tempo** : `116` BPM (ressenti half-time trap lent à 58 BPM).
  - **Tonalité / Mode** : `C:minor`.
  - **Profil de mix** : `trap_rap`.
  - **Valence** : `V=0.0` (pour éviter le pliage d'octaves du lead qui casse le `gap_fill`).
  - **Forme** : `I4-V8-P4-C8-V8-P4-C8-O4` (48 mesures, 8 sections : `intro`, `verse`, `pre_chorus`, `chorus`, `verse`, `pre_chorus`, `chorus`, `outro`).
  - **Seed** : `2026`, signature `4/4`.

---

## 2. Itération 1 — Validation Strudel & Pre-flight (0 rendu)

1. **Premier jet** : `kit("trap_half", {var: 2, hats: "rolls"})` + `$chords: grid("Cm7 Abmaj7 Ebmaj7 Bb7").slow(2)` + `$pad` + `$bass` + `$lead` + `$arp`.
2. **Test via `strudel_lint.mjs`, `strudel_to_score_ir.py --eval` et `preflight_strudel_gate.py`** :
   - **Échec** (`passed: false`) : `gap_fill = 0.333` (seuil `>= 0.60`), `n_bad_leaps = 12`, et `b2_fixed_by_bridge = 40` notes réécrites sur `Ebmaj7`/`Bb7`.
3. **Correction** : passage à la progression `Cm7 Abmaj7 Fm7 G7` avec le motif lead `0 3 2 1 ~ 1 2 1 0 ~ 2 3 2 1 0 ~`.
   - **Résultat pre-flight** : `passed: true` (`stepwise_ratio = 0.922`, `gap_fill = 1.0`, `rest_ratio = 0.205`, `contrary_or_oblique_ratio = 0.958`, `b2_clashes_remaining = 0`, `n_bad_leaps = 0`).

---

## 3. Itération 2 — Appel MCP `ai-studio-prod:studio_compose` & détection d'un serveur MCP périmé

1. **Appel MCP `studio_compose(auto_prepare=True, auto_master=True, palette_id="auto")`** :
   - Retour : `palette=palette_A samples=7/7 theme=organic`, mais avec l'avertissement `!! RESTART session agent (prod_server.py modifié depuis le démarrage du serveur MCP)` et `master=n/a (gridline absent ou auto_master off)` dans [`handoff.txt`](file:///home/gamo/Bureau/musicamical/projects/trap_planant_energique/handoff.txt).
2. **Tentative de compilation via `scripts/compile_song.py`** :
   - **Blocage `blueprint_safety` (3 erreurs)** :
     - `chords` pointait vers `/home/gamo/Bureau/musicamical/samples/fx/sxj_fx4_tuned.wav` (`inharmonic_bell_confined` + `max_semitone_shift_exceeded`, `s_max_st=47 > 7`).
     - `bass` pointait vers `/home/gamo/Bureau/musicamical/samples/808/energized_808_clean.wav` (`root_note: 24`, `max_semitone_shift_exceeded`, `s_max_st=20 > 9`).
   - **Cause** : le processus serveur MCP en mémoire exécutait une ancienne version de `prod_server.py` (antérieure aux garde-fous `sample_fit_guard.py` v4.2).

---

## 4. Itération 3 — Exécution directe du `prod_server.py` à jour & débogage du MIDI Gate

Pour utiliser le code à jour sur disque sans redémarrer la session, `prod_server.studio_compose` a été appelé directement via `.venv/bin/python`, suivi de `scripts/compile_song.py` :

1. **Sélection de palette corrigée** : `safety=0err/0warn`, `guard=OK`, `palette=palette_B samples=4/4 rej=41 theme=synthetic`.
2. **Échecs successifs au `verify_midi_gate` sur `master_sketch.mid`** :
   - **Essai A** (avec `$pad` + `$arp`) : `gap_fill_compliance=0.308 < 0.6` et `minor_2nd_clashes=4` (`G#3` vs `G3` entre `Lead` et `Chords`), mais `voice_leading_avg_semitones` corrigé.
   - **Essai B** (7 pistes atomiques : `kit` + `$chords` + `$bass` + `$lead`, avec `grid(...).slow(2)`) : `gap_fill` et `voice_leading` passés, mais `minor_2nd_clashes=6` (`Chords` vs `Bass` : `D#3/D2` et `G#3/G2`).
   - **Diagnostic dans [`score_ir.json`](file:///home/gamo/Bureau/musicamical/projects/trap_planant_energique/score_ir.json)** : `grid("Cm7 Abmaj7 Fm7 G7").slow(2)` étirait chaque accord sur `8.0` beats (2 mesures), alors que la `harmonic_grid` qui pilote `bassline(...)` changeait toutes les `4.0` beats (1 mesure), désynchronisant accords et basse.
3. **Correction finale de `composition.strudel`** :
   - Suppression de `.slow(2)` sur `grid("Cm7 Abmaj7 Fm7 G7")` pour verrouiller 1 accord = 1 mesure.
   - **Résultat** :
     - `studio_compose` : `OK tracks=7 notes=1181 | eval=True fc=True | gate=PASS gf=1.0 sw=0.923 rest=0.21 | safety=0err/0warn | guard=OK | master=renders/master.wav`.
     - `compile_song.py` : `ok: true`, `musical_safety: 0 errors / 0 warnings`, `full_coverage.passed: true`, `outro_resolved: true`, création de [`projet.gridline`](file:///home/gamo/Bureau/musicamical/projects/trap_planant_energique/projet.gridline).

---

## 5. Itération 4 — Rendu & Audit Quality Gate (`render_and_audit.py`)

Commande exécutée :
`.venv/bin/python scripts/render_and_audit.py projects/trap_planant_energique/projet.gridline -o projects/trap_planant_energique/renders/master.wav --profile trap_rap --blueprint projects/trap_planant_energique/song_blueprint.json --mp3 --auto-fix --max-passes 2`

Résultats observés dans [`renders/autofix_history.json`](file:///home/gamo/Bureau/musicamical/projects/trap_planant_energique/renders/autofix_history.json) :

- **Pass 0 (`initial`)** :
  - `integrated_lufs`: `-9.73` (cible `-11.0`, tol `1.2`) · `true_peak_dbtp`: `-1.15` · `clipped_samples`: `0`.
  - Échecs : `lufs`, `stereo_width=-17.73dB`, `resonances=[315.0]`, `low_mids=35.0%`, `low_end_share=52.8%`, `air=0.05%`.
  - Mutations auto-fix appliquées : Master gain `5.5 -> 4.23 dB`, notch `-7.11 dB @ 315 Hz` sur `mixer/19`, HPF `135 Hz` + dip `-3.0 dB @ 280 Hz` sur `mixer/19`, élargissement stéréo (`HiHat`, `Chords`, `BUS Music`).
- **Pass 1 (`after_fix_1`)** :
  - `integrated_lufs`: `-10.57` (PASS) · `resonances`: `[]` (PASS) · `true_peak_dbtp`: `-1.15` (`0` clip) · `low_end_corr`: `0.999`.
  - `macro_structure` : `PASS` (`score: 1.0`, `clones_8bar: PASS`, `novelty: PASS`, `arc_5d: PASS` avec `lufs_span_db: 10.58`).
  - **Échecs restants du `quality_gate`** (`passed: false`) :
    - `stereo_width=-16.55dB < -14.0dB`
    - `low_mids=28.05% > 24.0%`
    - `low_end_share=62.3% > 42.0%`
    - `air=0.06% < 3.0%`

---

## 6. Itération 5 — Ajustement du mix & arrêt demandé

- **Diagnostic** : dans [`renders/gain_staging_report.json`](file:///home/gamo/Bureau/musicamical/projects/trap_planant_energique/renders/gain_staging_report.json), `Bass` (`0.7175`) dominait `Chords` (`0.4795`) et `HiHat` (`0.4274`, `-3.41 dB`), accentué par les coupes de l'auto-fix sur `BUS Music` (`135 Hz` HPF, `-3 dB @ 280 Hz`, `-7.11 dB @ 315 Hz`).
- **Action appliquée sur `projet.gridline`** : ajustement des volumes mixeur (`Kick` `0.68`, `HiHat` `1.25` + `pan=0.25`, `Chords` `1.05`, `Lead` `1.08`, `BUS Bass` `0.52`, `BUS Music` `1.15`).
- **Relance & arrêt** : `render_and_audit.py` a été relancé (`task-90`), puis annulé immédiatement à la demande de l'utilisateur avant la fin du nouveau rendu.

---

## 7. État actuel des artefacts du projet

- Partition Strudel : [`composition.strudel`](file:///home/gamo/Bureau/musicamical/projects/trap_planant_energique/composition.strudel)
- Blueprint & IR : [`song_blueprint.json`](file:///home/gamo/Bureau/musicamical/projects/trap_planant_energique/song_blueprint.json), [`score_ir.json`](file:///home/gamo/Bureau/musicamical/projects/trap_planant_energique/score_ir.json), [`arrangement_plan.json`](file:///home/gamo/Bureau/musicamical/projects/trap_planant_energique/arrangement_plan.json), [`handoff.txt`](file:///home/gamo/Bureau/musicamical/projects/trap_planant_energique/handoff.txt)
- Projet Gridline compilé (avec les volumes mixeur ajustés) : [`projet.gridline`](file:///home/gamo/Bureau/musicamical/projects/trap_planant_energique/projet.gridline)
- Rendus audio existants (issus des passes précédentes) : [`renders/master.wav`](file:///home/gamo/Bureau/musicamical/projects/trap_planant_energique/renders/master.wav), [`renders/master.mp3`](file:///home/gamo/Bureau/musicamical/projects/trap_planant_energique/renders/master.mp3)
- Rapports d'audit : [`renders/autofix_history.json`](file:///home/gamo/Bureau/musicamical/projects/trap_planant_energique/renders/autofix_history.json), [`renders/gain_staging_report.json`](file:///home/gamo/Bureau/musicamical/projects/trap_planant_energique/renders/gain_staging_report.json)
