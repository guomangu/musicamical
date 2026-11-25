# 08 — Audio Intelligence v2 : qualité physique, tagging psychoacoustique & agencement cohérent

Intègre le pack de spécification « MIR v2 » dans l'idiome du projet (**Python in-process,
décodage unique**), sans rien casser : le Node reste réservé au pont Strudel headless.

## 1. Pourquoi pas le stack v1 tel quel (audit)

| Composant v1 | Défaut | Équivalent v2 retenu dans le projet |
|---|---|---|
| CLI spawning (sox, aubio, ffmpeg) | fork/exec + triple décodage disque par fichier | **décode once** via libsndfile (`soundfile`) en mémoire, zéro subprocess (`sample_quality_v2.decode_once`) |
| node-wav (readFileSync, 24/32-bit instable) | OOM / rejets silencieux HD | soundfile/librosa lisent WAV 16/24/32-bit float, FLAC, OGG nativement |
| meyda sur main thread | bloque l'event loop, pas de HPSS | DSP Python côté `ProcessPoolExecutor` (déjà utilisé par l'indexeur) |
| essentia.js WASM brut | coût init WASM, fuites VectorFloat | `librosa.decompose.hpss` (HPSS), `librosa.pyin` (F0 10 ms ≈ CREPE), numpy FFT |
| libebur128 (RPM) | dépendance système | `pyloudnorm` (BS.1770) déjà embarqué + **true peak x4** maison (`resample_poly`) |
| onnxruntime-node CLAP/CREPE | modèles lourds à télécharger | CLAP **optionnel** (déjà supporté via `ClapEmbedder`) ; repli déterministe = embedding DSP (Bark24 + métriques) ; CREPE ≈ `librosa.pyin` |
| sqlite-vec (npm natif) | build natif risqué | recherche vectorielle **numpy** sur `semantic_anchors.npz` (déjà en place, < 2 ms) + filtres SQL stricts dans `samples.db` |
| piscina workers | pool Node | `ProcessPoolExecutor` Python (indexeur, workers=4) |

## 2. Quality Gate physique (jalon 1) — `scripts/sample_quality_v2.py`

`quality_gate_v2(x, sr)` (numpy/scipy, cible < 15 ms/fichier) -> `PASS` / `REJECTED` + défauts :

| Défaut | Détection | Seuil |
|---|---|---|
| `CLIPPED` | True Peak par suréchantillonnage x4 (pics inter-échantillons) OU échantillons saturés | TP > +0.5 dBTP ou >= 3 consécutifs à 0.999 |
| `UPSAMPLED_LOSSY` | coupure brique spectrale (bins 500 Hz) sur source large bande | coupure à ~11 kHz ou ~16 kHz (sr >= 44,1 kHz) |
| `NOISY_FLOOR` | SNR spectral (pics tonaux vs médiane du spectre), gardé si fraction tonale >= 2 % | SNR < 30 dB |
| `DC_OFFSET` | moyenne du signal | > 0.005 |

Ingestion : `decode_once` (libsndfile) -> `remove_dc_offset` -> `smart_trim` (seuil -65 dBFS,
pré-roll 2 ms pour préserver l'attaque). Les samples `REJECTED` sont exclus de la sélection
(`select_sample.py` joint `sample_quality_v2`) et marqués `hard_reject` côté DSP.

## 3. Profilage morphologique (jalon 2) — `profile_morphology`

- Contour `F0(t)` trame 10 ms (`librosa.pyin`) + pic spectral des 80 premières ms pour le glide.
- **Signature 808** : `F0_tail` (médiane des 30 % voiced finaux) dans [28, 68] Hz ET
  `ΔF0 = F0_head - F0_tail > 35 Hz` -> tag `808` (+ `sub`).
- **T30** (enveloppe RMS lissée, pic -> -30 dB) : `< 0.30 s` percussive_short, `< 1.10 s` medium, sinon `long_sustain`.
- **Saturation** : crest factor temporel `CF = 20 log10(max|x|/rms(x))` + ratio d'énergie harmonique
  HPSS dans [2.5, 8] x F0 : `CF < 6.5 dB` + (rolloff85 > 350 Hz ou ratio > 0.15) + `F0_tail < 65 Hz`
  -> `timbre:saturated_distorted` ; `CF >= 9.5 dB` + rolloff < 160 Hz -> `timbre:clean_sub`.
- Sortie : rôle (`808`/`bass`/`kick`/`snare`/`perc`/`hihat`/`synth`), `pitch_midi`, `pitch_confidence`,
  `decay_t30_ms`, `crest_factor_db`, rolloff/centroid, **bark_24** (24 bandes normalisées), tags.

Jalon 2 vérifié en CI (`tests/test_sample_quality_v2.py` + `--selftest`) : 808 synthétique
(120 -> 45 Hz en 50 ms, T30 ~1.6 s, tanh CF ~5 dB) -> tags `[808, long_sustain, saturated_distorted]`,
`pitch_midi = 29 ± 1`.

## 4. Agencement cohérent (jalons 3 & 4)

- **Anti-masquage** (`spectral_collision`, `suggest_hpf_hz`) : `C(A,B) = Σ w_b E_A(b) E_B(b)` sur les
  24 bandes Bark, poids x4 sous 500 Hz ; HPF suggéré à la fréquence de croisement. La sélection
  applique déjà `compute_masking_penalty(bark_cand, bark_mix)` quand un mix de référence est fourni.
- **Phase Kick/808** (`phase_alignment`) : corrélation croisée bande [30, 100] Hz sur les 50
  premières ms -> `none` / `invert_polarity` / `delay_ms < 8 ms`, avec garantie
  `rms_bande(somme) >= max(rms_kick, rms_bass)` (pas d'annulation). Câblé dans
  `audit_project_sources.py` (`kick_bass_phase` + warning `PHASE_KICK_BASS`) ; les autofixes
  `INVERT_808_POLARITY` / `SHIFT_BASS_MICRO_MS` restent les bras armés côté rendu.
- **Sélection hybride SQL + vecteur** : filtres durs SQL (`quality_v2.status != 'REJECTED'`,
  `hard_reject = 0`, bornes de rôle) + proximité vectorielle (CLAP si indexé, sinon npz DSP),
  pénalité de masquage Bark24 vs mix actif.

## 5. Câblage indexeur

`sample_quality_v2` (table SQLite, cascade `ON DELETE CASCADE`) peuplée à l'indexation par les
workers (`populate_dsp_metrics`) ; rapport : `q2_pass` / `q2_rejected` ; purge des fichiers
supprimés incluse. CLI : `python3 scripts/sample_quality_v2.py <fichier.wav>` et `--selftest`.

## 6. Tests

`tests/test_sample_quality_v2.py` : jalons 1-4 synthétiques (4 fixtures qualité, 808, collision,
phase) + câblage indexeur/sélection (REJECTED exclu). Budget gate < 50 ms/fichier en CI
(cible spec : 15 ms).
