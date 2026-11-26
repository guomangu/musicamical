# Analyse des failles « sonorités désagréables » — constats, causes racines et corrections

> **Périmètre** : analyse en lecture seule du projet `projects/rap-3min` et du workflow qui l'a produit
> (`prod_server` → `palette_builder` → `blueprint_safety` → `compile_song`). **Aucun code du dépôt n'a été modifié.**
> Les correctifs ci-dessous sont des spécifications prêtes à implémenter.
> Symptôme utilisateur : rythme et composition bons, mais timbres « électriques / électroniques ».

---

## 1. Constat : ce que contient réellement `rap-3min`

Pistes du `song_blueprint.json` généré, croisées avec `samples_catalog.json` et `score_ir.json` :

| Piste | Sample choisi | Dossier / nature détectée | Notes jouées | `root_note` | Décalage max | Problème |
|---|---|---|---|---|---|---|
| chords | `fx/sxj_fx4_tuned.wav` | `fx` / `inharmonic_bell_or_fx` | 50–72 | **24** (C1) | **+48 st** (cap 7) | FX/cloche joué en accords, transposé de 3 à 4 octaves → timbre « chipmunk » électronique |
| bass | `kick/CLAUDE - kick04.wav` | `kick` / `mono_oneshot` | 35–41 | 31 | +10 st (cap 7) | un **kick** sert de basse (aucun 808 jouable disponible, cf. F2) |
| lead | `other/steel_stab_d5_clean.wav` | `other` / `mono_oneshot` | 59–65 | 62 | 3 st | « stab » acier, métallique mais dans le registre |
| kick | `perc/oddlogic…marker_#1…` | `perc` / `melodic_sequence` | 36 | 36 | — | n'est pas un kick (dossier `perc`, séquence mélodique) |
| snare | `snare/dnb_snare_crisp-2.wav` | `snare` / `melodic_sequence` | 38 | 38 | — | acceptable (rôle ok) |
| hihat | `hat/MZ Hi Hat [Fast]-2.wav` | `hat` | 42 | 42 | — | ok |
| perc | **le même fichier que hihat** | `hat` | 39 | 39 | — | deux pistes = un seul son (viole « 1 sonorité = 1 piste ») |

Le gate a pourtant affiché `preflight=PASS`, `compile_passed=True`, `fallback_gate=PASS`, `samples=7/7`, **sans aucune alerte**.
Rejouer `summarize_musical_safety(bp, catalogue, notes)` à la main donne **6 erreurs** :
`max_semitone_shift_exceeded` (chords 48 st, bass 10 st) et `root_note_mismatch` (×4 sur les drums, faux positifs, cf. F7).
**Le garde-fou existe ; il n'est simplement jamais alimenté.**

---

## 2. Failles, causes racines et solutions

Légende gravité : 🔴 cause directe du mauvais son · 🟠 aggravant · 🟡 hygiène/fiabilité.

### F1 🔴 Le gate de registre (`S_max`) ne reçoit jamais les notes ni le catalogue

- **Preuve** : `song_builder.py:151`, `loop_first_runner.py:344`, `render_and_audit.py:348` appellent
  `summarize_musical_safety(bp, catalog_path=…)` **sans `notes_by_track`** ; le catalogue cherché est
  `<projet>/samples_catalog.json`, or il vit à la **racine du dépôt** (`/samples_catalog.json`), jamais dans `projects/rap-3min/`.
  Résultat : seuls les contrôles structurels tournent (`sample_path`/`root_note` présents). `compile_song.py:20-24` bloque bien
  sur `errors>0`, mais `errors` vaut toujours 0. La docstring de `compile_song` (« S_max dépassé ») est donc **fausse en pratique**.
- **Solution** :
  1. Dans `song_builder.load_blueprint`, `render_and_audit` et `loop_first_runner`, charger `score_ir.json`
     (`notes_by_track = {nom: [pitch…]}`) et le passer à `summarize_musical_safety`.
  2. Résoudre le catalogue par cascade : `<projet>/samples_catalog.json` → `blueprint["palette"]["project_samples_dir"]/../samples_catalog.json`
     → `<racine>/samples_catalog.json`. Écrire le chemin retenu dans le rapport (`musical_safety.catalog`).
  3. Si le catalogue est introuvable, **échouer en warning visible** (`musical_safety.catalog_missing`) et non en silence.
