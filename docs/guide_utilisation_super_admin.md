# Guide d'utilisation super-admin

## 1. Rôle du super-admin

Le super-admin pilote l'outil dans sa globalite.

Il peut :

- administrer les utilisateurs,
- rattacher les producteurs aux techniciens,
- gérer les paramètres métier,
- configurer les recommandations et les leviers d'action,
- administrer les pages d'information,
- gérer les accès API et les tokens,
- consulter l'ensemble des données.

Le super-admin a donc un rôle de gouvernance fonctionnelle et technique.

## 2. Accès principaux

Le super-admin utilisé principalement :

- l'interface métier de l'application,
- l'admin Django,
- le CMS Wagtail,
- les exports Excel,
- l'API si necessaire.

## 3. Comptes et rattachements

Le super-admin peut :

- créer un producteur,
- créer un technicien,
- modifier un utilisateur,
- changer le technicien référent d'un producteur,
- corriger les coordonnées et le GPS,
- intervenir sur tous les departements.

Contrairement à un technicien :

- il peut créer un producteur pour n'importe quel technicien,
- il peut modifier les rattachements librement.

## 4. Paramétrage métier

Le super-admin gere, dans l'admin, les listes de reference suivantes :

- cultures,
- types de conduite,
- variétés,
- auxiliaires,
- molécules,
- types d'action,
- motifs de non-suivi,
- pages d'information et ressources.

Ce parametrage doit rester propre, car il structure :

- là saisie,
- les tableaux,
- les exports,
- les recommandations.

## 5. Gestion des cultures

Pour chaque culture, le super-admin peut définir l'indicateur auxiliaire utilisé par le moteur de recommandation.

Cet indicateur peut être :

- `Auxiliaires / plant`
- `Auxiliaires / feuille observée`
- `Auxiliaires / feuille infestee`

Ce choix est important car il change la façon dont les règles de décision sont évaluées.

## 6. Gestion des auxiliaires

Dans l'admin, le super-admin peut :

- créer ou modifier les auxiliaires,
- ajouter une photo,
- indiquer si un auxiliaire est `lâchable`.

Seuls les auxiliaires marques comme `lâchables` peuvent être proposes dans les actions de type :

- `Lâcher d'auxiliaire`

## 7. Gestion des molécules

Les molécules sont parametrables par :

- nom,
- cultures concernees,
- périmètre `Bio`, `Non bio` ou `Bio et non bio`.

Lors d'une saisie d'action :

- les molécules proposees sont filtrees selon la culture de la série,
- et selon son mode de conduite.

## 8. Gestion des types d'action

Le super-admin peut définir ou modifier :

- les types d'action,
- leur categorie,
- leur icône de graphique.

Ces types d'action sont utilises :

- dans là saisie d'action,
- dans les recommandations,
- dans les dashboards,
- dans les historiques d'action.

## 9. Gestion des conseils : règles de décision

La partie la plus sensible du paramétrage concerne les `Règles de décision`.

Ces règles permettent d'afficher un conseil adapté selon la situation observée.

### 9.1. A quoi sert une règle de décision

Une règle de décision permet d'associer à une situation donnée :

- un titre de conseil,
- une description du conseil,
- un ou plusieurs leviers d'action.

Exemple :

- `Infestation moderee : surveiller les plants infestes et envisager un renforcement`

### 9.2. Criteres pris en compte

Une règle peut être definie selon :

- la `culture`,
- la `semaine`,
- le `taux d'infestation`,
- le `niveau d'auxiliaires`.

Le niveau d'auxiliaires est interprete selon l'indicateur configure sur la culture.

### 9.3. Logique des bornes

Les règles suivent cette logique :

- `borne minimale incluse`
- `borne maximale exclue`

Donc :

- `10 <= x < 25`

Si une borne minimale est vide :

- elle est consideree comme `0`

Si une borne maximale est vide :

- elle est consideree comme ouverte vers le haut

Exemples :

- infestation `0 <= x < 3`
- infestation `3 <= x < 10`
- auxiliaires `1 <= x`

### 9.4. Semaine

La plage de semaines permet de limiter un conseil à une période de culture.

Exemple :

- de la semaine `11` à la semaine `25`

### 9.5. Bon paramètre avant creation

Avant de créer une règle, il faut vérifier :

- la bonne culture,
- le bon indicateur auxiliaire sur la culture,
- la logique des seuils,
- l'absence de doublon ou de chevauchement.

### 9.6. Chevauchement des règles

Deux règles actives ne doivent pas se chevaucher pour une même culture.

L'outil bloque l'enregistrement si un chevauchement est detecte entre règles actives.

Le message d'erreur identifie les règles concurrentes.

Ce point est important :

- des règles inactives peuvent se chevaucher,
- mais des règles actives concurrentes rendraient le conseil ambigu.

### 9.7. Règles actives / inactives

Une règle inactive :

- reste en base,
- peut servir de brouillon ou d'archive,
- n'est pas utilisée dans le moteur de conseil.

