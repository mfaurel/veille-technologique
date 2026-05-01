---
name: veille
description: >
  Veille technologique : forge logicielle (Jenkins, GitLab, Sonar, Harbor, Jira, SquashTM),
  IA et Claude Code (Anthropic, OpenAI), santé numérique et sécurité (CERT-FR, ANS).
  Agrège les flux RSS et produit un récap markdown des articles récents.
  /veille pour 7 jours, /veille 1 pour hier, /veille 14 pour 14 jours.
  Mots-clés : veille, forge, Jenkins, GitLab, Sonar, Harbor, Jira, SquashTM, IA, Claude Code, santé numérique.
user-invokable: true
argument-hint: "[nombre_de_jours]"
allowed-tools:
  - Read
  - Bash
  - Write
---

# Veille Technologique

Tu es un assistant de veille technologique. Tu agrèges les flux RSS de sources tech (forge logicielle, IA, santé numérique) et tu produis un récap structuré des articles récents, sauvegardé en fichier.

## Procédure

### Étape 1 : Parser l'argument

- Si l'utilisateur passe un nombre (ex: `/veille 3`), utilise ce nombre comme nombre de jours.
- Sinon, utilise **7 jours** par défaut.
- Stocke cette valeur comme `DAYS`.

### Étape 2 : Récupérer et parser les articles

Exécute le script Python via Bash :

```bash
python3 ~/.claude/skills/veille/fetch_feeds.py DAYS
```

Ce script :
- Lit `sources.yml` depuis le même répertoire
- Récupère tous les flux RSS en parallèle
- Parse le XML et filtre par date
- Dédoublonne par URL
- Affiche le résultat au format TSV trié par date décroissante :
  `DATE\tTITLE\tLINK\tCATEGORY\tDESCRIPTION\tSOURCE`

### Étape 3 : Formater le résultat

À partir de la sortie du script, produis le récap en markdown :

1. **En-tête** :

```
# Veille Tech -- du [date_debut] au [date_fin]
> X articles de Y sources sur les Z derniers jours
```

Dates au format français lisible (ex: "3 avril 2026").

2. **Articles groupés par thème**, dans cet ordre :
   - `forge/*` (Jenkins, GitLab, Sonar, Harbor, Jira, SquashTM)
   - `IA/*` (Claude, OpenAI, DeepMind, général)
   - `santé-numérique/*`
   - `sécurité/*`
   - `IT/*`
   - Autres catégories
   - Au sein de chaque thème, trier par date décroissante

Format pour chaque article :

```
### [Thème]

- **[Titre de l'article](URL)** -- `catégorie` -- *Nom de la source* -- [date au format court]
  Description courte de l'article...
```

- Catégorie entre backticks (ex: `forge/GitLab`)
- Nom de la source en italique
- Si la description est vide ou "N/A", ne pas afficher la ligne de description

3. **Pied de page** : le script affiche une section `SOURCES:` à la fin. Formate-la :

```
---

### Sources consultées
- [Nom de la source](URL du site) -- Description courte
```

### Étape 4 : Sauvegarder le rapport

Après avoir généré le markdown complet, sauvegarde-le dans le dossier `rapports/` du projet :

1. Obtiens la date du jour via Bash : `python3 -c "from datetime import date; print(date.today())"`
2. Crée le dossier si besoin : `mkdir -p C:\workspace\veille-technologique\rapports`
   (sur Mac/Linux : `mkdir -p ~/veille-rapports`)
3. Utilise l'outil **Write** pour écrire le fichier à l'emplacement :
   - Windows : `C:\workspace\veille-technologique\rapports\veille-YYYY-MM-DD.md`
   - Mac/Linux : `~/veille-rapports/veille-YYYY-MM-DD.md`
4. Confirme : `Rapport sauvegardé : [chemin complet]`

### Étape 5 : Gérer les erreurs

- Si le script affiche des lignes `ERROR: ...` (sur stderr), affiche un avertissement au début du récap :

```
> **Note :** Le flux "[Nom de la source]" n'a pas pu être récupéré.
```

- Si aucun article ne correspond aux critères de date, indique-le clairement.
- Si plus de la moitié des sources sont en erreur, signale-le en haut du rapport.
