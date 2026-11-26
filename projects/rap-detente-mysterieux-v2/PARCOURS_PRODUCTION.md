# Parcours de Production — `rap-detente-mysterieux-v2`

**Demande initiale** : *« Génère une instrumentale de rap rythmée avec une ambiance détente et mystérieuse. »*

---

## 1. Lecture de la documentation & préparation

1. Consultation de `DOCUMENTATION_IA/README.md`, `01_COMPOSER_AVEC_STRUDEL.md`, `02_PRODUIRE_LE_MORCEAU.md` et `03_SONS_AGRÉABLES.md`.
2. Vérification des schémas de la façade MCP `ai-studio-prod` (`studio_prepare`, `studio_compose`, `studio_refine`) et du répertoire `~/Music/AI-Studio`.
3. Choix du scénario **A — Création originale** via Strudel headless (`composition.strudel` → `ScoreIR` → `arrangement_plan.json` → `projet.gridline`) dans un nouveau dossier projet : `projects/rap-detente-mysterieux-v2/`.

---

## 2. Conception musicale (`composition.strudel`)

- **Tempo & tonalité** : `124 BPM`, `C:minor`, signature `4/4`, profil `trap_rap`, valence `V=0.0` (pour éviter le repli d'octave sur le lead), `seed=4242`.
- **Macro-forme** : `form=I4-V16-P4-C16-V16-P4-C16-B8-C8-O4` (96 mesures annoncées, soit ~3 min 07 s rendues).
- **Pistes (1 piste = 1 rôle atomique)** :
  - `kit("trap_half", {var: 2, hats: "rolls"})` → `$kick`, `$snare`, `$hihat`, `$perc` (rythmique rap/trap posée avec roulements de hats).
  - `$chords: grid("Cm7 Abmaj7 Fm7 G7").slow(2).gain(0.68).muteIn("outro")` → harmonie mineure détendue et mystérieuse.
  - `$pad: grid("Cm7 Abmaj7 Fm7 G7").slow(4).gain(0.45).in("intro,pre_chorus,bridge,outro")` → nappe atmosphérique sur les transitions et sections calmes.
  - `$bass: bassline("<1 ~ 1 ~>", {mode: "root", glide: 45}).slow(4).in("verse,pre_chorus,chorus,outro")` → basse glissée ancrée sur les fondamentales.
  - `$lead: deg("0 3 2 1 ~ 1 2 1 0 ~ 2 3 2 1 0 ~", 4).slow(2).gain(0.62).in("verse,chorus,bridge")` → motif mélodique fluide avec respiration et mouvement conjoint.

---

## 3. Itérations, incidents rencontrés & résolutions

### Étape A — Premier essai (`studio_compose`) et blocage au MIDI Gate
- **Premier essai de basse** : `$bass: bassline("<1 ~ 1 ~ 1 ~ 5 ~>", {mode: "root", glide: 45}).slow(4)`.
- **Résultat `studio_compose`** : `OK tracks=8 notes=366 | gate=PASS gf=1.0 sw=0.922 rest=0.207`, mais `master=n/a` dans `handoff.txt`.
- **Diagnostic** : en exécutant `scripts/compile_song.py projects/rap-detente-mysterieux-v2`, le compilateur a levé une erreur bloquante :
  - `MIDI GATE FAILED: minor_2nd_clashes=1 bloquants (0 autorisé)` entre `Chords` (`G#3`) et `Bass` (`G2`, `minor_9th_clash` à `t = 21.306 s`) dû au degré `5` de la ligne de basse frottant contre la sixte mineure (`Ab`) des accords.

### Étape B — Correction de la ligne de basse & compilation réussie
- **Correction** : simplification du motif de basse en `bassline("<1 ~ 1 ~>", {mode: "root", glide: 45}).slow(4)` dans `studio_compose(..., auto_prepare=True, auto_master=True)`.
- **Résultat** :
  - Le pont Strudel → `ScoreIR` → `arrangement_plan.json` → `projet.gridline` a **réussi** (MIDI Gate passé).
  - L'appel MCP `studio_compose` a atteint son timeout de 3 minutes pendant la phase de rendu/audit en arrière-plan (`render_and_audit.py`), mais la tâche a continué et généré `renders/master.wav` (49,45 Mo), `renders/master.mp3` (7,48 Mo) et `renders/diagnostics/s4_macro_structure.json`.

### Étape C — Audit audio du premier rendu
- **`s4_macro_structure.json`** : `score: 0.946`, `clones_8bar: PASS` (`max_similarity: 0.7898`), `cognitive_ceiling: PASS`, `novelty: FIX` (`0.839`, transition `chorus->bridge` à renforcer).
- **`audio-analyzer:analyze_mix` sur `renders/master.wav`** :
  - Durée : `186.886 s` (~3 min 07 s)
  - Loudness intégré : `-20.01 LUFS` (en dessous d'une cible rap/trap à `-11 LUFS`)
  - True Peak : `+0.19 dBTP` (`0` sample clippé, mais au-dessus du plafond `-1.0 dBTP`)
  - Bas-médiums (250–500 Hz) : `37.66 %` (un peu chargés)
  - Stéréo : très centrée (`stereo_width_ratio: 0.019`, `mono_compatible: true`).

### Étape D — Passe d'auto-fix interrompue sur demande utilisateur
- Lancement de `scripts/render_and_audit.py ... --profile trap_rap --blueprint song_blueprint.json --mp3 --auto-fix --max-passes 2` pour corriger automatiquement le niveau LUFS et le plafond du limiteur.
- La passe 1 d'audit s'est terminée, l'auto-fix a ajusté le limiteur (`ceiling_db=-1.60 dB`), et la passe 2 a réécrit `renders/master.wav` avant que la tâche ne soit arrêtée immédiatement à ta demande.

---

## 4. État actuel des fichiers du projet

- `composition.strudel` — Source Strudel v4.1 de la composition
- `song_blueprint.json` — Blueprint validé (profil `trap_rap`, 8 pistes)
- `score_ir.json` & `arrangement_plan.json` — Représentation intermédiaire et plan d'arrangement générés
- `master_sketch.mid` — Esquisse MIDI validée
- `projet.gridline` — Projet Gridline compilé (mis à jour lors de la passe d'auto-fix)
- `renders/master.wav` — Rendu WAV (~49,4 Mo, réécrit pendant la passe 2 avant interruption)
- `renders/master.mp3` — Rendu MP3 (~7,48 Mo, issu du premier rendu complet)
- `renders/gain_staging_report.json` — Rapport de gain staging initial
- `renders/diagnostics/enrichment_report.json` & `s4_macro_structure.json` — Diagnostics d'enrichissement et de macro-structure
- `handoff.txt` — Résumé compact du pipeline v4
