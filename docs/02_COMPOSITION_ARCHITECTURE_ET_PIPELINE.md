# 02 — Composition, architecture & pipeline

> Fusion des anciennes facettes 02 (composition/émotion/théorie), 03 (architecture/blueprint/cohésion)
> et de l'audit v3.2 (carences & solutions). Contenu intégral conservé ci-dessous.

---

# Facette 2 — Composition Universelle, Émotion `(V, T, E, D)`, Harmonie, Mélodie & Groove

> **Cheatsheet IA (L'essentiel v2.0)**
> - **Règle n°1** : Ne **jamais** calculer notes MIDI, gammes ou accords de tête — importer `SCALES`, `degree_to_midi` (`song_builder.py`) et `optimize_voice_leading` (`musicality_metrics.py`). Carte fichiers → [`docs/00_SOMMAIRE.md`](00_SOMMAIRE.md).
> - **Émotion `(V, T, E, D)`** : Choisir **Mode** (§2), **Progression éprouvée (§4)**, **BPM & Matrice Rythmique (§6)**, **Palette (§7)**.
> - **Mélodie (§5)** : Motif de **3 à 5 notes** varié sur 4 mesures (`A - A' - A'' [Apex <= 74] - B [Silence]` si `V >= 0`, ou courbe descendante/hypnotique si `V < 0`) ; `>= 65 %` pas conjoints (`<= 4 st`), `20–50 %` silence (`[0, 0.92]` en `free_ambient`), temps forts sur accord (`>= 80 %` consonant / `>= 65 %` sombre), **zéro** note sur chaque croche, **zéro** arpège 1-3-5-8 plat ou vélocité constante.
> - **Humanisation automatique** : Appeler **`scripts/groove_lib.py`** (`apply_micro_strum`, `apply_haas_pocket`, `apply_anti_machine_gun`, `enforce_frequency_inertia`) au lieu de coder les décalages à la main.
> - **Registres stricts** : Basse/808 **`31–48`** (`>= 49 Hz`, en `C..F#` jouer `36–42`), Accords `48–67`, Lead `55–72` (Apex `<= 74` ; `75–81` réservé aux timbres doux si `V > +0.3`).

---

## Chapitre 1 — Table MIDI Universelle & Helper Déterministe

> **Interdiction** de convertir notes ↔ numéros MIDI de tête : utiliser `song_builder.SCALES` et `song_builder.degree_to_midi(tonic_midi, mode, degree_0idx, oct_shift=0)`.

| Note | C | C#/Db | D | D#/Eb | E | F | F#/Gb | G | G#/Ab | A | A#/Bb | B |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **`pc`** | `0` | `1` | `2` | `3` | `4` | `5` | `6` | `7` | `8` | `9` | `10` | `11` |
| **Oct 1** | `24`⚠️ | `25`⚠️ | `26`⚠️ | `27`⚠️ | `28`⚠️ | `29`⚠️ | `30`⚠️ | **`31`** | **`32`** | **`33`** | **`34`** | **`35`** |
| **Oct 2** *(Basse)* | `36` | `37` | `38` | `39` | `40` | `41` | `42` | `43` | `44` | `45` | `46` | `47` |
| **Oct 3** *(Chords)* | `48` | `49` | `50` | `51` | `52` | `53` | `54` | `55` | `56` | `57` | `58` | `59` |
| **Oct 4** *(Lead)* | `60` | `61` | `62` | `63` | `64` | `65` | `66` | `67` | `68` | `69` (`440Hz`) | `70` | `71` |
| **Oct 5** *(Apex)* | `72` | `73` | `74` | `75` | `76` | `77` | `78` | `79` | `80` | `81` | `82` | `83` |

* **Bornes obligatoires** : Basse/808 **`MIDI 31–48`** (en `C..F#`, privilégier Octave 2 `36–42` contre l'infra-sub `< 42 Hz`) · Accords **`48–67`** · Lead **`55–72`** (`<= 74` par défaut ; `75–81` uniquement timbres doux + `V > +0.3`).
* **Registres adaptatifs sombres (`V < -0.2`, Phase 8 `Q5.2`)** : resserrer `Chords/Pad` dans **`48–60`** et `Lead` dans **`60–72`** (au moins **`+5 st`** d'écart moyen entre centroïdes MIDI, vérifié par `chords_lead_register_gap_st` dans `musicality_metrics.py`).
* Gammes couvertes par `song_builder.SCALES` : `ionian`/`major`, `dorian`, `phrygian`, `lydian`, `mixolydian`, `aeolian`/`minor`, `harmonic_minor`, `melodic_minor`, `phrygian_dominant`, `locrian`, `pentatonic_minor`, `pentatonic_major`, `blues`.

---

## Chapitre 2 — Modèle Émotionnel Holistique `(V, T, E, D)` & Modes

Toute demande utilisateur se traduit en 4 axes mesurables :
1. **Valence ($V \in [-1, +1]$)** : `-1.0` tragique/sombre ↔ `0.0` neutre/mystérieux ↔ `+1.0` joyeux/lumineux.
2. **Tension ($T \in [0, 1]$)** : `0.0` apaisé/consonant ↔ `1.0` nerveux/urgent/dramatique.
3. **Énergie ($E \in [0, 1]$)** : `0.0` intimiste/calme ↔ `1.0` puissant/frontal/monumental.
4. **Profondeur ($D \in [0, 1]$)** : `0.0` sec/proche/percutant ↔ `1.0` vaste/éthéré/cinématique.

### 2.1 Modes & Gammes (du plus sombre au plus lumineux)

> **Règle de Verrouillage par Valence ($V$)** :
> - Si **`V <= -0.3`** (triste, sombre, mélancolique, tendu) : **interdiction** d'utiliser Ionien, Lydien, Mixolydien ou Pentatonique Majeure. Choisir exclusivement parmi **Éolien**, **Phrygien**, **Mineur Harmonique**, **Dorien** (uniquement si cool/chic) ou **Pentatonique Mineure**.
> - Si **`V <= -0.7`** (sombre, tragique, menaçant) : privilégier **Phrygien** (`i - bII`) ou **Mineur Harmonique** (`i - bVI - V7`) avec ancrage fort sur la tonique `i`.

| Mode | Intervalles (demi-tons) | $V$ | $T$ | Couleur Émotionnelle & Genres Typiques |
| :--- | :--- | :---: | :---: | :--- |
| **Éolien (Mineur Naturel)** | `[0, 2, 3, 5, 7, 8, 10]` | `-0.7` | `0.3` | Triste, nostalgique, dramatique (Pop triste, Trap/Cloud, Rock, Ballade) |
| **Phrygien** | `[0, 1, 3, 5, 7, 8, 10]` | `-0.9` | `0.8` | Menaçant, sombre, tendu (`i <-> bII`) (Dark Trap, Drill, Metal, Techno) |
| **Mineur Harmonique** | `[0, 2, 3, 5, 7, 8, 11]` | `-0.8` | `0.7` | Tragique, théâtral, classique/épique (Orchestral, Metal, Trap dramatique) |
| **Phrygien Dominant** | `[0, 1, 4, 5, 7, 8, 10]` | `-0.6` | `0.85` | Mystique, oriental/flamenco, intense (Cinéma, Trap, Metal, Electro) |
| **Locrien** | `[0, 1, 3, 5, 6, 8, 10]` | `-1.0` | `1.0` | Horreur, chaos, instabilité (Sound design horreur uniquement) |
| **Pentatonique Mineure / Blues** | `[0, 3, 5, 7, 10]` (+`6`) | `-0.4` | `0.4` | Expressif, accrocheur, solo garanti sans clash (Rock, Blues, Rap, R&B) |
| **Dorien** | `[0, 2, 3, 5, 7, 9, 10]` | `-0.2` | `0.3` | Mineur élégant, cool, résilient (Soul, R&B, Funk, Lo-Fi, House, Cinéma) |
| **Mixolydien** | `[0, 2, 4, 5, 7, 9, 10]` | `+0.5` | `0.3` | Assuré, groove, rock/funk positif (Rock, Funk, Afrobeat, House) |
| **Pentatonique Majeure** | `[0, 2, 4, 7, 9]` | `+0.7` | `0.1` | Mélodique, instantanément chantable, zéro fausse note (Pop, Folk, Lo-Fi) |
| **Ionien (Majeur)** | `[0, 2, 4, 5, 7, 9, 11]` | `+0.8` | `0.1` | Joyeux, franc, solaire (Pop, Folk, Indie, House lumineuse) |
| **Lydien** | `[0, 2, 4, 6, 7, 9, 11]` | `+1.0` | `0.2` | Féérique, spatial, émerveillement (Cinéma, Synthwave, Ambient, VGM) |

---

## Chapitre 3 — Accords, Voicings & Dissociation Spectrale

### 3.1 Formules d'Accords (demi-tons depuis la fondamentale de l'accord)

| Code | Intervalles | $V$ | $T$ | Couleur & Usage |
| :--- | :--- | :---: | :---: | :--- |
| `Maj` / `m` | `[0, 4, 7]` / `[0, 3, 7]` | `+0.9` / `-0.7` | `0.1` / `0.2` | Clarté / gravité franche (Pop, Rock, Classique, Folk) |
| `Maj7` / `m7` | `[0, 4, 7, 11]` / `[0, 3, 7, 10]` | `+0.6` / `-0.5` | `0.3` | Nostalgie douce / mélancolie soyeuse (Lo-Fi, Jazz, R&B, Cloud, House) |
| `Maj9` / `m9` | `[0, 4, 7, 11, 14]` / `[0, 3, 7, 10, 14]` | `+0.7` / `-0.4` | `0.35` | Planant, luxueux, cinématographique (Neo-Soul, R&B, Lo-Fi, Chill) |
| `sus2` / `sus4` | `[0, 2, 7]` / `[0, 5, 7]` | `0.0` | `0.4` / `0.6` | Ouvert, aérien / héroïque suspendu (Ambient, Rock, Cinématique) |
| `5` (Power) | `[0, 7]` | `0.0` | `0.3` | Puissance neutre, zéro boue harmonique (Rock, Metal, Dark Trap) |
| `m(add9)` / `m(b6)` | `[0, 3, 7, 14]` / `[0, 3, 7, 8]` | `-0.6` / `-0.8` | `0.6` | Tristesse nerveuse / drame déchirant (Ballade, Cinéma, Trap sombre) |
| `7` / `dim7` | `[0, 4, 7, 10]` / `[0, 3, 6, 9]` | `+0.2` / `-0.9` | `0.7` / `0.95` | Tension de dominante (appel vers `I`/`i`) / angoisse (Jazz, Blues, Classique) |

* **Voicing Serré (`< 1` oct)** = intime/dense · **Voicing Ouvert (`1.5–2.5` oct)** = épique/aéré · **Toujours passer les pitch-classes dans `optimize_voice_leading()`** pour garder un mouvement moyen `<= 2.5 st` et éviter les blocs parallèles amateurs.
* **Dissociation Spectrale (Émotions Paradoxales)** : pour mêler deux émotions (ex. *triste + nerveux*, *doux + dansant*), affecter une polarité aux médiums/aigus (`> 300 Hz` : accords `m9` éthérés + soupirs descendants `b6 -> 5`) et la polarité opposée aux graves/rythme (`< 150 Hz` : basse sèche saturée + rythme syncopé).

---

## Chapitre 4 — Catalogue Universel de 14 Progressions Harmoniques Éprouvées

> Une IA textuelle ne doit **jamais inventer des accords au hasard**. Choisir une progression éprouvée dans le bloc correspondant à la valence `(V)` cible, la transposer dans la tonalité cible, puis calculer ses renversements via `optimize_voice_leading()`.
>
> **v4.0 (pont Strudel)** : avec le frontend `composition.strudel`, les mêmes progressions s'écrivent en symboles d'accords absolus — `$chords: chord("<Cm7 Abmaj7 Fm7 G7>").slow(2)` — que `scripts/strudel_to_score_ir.py` convertit en degrés romains (`symbol_to_roman`) pour la même `harmonic_grid` ; la mélodie s'écrit en degrés via `.scale("C:minor")` (jamais de noms de notes bruts susceptibles de se désynchroniser).

### A. Sombre & Triste (`V < -0.3`)

| # | Émotion / Ambiance | Genres | Mode | Progression (4 ou 8 mesures) | Mouvement Basse (demi-tons depuis tonique) |
| :---: | :--- | :--- | :--- | :--- | :--- |
| **P4** | Triste, profond, introspectif | Ballade, Cloud Trap, Piano, Lo-Fi | Éolien | `i7 – VI(maj7) – iv7 – v7` (ou `V7`) | `[0, 8, 5, 7]` |
| **P4b** | Mélancolique, lourd, hypnotique | Trap sombre, Cloud, Piano, Cinéma | Éolien | `i – bVI – v – i` ou `i – bVI – iv – i` | `[0, 8, 7, 0]` ou `[0, 8, 5, 0]` |
| **P5** | Sombre, nerveux, menaçant | Dark Trap, Drill, Metal, Techno | Phrygien | `i – bII – i – bVII` (ou `i – bII`) | `[0, 1, 0, 10]` |
| **P5b** | Sombre, minimaliste, oppressant | Dark Trap, Drill, Ambient sombre | Phrygien / Pédale | `i – bII(maj7) – i – bVI` (pédale de tonique `i` possible) | `[0, 0, 0, 8]` ou `[0, 1, 0, 8]` |
| **P6** | Tragique, dramatique, théâtral | Orchestral, Classique, Rap épique | Min. Harm. | `i – iv – VI – V7` | `[0, 5, 8, 7]` |
| **P3** | Épique, mélancolique-héroïque *(relatif majeur)* | Pop, Rock, Cinéma, Rap, EDM | Éolien | `i – VI – III – VII` *(⚠️ Attention : sonne héroïque/pop — attirance vers le relatif majeur `III`. Ne pas l'utiliser pour une ambiance vraiment sombre, glaciale ou dépressive.)* | `[0, 8, 3, 10]` |

### B. Neutre & Groove (`-0.3 <= V <= +0.3`)

| # | Émotion / Ambiance | Genres | Mode | Progression (4 ou 8 mesures) | Mouvement Basse (demi-tons depuis tonique) |
| :---: | :--- | :--- | :--- | :--- | :--- |
| **P7** | Cool, groove, chic, résilient | House, Funk, Soul, R&B, Lo-Fi | Dorien | `i7 – IV7 – i7 – bVII(maj7)` | `[0, 5, 0, 10]` |
| **P11** | Blues / Groove intemporel (12 mes.) | Blues, Rock, Jazz, Soul | Blues/Mix. | `I7(×4) – IV7(×2) – I7(×2) – V7 – IV7 – I7 – V7` | `[0..0, 5, 5, 0, 0, 7, 5, 0, 7]` |
| **P12** | Escalade émotionnelle (Pont / Pré-refrain) | Tous genres | Éolien/Ion. | `iv7 – III – VI – V(sus4 -> 7)` | `[5, 3, 8, 7]` |

### C. Lumineux & Joyeux (`V > +0.3`)

| # | Émotion / Ambiance | Genres | Mode | Progression (4 ou 8 mesures) | Mouvement Basse (demi-tons depuis tonique) |
| :---: | :--- | :--- | :--- | :--- | :--- |
| **P8** | Chaleureux, jazz, cosy | Lo-Fi, Jazz, Neo-Soul, Bossa | Ionien | `ii7 – V7 – Imaj7 – vi7` | `[2, 7, 0, 9]` |
| **P9** | Rêveur, spatial, contemplatif | Ambient, Cinéma SF, Chill, VGM | Lydien | `Imaj7 – II(add9) – Imaj7 – vii7` | `[0, 2, 0, 11]` |
| **P10** | Énergique, solaire, rock/funk | Rock, Funk, Indie, Afrobeat | Mixolydien | `I – bVII – IV – I` | `[0, 10, 5, 0]` |
| **P2** | Nostalgique, doux-amer, lumineux | Pop, Synthwave, Anime/J-Pop | Ionien | `IVmaj7 – V7 – iii7 – vi7` | `[5, 7, 4, 9]` |
| **P1** | Joyeux, fédérateur, hymne | Pop, Indie, EDM, Rock | Ionien | `I – V – vi – IV` | `[0, 7, 9, 5]` |

* **Astuce Pont / Contraste harmonique** : Si le Couplet/Refrain utilise `P4` / `P4b` ou `P3`, utiliser `P12` (`iv7–III–VI–V7`) au Pont pour créer un soulèvement émotionnel (ou basculer vers `iv -> V7` en mineur harmonique pour appeler le retour sur `i`).

---

## Chapitre 5 — Recette Mélodique, Relais Basse & Expressivité (`scripts/groove_lib.py`)

### 5.1 Générer une Mélodie Agréable sur 4 Mesures (`V >= 0` vs `V < 0`)
1. **Cellule `A` (Mesure 1)** : `3–5` notes sur `4` beats avec `>= 0.75–1.5` beat de silence, ancrée sur l'accord 1, `>= 65 %` pas conjoints (`<= 4 st`, sinon retour inverse de `1–3 st` *Gap-Fill*). Ex. en `Cm7` (`C=0, Eb=2, G=4, Ab=5`) : beats `[0.0, 1.0, 1.5, 2.5]` → `[G4, Ab4, G4, Eb4]`.
2. **Développement selon Valence (`V`)** :
   * **`V >= 0` (`A - A' - A'' [Apex <= 74] - B [Silence]`)** : `A'` adapte le même rythme à l'accord 2 + respiration ; `A''` atteint l'apex unique (`+2..+5 st`, `<= 74`) puis redescend ; `B` résout sur tierce/tonique puis **silence sur beats `2.5..4.0`**.
   * **`V < 0` (Sombre / Hypnotique)** : `A` descendant (`b6 -> 5` ou `b3 -> 2 -> 1`, `MIDI 58–67`) ; `A'` répétition obsédante `-1..-2` degrés ; `A''` appoggiature `b6->5` / `b2->1` (sans apex joyeux) ; `B` retombée grave + `1.5–2.0` beats de silence.
3. **Sculpting & Contre-chant** : varier les vélocités (temps `0.0`: `92–104`, temps `2.0`: `84–94`, contretemps: `68–82`, apex/tension `+6..10`, fin `-10..-15`), légato `0.90–0.98×` / pluck `0.45–0.70×`, mouvement Basse/Lead contraire ou oblique (`>= 55 %`), contre-chant uniquement dans les silences du Lead (`overlap <= 15 %`).

### 5.2 Loi d'Inertie, Haas & Expressivité Automatisée (`groove_lib.py`)
* **Inertie fréquentielle** : `< 100 Hz` toujours long (`>= 1/2` Sub `20–60 Hz`, `>= 1/8` Basse `60–250 Hz` sauf `Release < 50 ms`) ; subdivisions rapides (`1/16`, `1/32`) réservées aux aigus `> 800 Hz`. Imposer automatiquement via **`enforce_frequency_inertia(notes)`**.
* **Humanisation déterministe (`scripts/groove_lib.py`)** :
  * **`apply_micro_strum(chords, step_ms=15.0, bpm=...)`** : décale chaque voix ascendante (`+10–25 ms`) et atténue les voix internes.
  * **`apply_haas_pocket(tracks, bpm=..., profile=...)`** : Kick = `0 ms` (ancrage), Snare/Clap `+0..+10 ms` (urgent `-5 ms`), Hats `+5..+18 ms`, Basse `+10..+20 ms` (préserve l'attaque Kick et le headroom).
  * **`apply_anti_machine_gun(notes)`** : humanise vélo/longueur sur motifs rapides (`>= 1/16`) sans jamais répéter deux frappes identiques.

### 5.3 Bibliothèque Universelle de 11 Matrices Rythmiques Exactes (Beats 0-Indexed)

> Chaque mesure 4/4 vaut `4.0` beats (`0.00`..`3.75` par `0.25`). Choisir la matrice du genre cible et appliquer `groove_lib` + variations en mes. 4 et 8.

| Style / Genre | BPM | Feel | Kick (beats) | Snare / Clap (beats) | Hi-Hat / Ride / Perc (beats & vélocités) | Swing / Micro-timing |
| :--- | :---: | :---: | :--- | :--- | :--- | :--- |
| **1. Trap / Cloud** | `124–152` | Half-time | `0.0, 1.75, 2.75` (var. `0.0, 2.5`) | `2.0` (`vel 108`) | Croches `0.0, 0.5..3.5` (`75–92`) + rolls `1/16`/`1/32` sur `1.5–1.75` ou `3.5–3.875` (pitch `-1..-3 st`) | Snare `+0.01`, Hats `-0.005` |
| **2. Drill** | `138–146` | Half-time | `0.0, 1.5, 3.25` | **`2.0` et `3.5`** (signature Drill) | Syncopé `0.0, 0.75, 1.5, 2.0, 2.75, 3.5` (`72–95`) + glides 808 en mes. 4/8 | Sec, tendu, `swing = 0` |
| **3. Boom Bap / Lo-Fi** | `76–94` | Groove | `0.0, 1.75, 2.5` (var. `0.0, 2.25, 2.75`) | `1.0, 3.0` (`vel 98`, ghost `1.75` `vel 45`) | Croches `0.0, 0.5..3.5` avec accents alternés `88 / 58` | **Swing `0.15–0.30`**, Snare retardé `+10–18 ms` |
| **4. House / Techno / EDM** | `118–134` | 4-on-floor | **`0.0, 1.0, 2.0, 3.0`** | Clap `1.0, 3.0` (`vel 102`) | Open Hat contretemps **`0.5, 1.5, 2.5, 3.5`** (`88`) + Closed `0.25, 1.25...` (`55`) | House swing `0.10–0.20` ; Techno `0.0` |
| **5. Pop / Synthwave** | `102–126` | Binaire | `0.0, 1.5, 2.0` (ou 4-on-floor au Refrain) | `1.0, 3.0` (`vel 104`) | Croches régulières `0.0..3.5` (`82 / 64`) + Shaker `1/16` au Refrain (`48–62`) | Propre, `swing 0–0.05` |
| **6. Rock / Indie / Metal** | `105–165` | Binaire | `0.0, 1.5, 2.0, 2.5` (Metal : double `1/16`) | `1.0, 3.0` (`vel 112`) + crash sur `0.0` de mes. 1 | Croches Hi-Hat fermé (Couplet) → Ride ou Crash-Ride (Refrain) (`90 / 70`) | Humain `±6 ms`, fill toms/snare sur `3.0–3.75` mes. 4/8 |
| **7. Funk / R&B / Neo-Soul** | `88–114` | Pocket | `0.0, 0.75, 2.25, 2.5` | `1.0, 3.0` (`vel 100`) + ghosts `1.75, 2.75` (`42`) | Doubles croches `0.0..3.75` avec forte dynamique (`85, 45, 68, 42`) | **Pocket / Swing `0.12–0.25`** |
| **8. Reggaeton / Afrobeat** | `94–112` | Dembow | `0.0, 1.0, 2.0, 3.0` (Afro : `0.0, 1.5, 2.5`) | **`0.75, 1.5, 2.75, 3.5`** (Dembow) | Shaker `1/16` continu (`55–75`) + rimshot syncopé | Rebonds doux, basse calée sur Kick |
| **9. Drum & Bass / Jungle** | `168–176` | Breakbeat | **`0.0, 2.5`** | **`1.0, 3.0`** (ghosts `1.75, 2.25` `vel 52`) | `1/8` + `1/16` roulants (`82, 55, 74, 58`) | Tendu, sub basse longue |
| **10. Valse `3/4` / Ballade `6/8`** | `72–115` | Ternaire | `3/4` : `0.0` · `6/8` : `0.0, 2.5` | `3/4` : rim/brush `1.0, 2.0` (`58`) · `6/8` : `3.0` (`85`) | `3/4` : `0.0, 1.0, 2.0` (fort-doux-doux) · `6/8` : `0.0..5.0` (fort sur `0` et `3`) | Rubato léger, dynamique douce |
| **11. Ambient / Classique** | `55–90` | Libre | Aucun (ou timbale/sub doux sur `0.0` mes. 1) | Aucun (ou percussion cinématique sur pivots) | Aucun — rythme porté par les attaques lentes (`> 0.3 s`) et arpèges doux | Respiration libre, `FX Room` ample |

---

## Chapitre 6 — Palettes d'Instrumentation Cohérentes par Genre

Registres MIDI disjoints et instruments `gl.*` natifs compatibles GUI + headless :

| Famille de Genre | Profil Mix (`--profile`) | Basse / 808 (`MIDI 31–48`, `>= 49 Hz`) | Harmonie (`MIDI 48–67`) | Lead / Motif (`MIDI 55–72`, Apex `<= 74`) | Percussions / Texture |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Trap / Cloud / Drill** | `trap_rap` | `gl.sampler` 808 mono (`Voices=1`) | `gl.sampler` piano/pad WAV ou `gl.warmsynth` (assombrir par shelf doux, jamais de LPF brique < 6,5 kHz) | `gl.sampler` bell/pluck/flûte `mono_oneshot` ou `gl.pluck` (shelf doux, jamais de LPF brique < 9 kHz) | Samples Trap (Kick, Clap/Snare, Hats) |
| **Pop / Synthwave** | `pop_rnb` | `gl.analog` / `gl.fingerbass` (octave bass) | `gl.dcosynth` / `gl.warmsynth` / `gl.keys` (shelf doux, jamais de LPF brique < 6,5 kHz) | `gl.twinva` / `gl.keys` / `gl.plucker` / Voix (shelf doux, jamais de LPF brique < 9 kHz) | Kick/Snare punchy + Shaker |
| **House / Techno / EDM** | `edm_club` | `gl.acid` / `gl.analog` / `gl.stepbass` | `gl.sawswarm` / `gl.wavetable` / `gl.dcosynth` (shelf doux, jamais de LPF brique < 6,5 kHz) | `gl.wavetable` / `gl.pluck` / `gl.synth` (shelf doux, jamais de LPF brique < 9 kHz) | `gl.kick` / samples 909 + Open Hat |
| **Lo-Fi / Boom Bap** | `lofi_chill` | `gl.fingerbass` / sub rond doux | `gl.keys` (EP/Rhodes) / `gl.sampler` piano doux | `gl.plucker` (guitare douce) / flûte / piano (`<= 72`) | Kick/Snare feutrés (LPF `8–10 kHz`) |
| **Rock / Indie / Metal** | `rock_metal` | `gl.fingerbass` | `gl.plucker` / `gl.twinstring` (+ `gl.dist` suivi de LPF) | `gl.twinstring` / `gl.analog` / Voix | `gl.drumkit` ou samples acoustiques |
| **Jazz / R&B / Soul** | `jazz_acoustic` | `gl.fingerbass` (walking/pocket) | `gl.keys` (Rhodes `m7`/`m9`/`Maj9`) | `gl.keys` / `gl.sampler` sax/flûte/guitare | Kit doux / rimshot / ride |
| **Classique / Cinéma / Ambient** | `classical_ambient` | `gl.string` (contrebasse/cello) / sub doux | `gl.string` / `gl.twinstring` / `gl.warmsynth` (pad lent) | `gl.sampler` piano WAV / `gl.string` / `gl.plucker` | Timbales/percs ciné légères ou aucune |

*(Notes registres : pour les tonalités `C`, `C#`, `D`, `D#`, `E`, `F`, `F#`, écrire la fondamentale 808 en Octave 2 `MIDI 36–42` plutôt qu'en Octave 1 `< 42 Hz`. Côté aigus, `MIDI 75–81` est réservée exclusivement aux timbres très doux/sinusoïdaux — flûte douce, cordes légato avec filtre de synthé musical (pente douce) `<= 5 kHz`, jamais de LPF brique 24 dB/oct — sur morceaux lumineux `valence > +0.3`, et strictement interdite sur synthés riches, cloches FM ou morceaux sombres.)*


---

# Partie 2 — Architecture, blueprint & cohésion (ancienne facette 03)

# Facette 3 — Blueprint, Arrangement, Transitions, Gate / Autofix MIDI & `compile_song.py` (v3.2+, remplace `build_song.py` manuel)

> **Cheatsheet IA (L'essentiel v2.0)**
> - **Source unique & Isolation (`1 sonorité = 1 piste`)** : Écrire toutes les notes en **beats 0-indexed** dans `arrangement_plan.json` (v3.x) ou `composition.strudel` (v4.0) — `build_song.py` **LEGACY v2.1** (archive `REFERENCE_ARCHIVE.md`) — puis `compile_song.py` (`song_builder.py` + `gl_dsl.py` + `groove_lib.py` + `transitions_lib.py`) exporte `master_sketch.mid` (`Bass`, `Chords`, `Lead`), valider via `verify_midi_gate(..., auto_fix=True)`.
> - **Gate MIDI multi-signatures & Autofix** : Supporte `4/4`, `3/4`, `6/8`, `5/4`, `phrase_plan="free_ambient"` et morceaux sans basse (`contrary_or_oblique_ratio=None`). Si échec, **`scripts/midi_autofix.py`** corrige automatiquement avant tout appel Gridline.
> - **Flux v3.2+ (canonique)** : plus de `build_song.py` manuel — `compile_song.py <projet> [--probe]` lit `song_blueprint.json` + `arrangement_plan.json`, applique `blueprint_enricher.py` (champs créatifs omis), `NarrativeHarmony` (`section_progressions`, phrasé 8 mesures, suspensions 4→3/9→8), `OrchestrationEngine` (comping, voice-leading conscient du Lead), `ExpressionArticulations` (`glide_to_next`, vibrato retardé), `FullCoverageCoreValidator` (pré-vol 100 % + sonde `--probe`), produit le `ScoreIR` (groove en `expression`, clamp frontière 20 ms) puis `master_sketch.mid`.
> - **Piège `_infer_roles()` (§5.2)** : Nommer `Bass`/`808`, `Chords`/`Pad` (`poly_ratio >= 0.45` toléré même `< MIDI 48`), `Lead`/`Melody`, `Counter Harmony` (jamais `"Vocal"` sur contre-chant ni `"Piano"` seul sur arpège).
> - **Arrangement & Transitions (`transitions_lib.py`)** : Max **3 couches** hors batterie · paliers **4 / 8 / 16 mes.** · `beat4_dropout`, `filter_sweep`, `section_gain_arc`, `outro_fade` · fin résolue/fondue (jamais coupée net).
> - **Physique `T_noire = 60/BPM`** : Release 808 `35–55 ms` · Sidechain `65–80 ms` (`trap_rap`, `2.4:1`) ou `80–110 ms` · `FX Room` Decay `<= T_noire` rythmique ou `0.7–1.1 s` acoustique/ambient.

---

## Chapitre 1 — `song_blueprint.json`, Structure Multi-Genres & `transitions_lib.py`

### 1.1 Schéma Normatif (`schemas/song_blueprint.schema.json`)
* **Métadonnées** : `title`, `genre`, `mood`, **`mix_profile`** (`trap_rap`, `pop_rnb`, `edm_club`, `rock_metal`, `lofi_chill`, `jazz_acoustic`, `classical_ambient`), **`has_drums`** (`bool`, défaut `true` ; mettre `false` en Classique/Ambient sans batterie pour assouplir le Quality Gate), `emotion_vector` (`valence`, `tension`, `energy`, `depth`), `bpm`, `time_signature` (`numerator`, `denominator`), `key`, `swing`, `target_lufs`.
* **`harmonic_core`** : `key`, `mode`, `progression_bars`, `chord_progression` (`bass_midi` `31–48` ou `36–42` en `C..F#`, `voicing_midi` `48–67` via `optimize_voice_leading`, `chord_tones_pc`), `motif_cell`, `phrase_plan` (`A-A'-A''-B`, `A-B-A'-C`, `A-A'-B-B'`, `A-A-B-A`, `free_ambient`).
* **`structure`**, **`frequency_plan`**, **`tracks`**, **`vocals`**. Carte complète → [`docs/00_SOMMAIRE.md`](00_SOMMAIRE.md).
* **`tracks[]`** : chaque piste sample porte **`root_note`** (MIDI détecté, ex. 57 pour un La3) et **`fine_tune_ct`** (cents) issus de `propose_palettes` → `blueprint_tracks` ; `song_builder` les applique à `gl.sampler` (`channel/N/Root note`, `Fine tune`). Ne jamais laisser un sample non-C sur le root par défaut C4=60 (dissonance systématique).
* **`swing`** : appliqué réellement aux croches off-beat (`groove_lib.apply_swing`, délai = `swing × 0.25` temps) ; les notes syncopées hors grille 8ᵦ restent en place.
* **BPM des boucles** : `propose_palettes(bpm=...)` exclut les boucles dont le BPM détecté (nom de fichier ou estimation librosa dans `index_project_samples`) s'écarte de plus de 2× la tolérance (défaut 6 BPM), pénalise entre 1× et 2×.

### 1.2 Templates Structurels Multi-Genres (Choisir selon Durée & Style)

| Famille de Genre | Format ~1 min | Format ~2 min | Format ~3 min |
| :--- | :--- | :--- | :--- |
| **Pop / Rock / Synthwave / R&B** | Intro(4) → Couplet(8) → Refrain(8) → Pont(4) → Refrain(8) → Outro(4) | Intro(4) → Couplet 1(8) → Pré-Refrain(4) → Refrain 1(8) → Couplet 2(8) → Pont(8) → Refrain Final(8) → Outro(4) | Idem avec Couplets(16) + Refrain double(16) |
| **Trap / Rap / Drill** | Intro(4) → Refrain 1(8) → Couplet(12–16) → Pont(4) → Refrain 2(8) → Outro(4) | Intro(4) → Refrain 1(8) → Couplet 1(16 : 8+8) → Pont(8) → Refrain 2(8) → Couplet 2(8) → Climax(8) → Outro(8) | Intro(8) → Drop 1(16) → Verse 1(24) → Break(8) → Drop 2(16) → Climax(16) → Outro(16) |
| **House / Techno / EDM** | Intro(8) → Build(4) → Drop 1(16) → Break(4) → Outro(4) | Intro(8) → Build(8) → Drop 1(16) → Break(8) → Build 2(8) → Drop 2(16) → Outro(8) | Intro(16) → Build(8) → Drop 1(32) → Break(16) → Build(8) → Drop 2(32) → Outro(16) |
| **Lo-Fi / Jazz / Boom Bap** | Intro(4) → Thème A(8) → Variation B(8) → Thème A'(8) → Outro(4) | Intro(4) → A(8) → A'(8) → B/Solo(8) → Break(4) → A''(8) → Outro(4) | Format `AABA` (32 mes.) ou Blues `12×N` avec solos |
| **Classique / Cinématique / Ambient** | Exposition(8) → Développement(8) → Climax(8) → Coda(4–8) | Intro(8) → Thème doux(8) → Montée(8) → Climax orchestral(12) → Retombée/Coda(8) | Arche continue en 4 mouvements contrastés |

### 1.3 Matrice d'Arrangement Obligatoire (Éviter le "Mur de Son Plat")
Cocher quels rôles jouent dans chaque section afin de garantir un **arc dynamique réel (`3–5 dB LUFS`)** :

| Rôle | Intro (`E=0.3`) | Couplet (`E=0.6`) | Pont/Break (`E=0.35`) | Refrain/Drop (`E=0.9`) | Climax (`E=1.0`) | Outro (`E=0.25`) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **Harmonie (`Chords/Pad`)** | ✅ (doux/LPF) | ✅ (allégé) | ✅ (ouvert/variation) | ✅ (plein) | ✅ (+octave) | ✅ (fade-out) |
| **Basse (`808/Bass`)** | ❌ (ou 1 note fin) | ✅ (pattern sobre) | ❌ (ou longue sub) | ✅ (pattern plein) | ✅ (+glides/var.) | ❌ |
| **Batterie (`Drums`)** | ❌ (ou hats doux) | ✅ (sans open-hat) | ❌ (ou rim/tom seul) | ✅ (complet) | ✅ (+shaker/rolls) | ❌ (ou kick seul 2 mes.) |
| **Lead / Voix** | Motif filtré ou ❌ | Squelette / Couplet | Contre-chant / ❌ | ✅ (thème complet) | ✅ (+octave/contre-chant) | Écho final sur tonique |

### 1.4 Architecture Temporelle (Paliers `4 / 8 / 16`) & Transitions Automatisées (`scripts/transitions_lib.py`)
1. **Palier 4 mes.** : variation rythmique mineure (`beat4_dropout(tracks, autos, beat_idx, bpb)` : coupe batterie/basse sur dernier temps + baisse `FX Room`).
2. **Palier 8 mes.** : modification obligatoire de densité instrumentale (**aucune boucle de 8 mesures identique**).
3. **Palier 16 mes.** : saut dynamique + transition automatisée via **`transitions_lib.py`** : **`filter_sweep`** (ouverture/fermeture LPF `gl.eq`/`Cutoff`), **`section_gain_arc`** (arche dynamique `BUS Music`), **`outro_fade`** (résolution sur tonique + extinction propre).
4. **Transitions riches déclaratives (`Q5.1`)** : `generate_drum_fill(tracks, transition_beat, profile, style)` (roll `1/8 -> 1/16 -> 1/32` + crescendo `55 -> 112`, toms/ghost notes, pickup jazz, no-op ambient), `add_transition_fx` (riser `300 Hz -> 9 kHz` + silence `0.25 beat` + impact filtré vers `FX Hall 22`) et `apply_harmonic_anticipation` (accords/basse anticipés d'une croche, note précédente raccourcie, zéro chevauchement). Types JSON acceptés dans `arrangement_plan.json` (top-level `transitions` ou `sections[].transitions[]`) : `drum_fill`, `riser_impact`, `drum_fill_and_riser`, `harmonic_anticipation`, `impact`.

---

## Chapitre 2 — Cohérence Multi-Échelles, Plafond Cognitif & Physique (`T_noire`)

* **Multi-échelles** : Échantillon (`< 5 ms`, zéro clic, `440 Hz ± 5 ct`) → Transitoire (`5–50 ms`, attaques concurrentes `<= 15 %`) → Motif (`1–2 temps`) → Phrase (`4 mes.`) → Section (`8–16 mes.`) → Macro (`section_lufs`).
* **Plafond cognitif (`5–8` pistes simultanées par mesure vs isolation `1 sonorité = 1 piste`)** : le cerveau ne suit que `3–5` foyers ; plafonner à `5–8` pistes **jouant simultanément** (`1` Intro → `8` Climax), indépendamment du nombre total de pistes du projet Gridline (`9–14` pistes dédiées autorisées — ne jamais fusionner des pistes !).

| Catégorie | Quota Simultané | Bande | Règle d'Exclusion & Priorité |
| :--- | :---: | :---: | :--- |
| **1. Fondation (Low-End)** | **1–2** | `20–200 Hz` | Sidechain / décalage Kick–Basse. |
| **2. Rythmique (Groove)** | **2–4** | `150 Hz–16 kHz` | Cases complémentaires (`Kick`, `Snare`, `Hats`). |
| **3. Harmonie (Tapis)** | **1–2** | `200 Hz–3 kHz` | Creux EQ `1–3 kHz` sous le Lead. |
| **4. Élément Principal (Lead)** | **1** (max 2) | `500 Hz–5 kHz` | Domine l'harmonie de `>= +3 dB` (ou question-réponse). |
| **5. Habillage (Ear Candy)** | **1–2** | Filtré | Présence **< 15 %** de la durée totale. |

* **Calibrage sur `T_noire = 60 / BPM`** : Release 808 `35–55 ms` (`Voices=1`) · Sidechain Release `65–80 ms` (`trap_rap`, `2.4:1`, `-18 dB`) ou `80–110 ms` · `FX Room` Decay `<= T_noire` rythmique (`0.20–0.42 s`) ou `0.7–1.1 s` acoustique/ambient (`gl_dsl.reverb_room`).

---

## Chapitre 3 — Loi de la Réduction Unique (`master_sketch.mid`)

1. Ne **jamais** composer directement dans Gridline sans `master_sketch.mid` (3 pistes : `Bass`, `Chords`, `Lead`) généré en beats 0-indexed avec `optimize_voice_leading(chord_pcs, 48, 67)`.
2. Valider via `verify_midi_gate(..., time_signature=(num, den), phrase_plan=..., auto_fix=True)`.
3. Éclater la partition vers Gridline avec **`song_builder.py`** en respectant **`1 sonorité = 1 canal + 1 insert + patterns dédiés + piste Playlist nommée`**.

---

## Chapitre 4 — Grammaire Mélodique (`M1–M5`), Verticale (`V1–V6`) & Horizontale (`H1–H3`)

* **M1 (Conjoint + *Gap-Fill*)** : `>= 65 %` des intervalles `<= 4 st` ; `>= 60 %` des sauts `>= 5 st` suivis d'un retour inverse de `1–3 st`. Ambitus recommandé `<= 19 st` (`12–16 st` optimal).
* **M2 (Économie Motivique)** : `A - A' - A'' (Apex <= 74) - B` (`V >= 0`) ou motif descendant/hypnotique (`V < 0`).
* **M3 (Respiration)** : `20 %` à `50 %` de silence réel dans la piste `Lead`.
* **M4 (Gravité Harmonique)** : `>= 80 %` des notes sur temps forts dans l'accord courant en mode consonant (`>= 65 %` en mode sombre/tendu pour autoriser les appoggiatures expressives `b6->5`, `4->b3`, `b2->1`).
* **M5 (*Pocket Alignment*)** : appuis du Lead calés sur temps `1`/`3` (ou anacrouses) en dialogue avec la batterie.
* **V1 (Voice Leading)** : distance moyenne entre accords consécutifs `<= 2.50 st`, maintien des notes communes.
* **V2 (Zéro transposition d'accord samplé)** : jamais transposer un sample d'accord complet ; utiliser un `mono_oneshot` ou un synthé polyphonique `gl.*`.
* **V3 (Mouvement Contraire/Oblique)** : `>= 55 %` des transitions Basse/Lead en sens contraire ou oblique.
* **V4 (Relais d'Attaques)** : opposer attaque sèche et attaque lente ; contre-chant confiné aux silences du Lead (`overlap <= 15 %`).
* **V5 (EQ Miroir)** : creuser `-2` à `-4 dB` (`800 Hz–2.5 kHz`) sur Chords/Pad au profit du Lead/Voix.
* **V6 (Ciment Timbral)** : `BUS Music` + `FX Room` unique adapté au `mix_profile`.
* **H1–H3 (Horizontal)** : dériver le motif entre sections par **4 leviers** (octave `±12 st`, densité, LPF, contre-chant écho) ; ponts harmoniques en mes. 4/8 ; arc dynamique réel.

---

## Chapitre 5 — Gate MIDI Multi-Signatures, `_infer_roles()` & Autofix (`midi_autofix.py`)

### 5.1 Seuils Vérifiés (`musicality_metrics.py` & `deep_diagnostics.py`)
`audit_midi_harmony` et `verify_midi_gate` supportent désormais **`time_signature`** (`4/4`, `3/4`, `6/8`, `5/4`, temps forts & blocs 4 mesures dynamiques) et **`phrase_plan`** :
* Si `phrase_plan == "free_ambient"`, `melody_rest_ratio` tolère `[0.0, 0.92]`.
* Si aucune piste `bass` ou `lead` explicite n'existe (piano/cordes solo), **`contrary_or_oblique_ratio` retourne `None`** sans faux négatif.

| Métrique | Consonant (`V >= -0.3`, `T < 0.5`) | Sombre / Tendu (`V < -0.3` ou `T >= 0.5`) |
| :--- | :---: | :---: |
| **`max_pitch` (Lead) / `min_pitch` (808)** | `<= 76` / `>= 31` (`>= 49 Hz`) | **`<= 72–74`** / **`>= 31`** |
| **`strong_beat_chord_tone_ratio`** *(temps forts `num/den`)* | `>= 0.80` | **`>= 0.65`** *(appoggiatures `b6->5`, `b2->1`)* |
| **`minor_2nd_clashes`** / **`sub_collisions`** | `== 0` / `[]` | **`<= 2` / bloc 4 mes.** / `[]` |
| **`stepwise_ratio`** / **`gap_fill_compliance`** | `>= 0.65` / `>= 0.60` | `>= 0.65` / `>= 0.60` |
| **`melody_rest_ratio`** / **`voice_leading_avg_semitones`** | `0.20–0.50` *(0–0.92 ambient)* / `<= 2.50 st` | `0.20–0.50` / `<= 2.50 st` |
| **`lead_overlap_conflict`** / **`contrary_or_oblique_ratio`** | `<= 0.15` / `>= 0.55` *(ou `None`)* | `<= 0.15` / `>= 0.55` *(ou `None`)* |

### 5.2 Nommage `_infer_roles()` & Correction Déterministe (`scripts/midi_autofix.py`)
* **Noms reconnus** : Perc/FX ignorés (`kick, snare, clap, hat, perc, rim, cymbal, crash, tom, shaker, drum, fx, riser`) · `bass` (`808, bass, sub` ou pitch `< 48` hors accords `poly_ratio >= 0.45`) · `lead` (`lead, melody, topline, vox, vocal` ou `"piano"` si `poly_ratio < 0.45`) · `chords` (`chord, pad, string, rhodes, harmony` ou `poly_ratio >= 0.45`).
* **Autofix** : Appeler `verify_midi_gate(..., time_signature=(num, den), phrase_plan=..., auto_fix=True)` ou `autofix_midi(...)`. Corrige en 1 passe + ré-audit : hors-gamme, bornes sub/apex, collisions `< MIDI 48` dans `Chords`, appuis temps forts et frottements `b2` (`fix_report`). Si un défaut structurel persiste (`melody_rest_ratio`, `stepwise`, `contrary`), ajuster le générateur Python.

---

## Chapitre 6 — LEGACY v2.1 : ancien patron `build_song.py` (historique uniquement)

> **LEGACY** : remplacé depuis la v3.2 par `compile_song.py` (double entrée `arrangement_plan.json` /
> `composition.strudel` -> `ScoreIR`). Conservé pour historique ; ne plus l'utiliser en production.

```python
#!/usr/bin/env python3
import sys
sys.path.insert(0, "$AI_STUDIO_DIR/scripts")
from gl_dsl import eq_band, eq_params
from groove_lib import apply_anti_machine_gun, apply_haas_pocket, apply_micro_strum, enforce_frequency_inertia
from song_builder import ChannelSpec, degree_to_midi, optimize_voice_leading, run_song_cli
from transitions_lib import add_transition_fx, apply_harmonic_anticipation, beat4_dropout, generate_drum_fill, outro_fade, section_gain_arc

PROJ_DIR = "$AI_STUDIO_DIR/projects/mon_morceau"
SECTIONS = [("Intro", 0, 8, "intro"), ("Drop1", 8, 16, "chorus"), ("Outro", 24, 8, "outro")]  # start_bar 0-idx

def generate(meta):
    voicings = optimize_voice_leading([[0, 3, 7], [8, 0, 3], [5, 8, 0], [0, 3, 7]], 48, 65)
    tracks = {"kick": [], "808": [], "hihat": [], "chords": [], "lead": []}
    # ... remplir en beats 0-indexed, puis humaniser :
    tracks["chords"] = apply_micro_strum(tracks["chords"], step_ms=15.0, bpm=meta["bpm"])
    tracks["hihat"] = apply_anti_machine_gun(tracks["hihat"])
    tracks["808"] = enforce_frequency_inertia(tracks["808"])
    apply_haas_pocket(tracks, bpm=meta["bpm"], profile=meta["mix_profile"])
    markers = [{"name": s, "position": float(b * meta["beats_per_bar"])} for s, b, _, _ in SECTIONS]
    return tracks, markers

CHANNELS = [
    ChannelSpec("Kick", "kick", 1, "drums", volume=0.72, plugin="gl.kick", sidechain_buses=[(18, 0.8), (19, 0.5)]),
    ChannelSpec("808", "808", 2, "bass", role="bass", valence=-0.6, volume=0.52, plugin="gl.warmsynth"),
    ChannelSpec("Chords", "chords", 3, "music", role="chords", valence=-0.6, volume=0.45, plugin="gl.keys", fx_send=0.14),
    ChannelSpec("Lead", "lead", 4, "music", role="lead", valence=-0.6, volume=0.72, plugin="gl.fm", fx_send=0.16),
]

if __name__ == "__main__":
    run_song_cli(
        proj_dir=PROJ_DIR, out_gridline=f"{PROJ_DIR}/projet.gridline", sections=SECTIONS, channels=CHANNELS,
        generate_fn=generate, bass_keys=["808"], chords_key="chords", lead_key="lead",
        insert_eq_params=eq_params(1, [eq_band(1, "hpf", 30)]) + eq_params(3, [eq_band(1, "hpf", 220), eq_band(4, "peak", 1800, -3.0, q=1.4)]),
    )
```


---

## Phase 6 — Contrat Multi-Échelles ($S5 \rightarrow S1$) & Cohérence Macro ($S4$)

1. **Contrat normatif (`multi_scale_contract.json`)** : généré en **É0** via `scripts/target_fingerprint.py` (ou MCP `audio-analyzer:extract_target_fingerprint`) à partir de `0..3` audio de référence et du schéma `schemas/multi_scale_contract.schema.json`.
2. **Synchronisation $S3$ (Syntaxe & Forme Courte)** : `audit_midi_harmony` / `evaluate_midi_musicality(..., strict_s3=True)` vérifie la cohésion motivique (`A-A'-A''-B`, rejetant clones robotiques `> 0.92` et phrases sans cohésion `< 0.08`), l'arc de tension harmonique sur 4 mesures et l'interlocking Accords/Lead (`chords_lead_attack_overlap <= 0.25`). Corrections mécaniques via `midi_autofix.autofix_midi(..., fix_s3=True)`.
3. **Audit Macro-Structurel $S4$ (`scripts/macro_structure_audit.py`)** :
   - **SSM bar-synchronisée** (`0.35 Chroma CQT + 0.40 MFCC 1..13 + 0.25 Rythme 16 pas`) : tout bloc de 8 mesures cloné (`S_bar8 > 0.94`) déclenche `MUTATE_BLOCK_DENSITY_OR_LPF`.
   - **Nouveauté Checkerboard ($L=4$ mes.)** : chaque transition déclarée dans `song_blueprint.json` doit présenter un pic de nouveauté à `±1` mesure (`REINFORCE_SECTION_TRANSITION`).
   - **Arc Dramatique 5D** `(LUFS, Centroïde, Densité d'onsets, Largeur M/S, Flux cognitifs)` : valide l'évolution de genres sans batterie (`has_drums=false` / `classical_ambient`).
   - **Plafond Cognitif sur stems (`> -28 dBFS` RMS/mesure)** : vérifie le respect des quotas d'arrangement par section (`PRUNE_TRACK_IN_SECTION` si dépassement sur `> 2` mesures consécutives).
4. **Catalogue & EP (`scripts/catalog_coherence.py`)** : sur chaque `É4 PASS`, `render_and_audit.py` exporte `<projet>/renders/master_fingerprint.json` ($\approx 1\text{ Ko}$), audité ensuite en lot par mode `ep|album|dj_set` (homogénéité LUFS `<= 1.5 dB`, pente rose `<= 1.2 dB/oct`, diversité BPM/clé/progression, ordre optimal Camelot).

---

## Phase 7 — Plan Déclaratif, Prosodie (`P2`), Synchro Vocale (`P6`) & Checkpoint É2 (`P5`)

1. **Plan d'Arrangement Déclaratif (`schemas/arrangement_plan.schema.json` + `compile_arrangement_plan`)** :
   - L'agent génère `arrangement_plan.json` (sections, `active_roles`, `role_modifiers`, `transitions`, `motifs`), validé puis compilé de manière déterministe par `song_builder.compile_arrangement_plan()`. Exemple de transition riche : `{"transitions": [{"bar": 9, "type": "drum_fill_and_riser", "style": "snare_roll"}, {"bar": 17, "type": "harmonic_anticipation", "offset_beats": 0.5, "ratio": 0.35}]}`.
   - Permet à `multiscale_autofix.py` (`PRUNE_TRACK_IN_SECTION`, `REINFORCE_SECTION_TRANSITION`, `MUTATE_BLOCK_DENSITY_OR_LPF`) de muter localement le plan JSON et de recompiler sans régénération LLM.
   - **v4.0 — double frontend vers le même ScoreIR** : le LLM peut écrire à la place un `composition.strudel` compact (`1 piste $ = 1 rôle atomique` + `// @strudel-meta`) ; `scripts/strudel_eval.mjs` (Node headless) puis `scripts/strudel_to_score_ir.py` produisent exactement ce même `arrangement_plan.json` (+ `score_ir.json`) avec les garde-fous 0 token, puis `compile_arrangement_plan()` s'applique inchangé. Voir [03_PRODUCTION_SAMPLES_ET_PONT_STRUDEL.md](03_PRODUCTION_SAMPLES_ET_PONT_STRUDEL.md).
2. **Prosodie Instrumentale (`scripts/phrasing_engine.py`)** :
   - `apply_instrumental_prosody()` façonne chaque cycle de 4 mesures (arc de vélocités crescendo→décroissance + courbe d'automation LPF coordonnée), ajoute les notes de passage basse en mesure `4/8` et appoggiatures cadentielles sur le `Lead` (sécurisées par `detect_minor_2nd_clashes`), et décale le micro-pocket de section (`+3.5 ms` couplets / `-2.0 ms` refrains).
3. **Synchronisation Vocale (`scripts/vocal_arrangement_sync.py`)** :
   - En présence d'un fichier `.ustx` ou `samples/vocals.wav`, `extract_vocal_phrases()` isole les phrases chantées, `mute_counter_during_vocals()` restreint les contre-chants aux fenêtres respiratoires `>= 0.5 beat`, et `build_formant_ducking_automation()` automatise un creux `1.85 kHz` (`Band 5 gain` `0 -> -3.5 dB`) sur `BUS Music`.
4. **Checkpoint Humain 15 s (`--checkpoint-e2` / `--resume-from-checkpoint`)** :
   - Exporte `renders/e2_checkpoint_15s.wav` normalisé à `-14 LUFS`, met `pipeline_state.json` en `AWAITING_E2_CHECKPOINT` avec fiche 5 lignes (`<= 300` tokens), puis reprend en `É3/É4` sans recalculer `É1/É2` via le cache SHA-256.


---

# Partie 3 — Audit v3.2 : carences & solutions

# Audit v3.2 — Les 5 Carences Majeures Restantes & Solutions

**Socle vérifié :** v3.1 (6 chantiers intégrés : `mix_config.py`/`config/mix_profiles.json`, `autofix_transaction.py`, `score_ir.py` + clamp 20 ms, `adapt_voicing_to_timbre` + `evaluate_barwise_harmonic_alignment`, `spectral_corridors.json`, `asset_hygiene.py`). Le socle anti-fausse-note / anti-clipping est solide ; ce qui plafonne désormais la qualité **esthétique** est listé ci-dessous.

**Objectif v3.2 :** des morceaux vraiment mélodieux, vivants, cohérents d'un bout à l'autre — en conservant le budget (≤ 3 macro-tours LLM, ≤ 8 000 tokens, ≤ 2 rendus Gridline).

**État d'implémentation (v3.2 — terminé) :** les 5 priorités sont développées, un commit par jalon, et couvertes par la CI documentaire (`tests/test_docs_hygiene.py`) :

| Priorité | Jalon | Commit |
| :---: | :--- | :--- |
| P1 (#5b) | Purge contradictions docs + 3 assertions CI | `2b3b6ae` |
| P2 (#3) | `FullCoverageCoreValidator` + `compile_song --probe` | `38ac66d` |
| P3 (#1) | `NarrativeHarmony` (sections, phrasé 8 mesures, suspensions) | `bd379e0` |
| P4 (#2) | `ExpressionArticulations` (glides 808, vibrato retardé, vélocité→cutoff, ritardando) | `1129f93` |
| P5 (#4 + #5a) | `TimbralCohesion` (wetness/DRR, archétypes `gl.*`, banque grooves MIDI) + `MelodicAppealIndex` (tournoi Tour 2) | `3531038` |

## Feuille de route (ordre de priorité)

| Priorité | Problème | Modules ciblés | Impact audible |
| :---: | :--- | :--- | :--- |
| **P1** | #5b — contradictions doc + CI | `docs/00..05`, `REFERENCE_ARCHIVE.md`, `tests/test_docs_hygiene.py` | Supprime les ordres contradictoires (LPF « mix en U », plafonds) |
| **P2** | #3 — angle mort Tour 2 → Tour 3 | `full_coverage_validator.py`, `compile_song.py --probe`, `stem_matrix_evaluator.py`, `audio_analyzer_server.py` | 100 % des pistes auditées en stems dès le Tour 2 (sonde 12 mesures) |
| **P3** | #1 — narration statique (boucle 4 mesures) | `narrative_harmony.py`, schémas blueprint/plan, `song_builder.py`, `phrasing_engine.py`, `score_ir.py` | Vrais refrains/ponts contrastés, phrasé 8 mesures Q/R, suspensions 4→3/9→8 |
| **P4** | #2 — expressivité mécanique | `score_ir.py` (articulations), `gl_dsl.py`, `song_builder.py`, `transitions_lib.py` | Glides 808, vibrato retardé ≥ 220 ms, vélocité→cutoff, `outro_ritardando` |
| **P5** | #4 + #5a — DRR/espace, archétypes, grooves MIDI, MAI | `audio_metrics.py` (wetness/tail), `palette_builder.py`, `config/timbre_archetypes.json`, `midi_groove_bank.py`, `musicality_metrics.compute_melodic_appeal_index` | Espace sonore unifié (sends pondérés DRR), grooves idiomatiques, sélection active du meilleur motif |

## Les 5 problèmes & solutions

1. **Narration harmonique statique** → `NarrativeHarmony` : `harmonic_core.section_progressions` (verse/pre_chorus/bridge/chorus/outro), phrasé 8 mesures Antécédent/Conséquent (demi-cadence mesure 4, cadence fermée mesure 8), opérateurs motiviques déterministes (`motif_answer`, `rhythmic_lift`, `call_and_response`, `melodic_climax_arch`), suspensions `note_role="suspension"` résolues en 0,5–1,0 beat dans le ScoreIR.
2. **Expressivité mécanique** → `ExpressionArticulations` : `expression.articulation` (`glide_to_next`, `legato`, `swell`...), `glide_ms` 35–90 ms sur 808 (exemption ciblée du coupe-chevauchement), vibrato LFO retardé (0 sur les 220 premières ms, attaque ≤ 6 ct), couplage vélocité→Cutoff, `outro_ritardando` organique (4–8 % sur les 2 dernières mesures).
3. **Angle mort Tour 2/Tour 3** → `FullCoverageCoreValidator` : pré-vol symbolique 100 % du morceau sur ScoreIR dès le Tour 2 (< 100 ms : clones 8 mesures, charge cognitive 1–8 pistes/mesure, arc d'énergie par section, intégrité transitions/outro) + **sonde stems composite 12 mesures** (8 noyau + 4 hors-noyau séparées par 1 mesure de silence) en 1 seul rendu.
4. **Cohérence acoustique des palettes** → `TimbralCohesion` : `wetness_score`/`tail_duration_ms` dans `audio_metrics.py`, terme de cohérence d'espace DRR dans `palette_builder.py`, `fx_send`/`hall_send` pondérés par `(1 - wetness)` dans `song_builder.py`, archétypes `gl.*` calibrés par genre dans `config/timbre_archetypes.json`, banque de grooves extraits des `.mid` locaux via `scripts/midi_groove_bank.py` (jamais de mélodie copiée).
5. **Loi de Goodhart + contradictions doc** → `MelodicAppealIndex` : `compute_melodic_appeal_index(score_ir)` déterministe (< 15 ms, 4 critères positifs : entropie motivique zone d'or, clarté de l'arc de contour, tension→résolution cadentielle mesures 3–4/7→8, complémentarité rythmique lead/basse/counter) + tournoi de 2–3 variantes d'ornementation en Tour 2 (0 rendu audio) ; CI documentaire dans `tests/test_docs_hygiene.py` (0 lien Markdown cassé, 100 % des `scripts/*.py` indexés dans `00_SOMMAIRE.md`, 0 valeur chiffrée divergente vs `config/mix_profiles.json` dans `AGENT_RUNBOOK.md`/`docs/05_*`).

## Règle LPF clarifiée (v3.1+, normative)

Jamais de **LPF brique** (`gl.eq` 24 dB/oct) sous `6.5 kHz` (harmonie) / `9 kHz` (lead) — planchers `source_lpf_floors_hz` de `config/mix_profiles.json`. Pour assombrir un timbre : *high-shelf* doux (−3 dB) ou filtre de synthé musical (pente 6–12 dB/oct). Toute valeur chiffrée de mix/gate est gouvernée exclusivement par `config/mix_profiles.json` via `scripts/mix_config.py`.
