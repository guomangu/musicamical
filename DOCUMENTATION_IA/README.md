# DOCUMENTATION_IA — tout ce qu'il faut pour produire un morceau de qualité (et rien d'autre)

Ce dossier est **la seule lecture nécessaire pour générer un morceau** avec ce framework : **2 documents**.
Il remplace la lecture des 5 documents `docs/00`–`04` (réservés au développement du projet).

## Les 2 documents

| Document | Quand le lire | Contenu |
|---|---|---|
| [01_PRODUIRE_UN_MORCEAU_DE_QUALITE.md](01_PRODUIRE_UN_MORCEAU_DE_QUALITE.md) | **Toujours** — c'est le guide complet | Partie 1 : composer avec Strudel v4.1 (`@meta`, prélude, exemple canonique testé, pre-flight du gate, tables degrés/b2-safe/apex/`slow(N)`, recettes anti-échec) · Partie 2 : produire via la façade `ai-studio-prod` (3 outils, scénarios A–D, retours/handoff) · Partie 3 : diagnostic « sons désagréables » + solutions câblées · **Partie 4 : possibilités & bonnes pratiques pour un son de qualité** (choix des sons/samples, gate SWAP, volumes, master, fiabilité opérationnelle) |
| `README.md` (ce fichier) | En premier | Workflow global + règles absolues + liens |

## Workflow global (Scénario A, création originale — le plus courant)

1. `studio_compose(project_dir, strudel_code, auto_prepare=true)` — **1 seul appel** : le code Strudel est évalué headless (Node pur), converti en `ScoreIR` + garde-fous 0 token, blueprint auto-valide, **palette résolue automatiquement (vrais samples branchés)**, rendu master si Gridline présent.
2. Lire le retour (≤400 cars) + `<projet>/handoff.txt` : `preflight=PASS/FAIL gf=… sw=… rest=…` et **`palette=… samples=N`** (N > 0 attendu — sinon voir Partie 4.D : serveur périmé → redémarrer la session agent).
3. Si `preflight=FAIL` : corriger le motif dans `composition.strudel` (Partie 1), pré-vérifier en 0 rendu avec `scripts/preflight_strudel_gate.py <projet>`, puis re-composer.
4. Retouche fine : `studio_refine(project_dir, semantic_patch={"lead": {"oct": -1, "rests": "+15%"}})` (~20–40 tokens).
5. `studio_prepare` ne sert que pour les scénarios B (samples verrouillés), C (remix), D (chant) — voir Partie 2.

## Règles absolues

1. `1 piste $ = 1 rôle atomique` (`kit()` émet `$kick/$snare/$hihat/$perc` seuls).
2. **Jamais de noms de notes bruts** (`c2`, `f1`…) : uniquement `grid(...)` / `deg(...)` / `bassline(...)`.
3. Toujours un en-tête `// @meta … form=I4-V8-C8-O4` + présence par section (`.in()`/`.only()`/`.muteIn()`), `V=0.0`.
4. Production = serveur MCP `ai-studio-prod` **seul** (3 outils, < 1 800 tokens combinés) ; jamais les 5 serveurs bas niveau.
5. Erreur Strudel : ≤ 2 lignes `ERR L..:..` du lint → corriger et relancer ; pas de stacktrace.
6. **Après chaque `git pull` / `install.sh` : redémarrer la session agent** (les serveurs MCP chargent le code au démarrage) — sinon `palette: null` / pistes 100 % `gl.*` / alerte `RESTART session agent`.

## Pour aller plus loin (développement uniquement)

`docs/` (5 documents numérotés `00`–`04`) reste la référence développement : [docs/00_SOMMAIRE.md](../docs/00_SOMMAIRE.md) (index complet), [docs/03_PRODUCTION_SAMPLES_ET_PONT_STRUDEL.md](../docs/03_PRODUCTION_SAMPLES_ET_PONT_STRUDEL.md) (pont Strudel + guide conception détaillé + Audio Intelligence v2), [docs/04_MIXAGE_QUALITY_GATE_ET_APPRENTISSAGE.md](../docs/04_MIXAGE_QUALITY_GATE_ET_APPRENTISSAGE.md) (mixage/quality gate), [docs/AGENT_RUNBOOK_V4.md](../docs/AGENT_RUNBOOK_V4.md) (runbook tokenisé ≤ 750 tokens).
