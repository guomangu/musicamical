# 03 — Pourquoi le morceau sonne désagréable (rythme bon, sons mauvais) et la solution

Symptôme rapporté : le morceau est rythmiquement correct mais **désagréable à l'oreille** — les sons
choisis sont durs, grinçants ou se masquent entre eux.

## Diagnostic (4 causes racines)

1. **Confort « mou » seul** : `compute_comfort_score` était un score souple — un sample dur/grinçant
   pouvait encore gagner sur le fit de rôle. Aucun **plafond dur** sur la dureté.
2. **Aucune dissonance sensorielle par paire** : deux timbres proches en fréquence critique
   (rugosité Plomp-Levelt) ou qui se chevauchent dans les mêmes bandes de Bark graves se masquent
   (boue dans les bas-médiums) — jamais mesurée dans une palette.
3. **Carte de plugins statique** : la façade `palette_id="auto"` utilisait une table `ROLE_PLUGIN`
   fixe, ignorant le profil de mix et la valence (les archetypes calibrés `gl_dsl.resolve_archetype`
   n'étaient pas appliqués).
4. **La façade ne cherchait pas dans `samples/`** : `palette_id="auto"` ne proposait que des
   synthés `gl.*`, même quand des samples propres étaient indexés ; et les samples posés à la
   racine de `samples/` restaient physiquement non classés.
5. **Serveur MCP périmé après `git pull`** : le processus serveur en mémoire garde l'ancien code
   → `palette: null`, pistes 100 % `gl.*`, aucune ligne `palette=`. Correctif : `studio_compose`
   détecte que `prod_server.py` a changé depuis son démarrage et affiche `RESTART session agent`
   (retour + `handoff.txt`) → fermer/rouvrir la session agent. `install.sh` fait aussi un
   `git pull --ff-only` et rappelle de redémarrer.
6. **Chemins de samples fantômes** (DB indexée depuis une ancienne racine, ex. `.../musicos/...`) :
   `indexer.reroute_sample_path` re-reroute sous `<racine>/samples/...` si le fichier existe, sinon
   le sample est exclu (jamais branché).
7. **`root_note` des percussions absurde** (pitch détecté d'un one-shot, ex. rimshot à 84 → chaque
   hit pitch-shifté de −40 st = inaudible) : désormais `root_note` = **hauteur notée du kit**
   (kick=36, snare=38, hihat=42, lu dans `score_ir.json`, repli GM).
9. **Lookup blueprint par rôle seul au rendu** : les 4 pistes drums partagent le rôle `drums`, donc
   `song_builder._default_channels_for_plan` ne retrouvait jamais la piste `$kick`/`$snare` → le sample
   branché dans le blueprint n'atteignait PAS le canal (rendu en `gl.kick`/`gl.snare` malgré tout).
   Correctif : indexation du blueprint par rôle **et par nom** de piste. Lié : les `plugin_params`
   de confort (`pick_gl_plugin` : cutoff ≤ 9 kHz, attaque ≥ 2 ms…) étaient perdus (seul le nom du
   plugin était stocké) → désormais stockés dans le blueprint et appliqués au canal (`cutoff_hz`).
8. **Volumes inégaux** : sans métriques soundcheck, le gain staging applique un calibrage nominal
   **absolu** par rôle (ancre = kick ; chaque piste réglée à `ancre × 10^(offset/40)`, ex. hihat −10 dB,
   pad −8 dB) au lieu d'un décalage borné à ±6 dB qui ne suffisait pas ; le rapport affiche enfin le
   vrai `volume_before` et le statut `NOMINAL_APPLIED`.

**Preuves concrètes sur un morceau généré** (blueprint + handoff fournis) : les 8 pistes étaient
toutes `source_type: "gridline_native"` (`gl.keys`, `gl.fingerbass`, `gl.warmsynth`, `gl.pluck`,
`gl.kick`…) avec **aucune clé `palette`** — la résolution de palette n'avait jamais branché de
sample ; et le log d'indexation montrait `q2_pass: 0, q2_rejected: 0` sur 7 324 rows DSP : la
table `sample_quality_v2` n'était remplie que pour les nouveaux fichiers, jamais en incrémental.

## La solution désormais câblée (0 token pour l'agent)

`scripts/timbre_pleasantness.py` fournit le **gate d'agréabilité** :

- `pleasantness_gate(metrics, role)` → verdict dur **PASS / FIX / SWAP** + score + raisons.
  Plafonds durs par rôle (`PLEASANTNESS_CAPS`) : `r_harsh`, `roughness` (Plomp-Levelt), `peak_harsh_db`,
  plancher `comfort_min` ; plus pour les rôles tonaux : centroïde max (lead 6,5 kHz, chords 5,5 kHz,
  pad 4,5 kHz, basse 900 Hz) et crest max (lead 14 dB, chords 13 dB, pad 12 dB). SWAP si un plafond
  est dépassé, FIX si on en approche (> 80 %), PASS sinon. Les cymbales (hihat) sont tolérées
  (plafonds hauts).
- `plomp_levelt_roughness(freqs, mags)` : rugosité sensorielle d'un spectre (paires de partiels
  dans une bande critique, courbe de dissonance Zwicker).
