# Récapitulatif du Workflow — `rap-determine-rythme-planant`

Ce document récapitule l'ensemble des actions, outils, itérations et diagnostics parcourus pour produire le morceau dans `/home/gamo/Bureau/musicamical/projects/rap-determine-rythme-planant` (sur la demande : *« Génère une instrumentale de rap déterminée, rythmée, rapide et planante »*).

---

## 1. Lecture documentaire et préparation (0 rendu)

1. **Consultation de `DOCUMENTATION_IA/`** :
   - Lecture de `DOCUMENTATION_IA/README.md` et `DOCUMENTATION_IA/01_PRODUIRE_UN_MORCEAU_DE_QUALITE.md` (contrat Strudel v4.1, façade MCP `ai-studio-prod`, pre-flight MIDI gate, tables de degrés `b2-safe`).
   - Lecture des schémas MCP de `ai-studio-prod` (`studio_prepare`, `studio_compose`, `studio_refine`).
2. **Repérage de l'espace de travail** :
   - Vérification du dossier `projects/` et choix du nom de projet `projects/rap-determine-rythme-planant`.
   - Paramètres musicaux choisis : `152` BPM, `C:minor`, profil `trap_rap`, `V=0.0`, structure `I8-V16-C8-V16-C8-B8-C8-O8` (80 mesures), `seed=2026`.

---

## 2. Conception Strudel & itérations Pre-Flight / MIDI Gate (0 rendu)

### Itération 1 — Motif initial (`Cm7 Abmaj7 Ebmaj7 Bb7`)
- Création de `composition.strudel` avec `kit("trap_full", {var: 2, hats: "rolls"})`, `$chords`, `$bass`, `$lead`.
- Exécution de `node scripts/strudel_lint.mjs`, `scripts/strudel_to_score_ir.py --eval` et `scripts/preflight_strudel_gate.py`.
- **Résultat** : `preflight` **FAIL** (`gap_fill = 0.333 < 0.60`, `n_bad_leaps = 12`, `b2_fixed_by_bridge = 40`). Cause : certaines notes du motif `lead` provoquaient des frottements `b2` corrigés automatiquement par `fix_lead_b2_clashes`, ce qui cassait les enchaînements leap+fill.

### Itération 2 — Analyse des degrés sûrs (`--safe-degrees`) & adaptation par accord (`Cm Ab Eb Bb`)
- Vérification via `scripts/preflight_strudel_gate.py --safe-degrees` sur `Cm7 Abmaj7 Fm7 G7`, `Cm7 Fm7 Abmaj7 Bb7` et `Cm Ab Eb Bb`.
- Réécriture du motif `$lead` avec 4 sous-motifs adaptés aux degrés sûrs de chaque accord (`Cm`, `Ab`, `Eb`, `Bb`).
- **Résultat preflight** : **PASS** (`stepwise_ratio = 0.92`, `gap_fill = 1.0`, `rest_ratio = 0.335`, `contrary_or_oblique_ratio = 1.0`, `n_bad_leaps = 0`, `compile.passed = true`, `full_coverage.passed = true`).

### Itération 3 — Passage par `ai-studio-prod:studio_compose` et vérification `compile_song.py`
- Appel MCP `studio_compose(project_dir=..., palette_id="auto", auto_master=True, auto_prepare=True)`.
- Retour `studio_compose` : `OK tracks=7 notes=560 | eval=True fc=True | gate=PASS gf=1.0 sw=0.92 rest=0.336 | palette=palette_A samples=1 theme=organic`, mais `handoff.txt` indiquait `master=n/a (gridline absent ou auto_master off)`.
- Diagnostic : `/usr/bin/gridline` était bien présent, mais `compile_song.py` échouait en interne sur `verify_midi_gate` (`master_sketch.mid`) :
  - `melody_rest_ratio = 0.668` (hors `[0.2, 0.5]` sur la durée totale du morceau incluant intro/outro) ;
  - `minor_2nd_clashes = 7` (`['Chords', 'Bass']`, `['D#3', 'D2']`, `minor_9th_clash` à `t = 18.849 s` et toutes les 8 mesures).