Une règle active :

- est prise en compte dans les recommandations.

### 9.8. Message si aucune règle ne correspond

Si aucune règle ne matche une situation, l'outil affiche :

- `Situation anormale, vous pouvez appeler votre technicien.`

Il est donc utile de couvrir correctement les principaux cas terrain.

## 10. Gestion des conseils : leviers de décision

Les `Leviers de décision` sont rattachés aux règles.

Ils correspondent aux actions proposees à l'utilisateur.

Pour chaque levier, le super-admin peut définir :

- un titre,
- une description,
- un type d'action preconfigure,
- une portee preconfiguree,
- une molécule optionnelle,
- un auxiliaire optionnel,
- un texte de details pré-rempli.

### 10.1. Usage

Quand un utilisateur clique sur un levier recommande :

- le formulaire d'action s'ouvre,
- les champs sont pre-remplis selon le levier.

### 10.2. Plusieurs leviers

Une même règle peut comporter plusieurs leviers.

Cela permet de proposer plusieurs options de gestion selon la situation.

## 11. Conseils et suivi des recommandations

Le super-admin doit aussi surveiller :

- les recommandations produites,
- les recommandations suivies,
- les recommandations non suivies,
- les motifs de non-suivi.

Les motifs de non-suivi sont paramètres dans l'admin.

Exemples :

- `Plus besoin d'agir`
- `Pas envie de mobiliser ce levier`
- `Autre`

Le motif `Autre` ouvre un texte libre.

## 12. Recommandations : bonnes pratiques de parametrage

Pour garder un systeme robuste :

- ne pas multiplier inutilement les règles,
- garder une logique simple par culture,
- documenter les seuils utilises,
- tester les cas limites,
- vérifier les conséquences d'une borne ouverte,
- désactiver une règle obsolète plutôt que l'effacer tout de suite,
- vérifier les chevauchements avant mise en production.

## 13. Pages d'information et CMS

Le super-admin peut administrer les contenus d'information via le CMS Wagtail.

Pages typiques :

- protocole,
- techniques de lutte,
- auxiliaires,
- financeurs,
- footer.

Selon les pages ou reglages, il est possible d'ajouter :

- texte,
- logo,
- image,
- document PDF,
- lien externe.

## 14. Gestion des financeurs et du footer

Les blocs `Financeurs` et `Footer` sont paramètres via le CMS / les reglages de site.

Le super-admin peut y ajouter :

- du texte,
- des logos,
- des liens si necessaire.

Ces informations sont reutilisees sur les pages publiques ou institutionnelles de l'outil.

## 15. API et tokens

Le super-admin peut gérer les tokens d'API.

Cette fonction sert notamment a :

- connecter l'outil a n8n,
- lire ou pousser des données,
- automatiser certains traitements.

Les tokens doivent être réservés a des usages identifiés.

Bonnes pratiques :

- créer un token par usage,
- nommer clairement les tokens,
- supprimer les tokens inutiles,
- ne jamais diffuser un token en clair dans un document partage.

## 16. Dashboard et vues globales

Le super-admin peut consulter :

- les dashboards,
- les données de tous les producteurs,
- les vues technicien,
- les exports globaux.

Il peut donc vérifier :

- la qualité de là saisie,
- la cohérence des séries,
- la dynamique de groupe,
- l'application pratique des recommandations.

## 17. Imports et qualité de données

Avant un import CSV de producteurs, vérifier :

- l'orthographe des colonnes,
- la presence du bon technicien référent,
- les mails,
- les mobiles,
- les adresses,
- la cohérence du département.

Après import :

- vérifier quelques comptes,
- vérifier les rattachements,
- vérifier la géolocalisation si besoin.

## 18. Checks à faire avant ouverture aux utilisateurs

Avant de diffuser l'outil à un groupe :

1. vérifier les cultures,
2. vérifier les variétés,
3. vérifier les auxiliaires et leurs photos,
4. vérifier les molécules,
5. vérifier les types d'action,
6. vérifier les motifs de non-suivi,
7. vérifier les règles de décision,
8. vérifier les leviers associés,
9. vérifier les pages d'information,
10. vérifier les comptes techniciens,
11. vérifier quelques comptes producteurs.

## 19. Strategie de maintenance

Je te recommande de distinguer trois niveaux :

- `parametrage stable`
  - cultures
  - types de conduite
  - types d'action
- `parametrage saisonnier`
  - variétés
  - séries
  - campagnes annuelles
- `parametrage conseil`
  - règles de décision
  - leviers
  - molécules

Cette separation simplifie la maintenance.

## 20. Besoin d'aide

Si un comportement semble incohérent, vérifier d'abord :

- la culture de la série,
- l'année,
- la semaine,
- l'indicateur auxiliaire configure sur la culture,
- les bornes des règles,
- l'etat actif / inactif des règles,
- les chevauchements entre règles actives.

Dans la plupart des cas, un probleme de conseil vient d'un parametrage de seuil ou d'un chevauchement logique entre règles.