- `interval_consonance(f1, f2)` : octave > quinte > … > triton (~0,05) — le triton est le pire.
- `palette_timbre_coherence(items)` : collision Bark24 pondérée graves + rugosité par paire de
  centroïdes médiums → score de cohérence de la palette entière.
- `pick_gl_plugin(role, mix_profile, valence)` : plugin `gl.*` agréable via les archetypes
  (`resolve_archetype`, dark si valence < −0,2) avec garde-fous de confort : attaque ≥ 2 ms,
  release ≥ 60 ms, cutoff ≤ 9 kHz sur les rôles tonaux.

**Câblage (tout est automatique) :**

| Où | Effet |
|---|---|
| `palette_builder.propose_palettes` | candidats SWAP filtrés, confort réel des synthés `gl.*`, pénalité de cohérence `−0,2×(1−score)`, champ `timbre_coherence` en sortie |
| `select_sample.py` | les samples SWAP sont exclus de la sélection |
| `prod_server._resolve_palette` (systématique, **tout** `palette_id`) | interroge `samples.db` global + `Samples/`/`samples/` du projet et branche les meilleurs samples RÉELS sur les pistes (`source_type="sample"`, `sample_path`, `gl.sampler`, `root_note`, `fine_tune_ct`) ; **préfère la palette la plus riche en samples réels** (un morceau doit utiliser les sons fournis, pas retomber sur 100 % `gl.*`) ; repli `gl.*` par archetype sinon. Le retour et `handoff.txt` affichent `palette=<id> samples=N theme=…` (observabilité) |
| `prod_server._ensure_blueprint` | plugin `gl.*` par archetype calibré pour **toutes** les pistes (percussions → repli direct `gl.kick`/`gl.snare`/`gl.hat`, jamais un archetype basse) |
| `pick_gl_plugin` | garde-fous de confort **forcés** sur les rôles tonaux : attaque ≥ 2 ms, release ≥ 60 ms, cutoff ≤ 9 kHz même si l'archetype fournit des valeurs plus sèches/aiguës (son criard) |
| `indexer.populate_dsp_metrics` | **backfill v2** : les rows déjà analysées en DSP mais sans profil `sample_quality_v2` sont ré-analysées en incrémental (`q2_pass` réel, exclusion REJECTED effective) |
| `indexer.reroute_sample_path` + `palette_builder` | chemins stale re-rootés, fichiers absents exclus, **`sample_quality_v2` REJECTED exclus dès la proposition de palettes** |
| `sample_quality_v2` | clamp dur `n_fft <= len(mono)` : plus de `UserWarning librosa n_fft too large` |
| `gain_staging_lib.apply_nominal_balance` + `song_builder` | calibrage nominal **absolu** par rôle (volumes égaux sans mesure) + rapport `volume_before`/`NOMINAL_APPLIED` correct |
| `prod_server._ensure_blueprint` | plugin `gl.*` choisi par `pick_gl_plugin` (plus de table statique) |
| `sample_quality_v2` (indexation) | samples REJECTED (clipping TP×4, faux WAV, SNR, DC) exclus d'emblée — voir [docs/08](../docs/08_AUDIO_INTELLIGENCE_V2.md) |
| `scripts/organize_samples.py --move` | classe physiquement les samples de la racine de `samples/` dans leur sous-dossier (`808/`, `kick/`, …) sans jamais écraser, puis relancer `index_samples.py` |

## Consignes pour l'IA qui compose

1. **Ne touche pas aux timbres** : laisse `palette_id="auto"` (défaut). La recherche de samples et
   le gate d'agréabilité sont automatiques. Ne force un plugin `gl.*` que via un archetype adapté
   au profil (jamais un son dur pour lead/chords/pad).
2. **Vérifie la ligne `palette=… samples=N`** dans le retour de `studio_compose` et dans
   `handoff.txt` : si `palette=n/a` ou `samples=0` → soit le serveur MCP tourne un vieux code (voir
   `RESTART session agent` → redémarrer la session agent après chaque `git pull`/`install.sh`), soit
   relancer l'indexation
   (`python scripts/index_samples.py` — backfill v2 inclus), ou déposer les samples dans
   `<projet>/Samples/` classés par dossier.
3. Évite les intervalles de **triton** entre lead et basse/accompagnement dans `deg(...)` —
   privilégie {0, 3, 6, 7} (voir [01 §tables b2-safe](01_COMPOSER_AVEC_STRUDEL.md)).
4. Si tu fournis tes propres samples : mets-les dans `<projet>/Samples/` **classés par dossier**
   (`python scripts/organize_samples.py --move` puis réindexe), et passe par `studio_prepare`
   (scénario B) pour verrouiller la palette.
5. Vérification rapide d'un son en 0 rendu :
   `python scripts/timbre_pleasantness.py --role lead_melody --metrics '{"r_harsh":0.6,"roughness":0.3}'`
   → SWAP = à remplacer ; `--plugin lead trap_rap -0.6` → plugin `gl.*` conseillé.

Règle d'or : **un son SWAP ne doit jamais entrer dans le morceau** — le gate le fait tout seul ;
ne le contourne pas.