- **Test de non-régression** : blueprint avec FX root 24 + notes 50–72 ⇒ `errors ≥ 1`, `compile_song` retourne `ok=False, stage=blueprint_safety`.

### F2 🔴 Détection d'accord erronée : 34/48 vrais 808 classés `poly_chord_or_dyad` → injouables

- **Preuve** : `samples_catalog.json` — dossier `808/` : 48 samples, **6 jouables**, 34 `poly_chord_or_dyad`. Pool `bass_sub` = 10 candidats dont
  des kicks et des reese. Conséquence directe : la basse retombe sur un kick.
- **Cause** : `audio_metrics.py:563-591` (test « ≥ 2 pics graves mutuellement non harmoniques »). Un 808 saturé a des pics à `2·f0` et `3·f0`
  (ex. 66 Hz et 99 Hz) ; le test compare les pics **entre eux** : `99/66 = 1,5` → `round(1,5)=2` → écart de ~500 ct → « non harmonique » → accord.
  Tout sub riche en harmoniques est donc déclaré accord.
- **Solution (testée en lecture seule)** : exempter les paires dont les deux pics sont des partiels bas-rang (k ≤ 8, ±45 ct) du `med_f0` mesuré.

  ```python
  def _is_f0_partial(freq):                      # dans analyze_pitch_and_purity, bloc "Low-register chord evidence"
      k = round(freq / med_f0)
      return 1 <= k <= 8 and abs(1200*math.log2(freq/(k*med_f0))) <= 45.0
  def _clash(a, b):
      if _is_f0_partial(a) and _is_f0_partial(b):
          return False                           # même fondamental = consonance native
      r = max(a, b)/max(min(a, b), 1e-6)
      return abs(1200*math.log2(r/max(1, round(r)))) > 50.0
  chord_peak_count = sum(1 for i in range(len(lp))
                         if any(_clash(lp[i], lp[j]) for j in range(len(lp)) if i != j)) if len(lp) >= 2 else 0
  ```
  (k ≤ 8 : au-delà, la grille `k·f0` d'un f0 grave est si dense qu'elle « expliquerait » n'importe quel pic.)
- **Mesure** : ré-analyse des 28 samples uniques de `samples/808/` avec ce correctif → **20 `mono_oneshot` jouables** (contre 2 avant ; ex. `808_sub_c1`, `deep_808_clean`, `sxj_808_4/5`), les vrais accords/FX (`Cowbell`, `Crash`, `Rim`, `Cool Kix Kick`) restent correctement exclus.
- **À faire après correctif** : ré-indexer (`scripts/index_project_samples.py` pour `samples_catalog.json`, `index_samples.py` pour `samples.db`, qui contient 7 324 lignes issues de la même classification) et ajouter un test sur 2 fixtures (808 saturé vs accord Cm).

### F3 🔴 La palette ignore le registre réellement joué

- **Preuve** : `_resolve_palette` (`prod_server.py:374-522`) choisit les samples **avant** toute comparaison avec `score_ir.json` ; `score_ir` n'est lu que
  pour la hauteur *notée* des drums (l.445-456). Un sample tonal à `root_note=24` peut donc être affecté à des accords joués en 50–72.
  `palette_builder._load_project_candidates` filtre la tonalité (`key_fit_penalty`) mais pas la **distance de transposition**.
- **Solution** :
  1. `propose_palettes(..., note_ranges={"chord_keys":[50,72], "bass_sub":[35,41], "lead_melody":[59,65]})` : pour les rôles tonaux, écarter tout
     candidat sample dont `max(|lo-root|, |hi-root|)` dépasse le cap de `blueprint_safety.SAMPLE_SHIFT_CAP_ST` (5 acoustique / 7 synthé-808, **même métrique que le gate de rendu**).
     Les synthés `gl.*` du pool restent éligibles : ils deviennent le repli naturel.
  2. `prod_server` calcule `note_ranges` depuis `score_ir.json` (union des pistes par rôle de slot : chords→`chord_keys`, bass→`bass_sub`, lead/counter→`lead_melody`).
  3. Optionnel (v2) : brancher `sample_prep_lib.build_multizone_samples` — **définie mais appelée nulle part** (`grep` : 0 appel) — quand l'ambitus ≤ 18/20 st.

### F4 🔴 Le sélecteur « mode projet » ignore le dossier d'origine et la nature du sample

- **Preuve** : `_load_project_candidates` (`palette_builder.py:362-482`) n'utilise **jamais** `category` ; l'éligibilité est
  `role in compatible_roles or role_score ≥ seuil` avec `relax` (seuil 0,18). `_ROLE_CATEGORY_FILTER` (l.485) n'existe que dans le chemin SQLite, inactif ici car
  `project_samples_dir` pointe sur `<racine>/samples` → mode `project` (trace : `mode: "project"`, 422 samples). D'où : un kick pris dans `perc/`, un FX pris pour des accords, un kick pour la basse.
  Aucune règle ne dit « une cloche inharmonique (`inharmonic_bell_or_fx`) ne porte pas d'accords » ; `blueprint_safety` ne la met qu'en *warning* (`inharmonic_bell_confined`), même pour `chords`.
- **Solution** : politique de timbre par rôle (0 token, < 1 ms), appliquée dans `propose_palettes` avant le glouton :

  | Rôle | Préféré (+0,30) | Déconseillé (−0,25) | **Interdit (exclu)** |
  |---|---|---|---|
  | kick | `kick` | `808`, `other` | `fx vocal loop piano guitar synth bass snare clap hat perc sfz` |
  | snare_clap | `snare clap` | `perc other` | `fx vocal loop tonal kick 808 hat` |
  | hihat_perc | `hat open_hat perc` | `other` | `fx vocal loop tonal kick 808 snare clap` |
  | bass_sub | `808 bass` | `kick synth other piano guitar` | `fx vocal loop hat snare clap perc` |
  | chord_keys | `piano guitar` | `vocal bass 808` | `fx loop` + toute percussion |
  | pad_atmosphere | `pad synth piano` | `fx bass 808` | `loop` + toute percussion |
  | lead_melody | `piano guitar synth` | `bass 808 other` | `fx loop` + toute percussion |

  Règles de nature : `inharmonic_bell_or_fx` (ou `inharmonicity > 0,15`) **exclu** en `chord_keys`/`pad_atmosphere`/`bass_sub`, malus −0,30 en `lead_melody` (et confinement degrés {1,5,8} déjà documenté).
  Malus −0,20 sur les noms contenant `fm glitch laser robot zap digital bitcrush chiptune metallic stab…` pour les rôles tonaux.
  Propager `category`, `sample_nature`, `inharmonicity` dans les candidats (les deux chargeurs) et dans les `slots`.
  Un rôle sans candidat sain **bascule sur le synthé `gl.*` calibré** (`pick_gl_plugin`) au lieu d'un sample hors-rôle.

### F5 🔴 Le « gate d'agréabilité » est vacuous en mode projet

- **Preuve** : `pleasantness_gate` (`timbre_pleasantness.py:113-150`) traite les métriques manquantes comme `0 (neutre)`. Or les candidats construits par
  `_load_project_candidates` (l.448-474) **ne copient ni `r_harsh`, ni `roughness`, ni `peak_harsh_db`, ni `crest_factor_db`** (le catalogue contient pourtant `r_harsh`).
  Le gate reçoit donc `None` partout et rend PASS. Il ne voit pas non plus `sample_nature`/`category`. Le FX (comfort 0,74, r_harsh 0,016) passe tous les plafonds.
- **Solution** :
  1. Copier `r_harsh`, `crest_factor_db` (et `roughness`/`peak_harsh_db` si présents) du catalogue dans le candidat ; ajouter ces deux champs à l'indexeur projet si absents.
  2. Dans `pleasantness_gate`, **métrique manquante sur rôle tonal = FIX**, pas PASS ; ajouter la clé `sample_nature`/`category` et appeler la politique F4.
  3. Compter `gate_unmeasured` dans `selection_trace` pour que l'absence de mesure soit visible.

### F6 🟠 Un même fichier peut servir deux pistes (perc == hihat)

- **Preuve** : `_SLOT_FOR` (`prod_server.py:368`) mappe `hihat` **et** `perc` sur le slot unique `hihat_perc` ; `_resolve_palette` applique le même `slot["filepath"]` aux deux pistes.
- **Solution** : `propose_palettes(include_pool=True)` renvoie le top‑8 trié par rôle ; le résolveur affecte aux pistes un fichier **non déjà utilisé** (le perc préfère la catégorie `perc`) ; à défaut → `gl.clap` (`pick_gl_plugin("perc")`). Garde finale `enforce_blueprint_sample_fit` : doublon ⇒ remplacement.

### F7 🟠 Faux positifs `root_note_mismatch` sur les drums (bloqueraient le build une fois F1 corrigé)

- **Preuve** : `blueprint_safety.py:394-416` applique le verrou de racine **avant** le test percussif (l.519) ; or pour les percussions `root_note` est volontairement la *hauteur notée du kit* (36/38/39/42, `prod_server.py:457-474`) et non le `midi_root` détecté.
  Audit réel : 4 erreurs `root_note_mismatch` (kick, snare, hihat, perc).
- **Solution** : sauter le verrou de racine quand `is_percussive_role(role) or is_percussive_role(name)`. **À faire avec F1, sinon F1 bloquerait tous les morceaux.**

### F8 🟠 La palette peut être « excellente » avec une cohérence timbrale de 0,5

- **Preuve** : `bp.palette` = `score 1,0677` (> 1, le score n'est pas borné après `+0,05·n_real` et bonus sample-first +0,25), `timbre_coherence.score = 0,5`, `max_bark_collision = 0,46`, **sans effet** : aucun seuil.
  `fallback_gate = PASS` ne mesure que le *ratio* de samples (≥ 0,7), pas leur adéquation. Le biais « sample-first » (+0,25 +0,09 + 0,05/sample) écrase systématiquement le critère de qualité.
- **Solution** : (a) borner `pal_score` à 1 et publier `quality` séparément de `sample_ratio` ; (b) seuil dur `timbre_coherence.score ≥ 0,6` sinon second passage avec pénalité de collision ×2 ; (c) le sample-first ne doit s'appliquer qu'à des candidats **dont la politique F4 est `preferred`/neutre et le registre F3 valide** ; (d) `fallback_gate` doit échouer si > 1 piste a un `S_max` hors cap, quel que soit le ratio.

### F9 🟡 Catalogue pollué par des doublons

- **Preuve** : `kick04` existe dans `kick/` **et** `other/` ; variantes `-2.wav`, `_clean.wav` du même son ; 422 entrées pour un banc bien plus petit. Le glouton tire profit de doublons, et la diversité inter-palettes est faussée.
- **Solution** : dédoublonner l'index par hash du contenu (premier 64 Ko + durée) dans `index_project_samples.py`, garder la catégorie du dossier **spécifique** (`kick/` bat `other/`).

### F10 🟡 Artefacts périmés trompeurs dans le projet

- `renders/master.wav` est référencé par `master.wav.origin.json` avec source `kick/glorkglunk_-_kick_3_clean.wav` : ce n'est pas un rendu du morceau, alors que `handoff.txt` dit `master=n/a` (Gridline absent). Risque : écouter un faux master et juger les sonorités sur autre chose.
- **Solution** : `studio_compose` doit supprimer/renommer `renders/master.wav*` quand le rendu n'a pas eu lieu, et écrire `master=STALE` si un fichier ancien subsiste ; `handoff.txt` doit afficher `sound_guard=` (cf. §3).

### F11 🟡 `studio_refine` ne revalide pas les sons après un changement de registre

- `refine_project` (`prod_server.py:598-636`) régénère `score_ir` via `strudel_patch`/`semantic_patch` (ex. `lead oct +1`) sans repasser par la résolution/garde de palette : un patch peut recréer un `S_max` hors cap sur un sample déjà validé.
- **Solution** : après tout patch, exécuter la garde finale (§3, étape 4) et réécrire `blueprint.tracks` si besoin.

---

## 3. Architecture de correction recommandée (ordre d'implémentation)

1. **`scripts/sample_fit_guard.py`** (pur stdlib, 0 token, < 1 ms) — module unique qui regroupe : `category_tier`, `nature_verdict`, `electronic_penalty`,
   `candidate_policy`, `register_fit` (même métrique que `blueprint_safety`), `notes_by_track_from_score_ir`, `note_ranges_by_slot`, `enforce_blueprint_sample_fit`, `format_guard_line`.
2. **`audio_metrics.py`** — correctif F2 puis ré-indexation.
3. **`palette_builder.py`** — `note_ranges`, `include_pool`, politique F4, métriques F5, score borné F8.
4. **`prod_server._resolve_palette`** — calcul des `note_ranges`, affectation sans doublon (F6), puis **garde finale** `enforce_blueprint_sample_fit` (remplace par alternative saine sinon synthé `gl.*`), appelée aussi dans `refine_project` (F11). Ligne `sound_guard=OK|FIXED …` dans `handoff.txt` (≤ 160 car.).
5. **`blueprint_safety` + `song_builder` + `render_and_audit` + `loop_first_runner`** — F1 et F7 ; élever `inharmonic_bell_confined` en **error** pour `chords/pad/bass`.
6. **Tests** : `tests/test_sample_fit_guard.py` (FX root 24 sur accords ⇒ remplacé ; kick en basse ⇒ exclu ; perc≠hihat ; drums sans faux positif ; 808 saturé jouable).

Règle de conception à respecter : **le garde-fou ne bloque jamais le morceau, il remplace** (alternative saine → synthé calibré), pour rester à 0 tour LLM supplémentaire.

## 4. Critères d'acceptation (rejouer sur `rap-3min` après correctif)

| Contrôle | Attendu |
|---|---|
| `summarize_musical_safety(bp, catalogue, notes)` | `errors == 0` (hors faux positifs drums corrigés) |
| chords | aucun sample `fx`/`inharmonic_bell_or_fx` ; `S_max ≤ 7` ou synthé `gl.*` |
| bass | catégorie `808`/`bass` (ou `gl.fingerbass`), jamais `kick` |
| kick / perc / hihat | trois fichiers distincts, kick issu de `kick/` |
| `selection_trace` | contient `policy_excluded`, `gate_unmeasured`, `range_excluded` |
| `handoff.txt` | `sound_guard=OK` ou `FIXED …` |
| Rendu A/B (humain, 1 écoute) | plus de timbre « chipmunk/FX » sur les accords, basse grave et ronde |

## 5. Améliorations potentielles complémentaires

**Fiabilité**
- Scénario B/`user_samples_locked` : ne verrouiller que ce que l'utilisateur a **explicitement** fourni ; la banque racine `samples/` traitée comme « projet » prive le workflow de `samples.db` (7 324 entrées) et de ses filtres de catégorie.
- Faire du rapport `musical_safety` un champ **obligatoire** du retour compact de `studio_compose` (`safety=0err/0warn`).
- Test CI « palette sur banc réel » : pour `trap_rap`, `lofi`, `classical_ambient`, vérifier qu'aucun slot tonal n'a `S_max > cap` (échoue le build, évite la dérive).
- Calibrer les caps par catégorie : un 808 pitché +12 st reste acceptable alors qu'un piano ne l'est pas ; exposer `SAMPLE_SHIFT_CAP_ST` par rôle (`bass_sub: 9`) dans `config/mix_profiles.json`.
- Brancher `build_multizone_samples` (zones de 6 st, ±3 st) : débloque les banques à racines graves sans changer de timbre.
- Écoute de contrôle automatique : `listener_backend` sur 8 mesures de chaque piste tonale (centroïde, rugosité de paires) avant le rendu complet.

**Sons plus agréables**
- Pré-filtre « organique » par défaut pour `valence ≥ −0,2` : préférer `piano/guitar/pad` sur chords/lead, synthés `gl.*` filtrés (cutoff ≤ 6,5 kHz, attaque ≥ 8 ms) sinon.
- Chaîne douce systématique sur pistes tonales sample : LPF 6,5–9 kHz, saturation douce ≤ 0,05, réverbe courte sur `FX Room` ; high-shelf −3 dB @ 5,5 kHz sur `BUS Music` si centroïde moyen > 3 kHz.
- Pénaliser dans la sélection tout timbre dont le centroïde > 4 kHz sur les accords et toute `crest` > 13 dB sur les tonaux (déjà dans les plafonds, mais inactifs — cf. F5).
- Interdire deux timbres à pic spectral dans la même bande critique : durcir le seuil `max_bark_collision` (> 0,35 → pénalité ×2).

**Économie de tokens**
- Tout est déterministe et local : garder le LLM hors de la boucle son (aucune relecture de `samples_catalog.json`, 1,2 Mo ; ne jamais le renvoyer dans un retour d'outil).
- Retours compacts : `sound_guard=` (≤ 160 car.) remplace tout rapport détaillé ; détail dans `renders/diagnostics/sound_guard.json`.
- `studio_compose` retourne déjà `palette=… samples=N` : y ajouter `rej=K` (candidats exclus) évite un tour de diagnostic.
- Pré-vol « 0 rendu » unique (`preflight_strudel_gate.py`) étendu au **pré-vol sonore** (`sample_fit_guard`) : 1 appel avant compilation plutôt qu'un cycle compose → écoute → refine.
- Cache de la politique/ranges par `(sha(score_ir), sha(catalogue))` : re-composer sans changement de notes = 0 recalcul.

---

## 6. Récapitulatif

| # | Faille | Gravité | Fichier(s) | Solution courte |
|---|---|---|---|---|
| F1 | Gate `S_max` jamais alimenté (pas de notes, mauvais chemin catalogue) | 🔴 | `song_builder`, `render_and_audit`, `loop_first_runner`, `compile_song` | passer `notes_by_track` + cascade de catalogue |
| F2 | 808 saturés classés « accords » | 🔴 | `audio_metrics.py:563-591` | exempter les partiels de f0 (k ≤ 8) ; ré-indexer |
| F3 | Registre joué ignoré à la sélection | 🔴 | `prod_server`, `palette_builder` | `note_ranges` + cap `S_max` |
| F4 | Dossier d'origine et nature ignorés (mode projet) | 🔴 | `palette_builder:362-482` | politique de timbre par rôle |
| F5 | Gate d'agréabilité vacuous (métriques absentes = PASS) | 🔴 | `timbre_pleasantness`, `palette_builder` | copier les métriques ; manquant = FIX |
| F6 | Fichier partagé perc/hihat | 🟠 | `prod_server:368,465` | pool trié + affectation unique |
| F7 | Faux positifs `root_note_mismatch` drums | 🟠 | `blueprint_safety:394-416` | exempter les rôles percussifs |
| F8 | Score de palette non borné, cohérence ignorée | 🟠 | `palette_builder:1046-1058` | seuil dur + sample-first conditionné |
| F9 | Doublons dans le catalogue | 🟡 | `index_project_samples` | hash de contenu |
| F10 | Faux `master.wav` périmé | 🟡 | `prod_server` | purge + `master=STALE` |
| F11 | `studio_refine` sans revalidation | 🟡 | `prod_server:598` | garde finale après patch |

*Méthode de vérification employée : lecture de `projects/rap-3min/{song_blueprint,score_ir}.json`, de `samples_catalog.json`, exécution en lecture seule de `blueprint_safety.summarize_musical_safety`, et ré-analyse DSP de `samples/808/` avec le correctif F2 appliqué temporairement puis retiré (`git status` : aucun fichier suivi modifié).*