### Itération 4 — Réduction des silences du `$lead` & tests sur `$bass`
- Réduction du nombre de silences `~` dans `$lead` et ré-appel de `studio_compose` (`notes=2834..2874`, `rest=0.282`).
- `melody_rest_ratio` est passé au vert, mais le clash `['Chords', 'Bass']` (`D#3` vs `D2` à `18.849 s`) persistait même avec `Cm Ab Fm Gm` et `glide: 0`.
- En passant `$bass` au motif `bassline("<1 ~ 1 ~>", {mode: "root", glide: 40}).slow(4).in("verse,chorus,outro")` avec `grid("Cm7 Abmaj7 Fm7 Gm7").slow(2)` (inspiré du projet de référence `rap-tres-rythme-mysterieux`), le **MIDI Gate de `compile_song.py` a réussi**.

---

## 3. Déblocage du rendu `.gridline` (correction paramètres plugins)

- Lors de `compile_song.py`, `build_gridline_project` a levé une erreur Gridline `set_parameters` sur `channel/1/cutoff_hz` (`no parameter "cutoff_hz" here`), car `song_blueprint.json` contenait des clés génériques (`cutoff_hz`, `attack_ms`, `release_ms`, `glide_ms`, `vibrato_delay_ms`) dans `plugin_params` pour `gl.warmsynth`, `gl.sampler` et `gl.fm`.
- Vidage de `plugin_params: {}` sur ces 3 pistes dans `song_blueprint.json` afin que `song_builder.py` applique uniquement les paramètres natifs Gridline issus des archétypes.
- Relance de `.venv/bin/python scripts/compile_song.py /home/gamo/Bureau/musicamical/projects/rap-determine-rythme-planant` :
  - **Succès** (`"ok": true`), `musical_safety` (`errors: 0`, `warnings: 1`), `full_coverage` (`passed: true`, `n_issues: 0`), génération de `projet.gridline`, `master_sketch.mid`, `score_ir.json` et `renders/gain_staging_report.json`.

---

## 4. Rendu audio & audit final (interrompu sur demande)

- Lancement de :
  `.venv/bin/python scripts/render_and_audit.py /home/gamo/Bureau/musicamical/projects/rap-determine-rythme-planant/projet.gridline -o /home/gamo/Bureau/musicamical/projects/rap-determine-rythme-planant/renders/master.wav --profile trap_rap --blueprint /home/gamo/Bureau/musicamical/projects/rap-determine-rythme-planant/song_blueprint.json --mp3 --auto-fix --max-passes 2`
- Le premier rendu `renders/master.wav` (~33,7 Mo) et l'audit CLAP ont été exécutés ; la passe `--auto-fix` était en cours lorsque l'arrêt immédiat a été demandé.
- La tâche en arrière-plan (`task-107`) a été annulée proprement avant l'export `.mp3` final.

---

## 5. État actuel des fichiers du projet

- `composition.strudel` : source Strudel finale (`152 BPM`, `C:minor`, `trap_rap`, `I8-V16-C8-V16-C8-B8-C8-O8`).
- `composition.strudel.haps.json`, `score_ir.json`, `arrangement_plan.json`, `handoff.txt` : artefacts du pont Strudel v4.
- `song_blueprint.json` : blueprint validé (`palette_A`, `gl.warmsynth`, `gl.sampler` sur `reese_a1_clean.wav`, `gl.fm`, kit percussions).
- `master_sketch.mid` : partition MIDI validée par `verify_midi_gate`.
- `projet.gridline` : projet Gridline compilé et prêt au rendu.
- `renders/master.wav`, `renders/master.wav.origin.json`, `renders/gain_staging_report.json`, `renders/diagnostics/` : rendu WAV de la passe 1 et rapports associés (`master.mp3` non généré suite à l'interruption demandée).
