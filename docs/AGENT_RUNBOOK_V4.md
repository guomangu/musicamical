# AGENT RUNBOOK V4 — 2 tours, préfixe 100 % statique (prompt caching)

Seul fichier de règles en production v4.0 (ne JAMAIS y injecter de données projet : tout le
variable va dans les messages utilisateur). Archive : [REFERENCE_ARCHIVE.md](REFERENCE_ARCHIVE.md).
Détail pont : [06_PONT_STRUDEL_HEADLESS.md](06_PONT_STRUDEL_HEADLESS.md).

## Flux (2 appels LLM)

1. `ai-studio-prod:studio_prepare(project_dir, intent)` → `CTX|PALETTES|GROOVES` (≤ 450 cars).
2. `ai-studio-prod:studio_compose(project_dir, strudel_code, palette_id="auto", auto_master=true)`
   → écrit `composition.strudel`, évalue Node headless, compile `ScoreIR` + garde-fous 0 token,
   chaine master + `handoff.txt`. Retouche : `studio_refine(project_dir, delta_json=...)` ou
   `strudel_patch={"replace_track":"lead","code":"..."}` — quasi gratuit, stateless via `handoff.txt`.

## Exemple canonique (22 lignes, à recopier tel quel)

```js
// @strudel-meta {"bpm": 140, "key": "C", "mode": "minor", "time_signature": [4, 4], "seed": 42,
//   "mix_profile": "trap_rap", "valence": -0.6,
//   "sections": [{"name": "intro", "bars": 4}, {"name": "verse", "bars": 8},
//                {"name": "chorus", "bars": 8}, {"name": "outro", "bars": 4}]}
$chords: grid("Cm7 Abmaj7 Fm7 G7").slow(2).gain(0.7)
$bass: track("bass", note("<c2 c2 [ab1 g1] f1>").lpf(280))
$kick: track("kick", s("bd ~ ~ ~").gain(0.95))
$snare: track("snare", s("~ ~ cp ~").gain(0.8))
$hihat: track("hihat", s("hh*8").gain(0.45).every(4, x => x.degradeBy(0.3)))
$lead: deg("0 ~ [2 3] 4 ~ 5 4 [3 2] ~", 5).glide(60).gain(0.6).lpf(sine.range(1200, 4200).slow(8))
```

(22 lignes utiles : meta 5 + 6 pistes + commentaires.)

## 5 règles dures non-automatisables

1. `1 piste $ = 1 rôle atomique` (jamais 2 percussions sur le même canal).
2. Harmonie en symboles (`grid`/`chord`), mélodie en degrés (`deg`) — jamais de noms de notes bruts.
3. Toujours `// @strudel-meta` avec `seed` (déterminisme) et `sections` (macro-forme).
4. Ne jamais appeler les 5 serveurs bas niveau en production (profil Zed = `ai-studio-prod` seul).
5. En cas d'erreur Strudel : lire les ≤ 2 lignes `ERR L..:..` du lint, corriger, ne pas lire de stacktrace.

## Garde-fous actifs en local (0 token, ne pas les contourner)

Registre 808 `[31,48]`, apex Lead `<= 74`, voice leading + LIL, b2 Lead/accord, clamp 20 ms,
`FullCoverageCoreValidator`, lint ≤ 2 lignes, `handoff.txt` ≤ 350 tokens, solveur de mix
`math_mix_solver.py` (< 0.8 s, 0 rendu supplémentaire) après le 1er rendu stems.
