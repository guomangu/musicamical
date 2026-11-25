# AGENT RUNBOOK V4.1 — 2 tours (1 si auto_prepare), préfixe 100 % statique (prompt caching)

Règles de production v4.x. Archive : [REFERENCE_ARCHIVE.md](REFERENCE_ARCHIVE.md) · pont : [06_PONT_STRUDEL_HEADLESS.md](06_PONT_STRUDEL_HEADLESS.md) · RETEX : [RETEX_SESSION_RAP_FR_2026-11-25.md](RETEX_SESSION_RAP_FR_2026-11-25.md).

## Flux
1. `studio_prepare(project_dir, intent)` — **Scénarios B/C/D seuls** (`remix`, `lyrics`). Sans `Samples/` : étape 2 directe, `auto_prepare=true` (palette `auto` = synthés `gl.*`, 0 token). Le `CTX` du retour est une **valeur par défaut à surcharger** dans `@meta`.
2. `studio_compose(project_dir, strudel_code, palette_id="auto", auto_master=true, auto_prepare=true)` → eval Node headless → `ScoreIR` + garde-fous 0 token → blueprint valide auto → master. Retouche : `studio_refine(..., semantic_patch={"lead": {"oct": -1, "rests": "+15%"}})` ou `strudel_patch={"replace_track":"lead","code":"..."}`.

## Contrat `composition.strudel` (v4.1)
- En-tête **1 ligne** : `// @meta 140 C:minor trap_rap V=-0.6 form=I4-V8-C8-O4 [seed=1337] [ts=6/8]` (I=intro V=verse P=pre_chorus C=chorus B=bridge O=outro D=drop ; `seed` sinon = SHA-256 du source ; `ts` défaut 4/4).
- Prélude : `grid("Cm7 ...")` (1 symbole = 1 mesure), `deg("0 ~ [2 3] 4", oct)`, `bassline("<1 ~ 1 ~>", {mode:"root|stepwise", glide:40})` (registre `[31,48]` à la source), `kit("trap_half", {var:2, hats:"rolls"})` (émet `$kick/$snare/$hihat/$perc` séparés), `.in("verse,chorus")`/`.only()`/`.muteIn("intro,outro")` (présence par section), `track()`, `glide(ms)`.
- **Jamais de noms de notes bruts** (`c2`, `f1`…) : `grid`/`deg`/`bassline` seuls.

## Exemple canonique (recopier tel quel — 0 correction de garde-fou, testé en CI)
```js
// @meta 140 C:minor trap_rap V=-0.6 form=I4-V8-C8-O4
kit("trap_half", {var: 2, hats: "rolls"})
$chords: grid("Cm7 Abmaj7 Fm7 G7").slow(2).gain(0.7).muteIn("intro,outro")
$bass: bassline("<1 ~ 1 ~>", {mode: "root", glide: 40}).slow(4).in("verse,chorus,outro")
$lead: deg("0 ~ 2 ~ 4 ~ 3 ~ 5 ~ 4 ~ 2 ~", 4).slow(2).gain(0.6).sometimesBy(0.25, x => x.degradeBy(0.5)).in("verse,chorus")
```

## Anti-clone & gate MIDI (1er coup)
- Variations seedées par piste (`.sometimesBy(0.2-0.3, x => x.degradeBy(0.5))`) ; cycles bass/lead de **16 mesures** (`.slow(4)`/`.slow(2)`), jamais 4 mesures statiques (`clone_8bar`). Lead : saut ≥ 5 st résolu en sens inverse, 20–50 % de `~` ; basse en sens contraire.
- Durée : `bars = round(180 * bpm / 240)` (90 BPM → 68 mes. ≈ 3 min).

## 5 règles dures
1. `1 piste $ = 1 rôle atomique` (`kit()` émet les pistes atomiques seul).
2. Jamais de noms de notes bruts.
3. Toujours `@meta` avec `form=` + `.in()`/`.muteIn()` par piste.
4. Production = `ai-studio-prod` seul (3 outils) ; jamais les 5 serveurs bas niveau.
5. Erreur Strudel : ≤ 2 lignes `ERR L..:..` du lint, corriger, pas de stacktrace.

