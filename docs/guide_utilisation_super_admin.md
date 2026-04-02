# Guide d'utilisation super-admin

## 1. Role du super-admin

Le super-admin pilote l'outil dans sa globalite.

Il peut :

- administrer les utilisateurs,
- rattacher les producteurs aux techniciens,
- gerer les parametres metier,
- configurer les recommandations et les leviers d'action,
- administrer les pages d'information,
- gerer les acces API et les tokens,
- consulter l'ensemble des donnees.

Le super-admin a donc un role de gouvernance fonctionnelle et technique.

## 2. Acces principaux

Le super-admin utilise principalement :

- l'interface metier de l'application,
- l'admin Django,
- le CMS Wagtail,
- les exports Excel,
- l'API si necessaire.

## 3. Comptes et rattachements

Le super-admin peut :

- creer un producteur,
- creer un technicien,
- modifier un utilisateur,
- changer le technicien referent d'un producteur,
- corriger les coordonnees et le GPS,
- intervenir sur tous les departements.

Contrairement a un technicien :

- il peut creer un producteur pour n'importe quel technicien,
- il peut modifier les rattachements librement.

## 4. Parametrage metier

Le super-admin gere, dans l'admin, les listes de reference suivantes :

- cultures,
- types de conduite,
- varietes,
- auxiliaires,
- molecules,
- types d'action,
- motifs de non-suivi,
- pages d'information et ressources.

Ce parametrage doit rester propre, car il structure :

- la saisie,
- les tableaux,
- les exports,
- les recommandations.

## 5. Gestion des cultures

Pour chaque culture, le super-admin peut definir l'indicateur auxiliaire utilise par le moteur de recommandation.

Cet indicateur peut etre :

- `Auxiliaires / plant`
- `Auxiliaires / feuille observee`
- `Auxiliaires / feuille infestee`

Ce choix est important car il change la facon dont les regles de decision sont evaluees.

## 6. Gestion des auxiliaires

Dans l'admin, le super-admin peut :

- creer ou modifier les auxiliaires,
- ajouter une photo,
- indiquer si un auxiliaire est `lachable`.

Seuls les auxiliaires marques comme `lachables` peuvent etre proposes dans les actions de type :

- `Lacher d'auxiliaire`

## 7. Gestion des molecules

Les molecules sont parametrables par :

- nom,
- cultures concernees,
- perimetre `Bio`, `Non bio` ou `Bio et non bio`.

Lors d'une saisie d'action :

- les molecules proposees sont filtrees selon la culture de la serie,
- et selon son mode de conduite.

## 8. Gestion des types d'action

Le super-admin peut definir ou modifier :

- les types d'action,
- leur categorie,
- leur icone de graphique.

Ces types d'action sont utilises :

- dans la saisie d'action,
- dans les recommandations,
- dans les dashboards,
- dans les historiques d'action.

## 9. Gestion des conseils : regles de decision

La partie la plus sensible du parametrage concerne les `Regles de decision`.

Ces regles permettent d'afficher un conseil adapte selon la situation observee.

### 9.1. A quoi sert une regle de decision

Une regle de decision permet d'associer a une situation donnee :

- un titre de conseil,
- une description du conseil,
- un ou plusieurs leviers d'action.

Exemple :

- `Infestation moderee : surveiller les plants infestes et envisager un renforcement`

### 9.2. Criteres pris en compte

Une regle peut etre definie selon :

- la `culture`,
- la `semaine`,
- le `taux d'infestation`,
- le `niveau d'auxiliaires`.

Le niveau d'auxiliaires est interprete selon l'indicateur configure sur la culture.

### 9.3. Logique des bornes

Les regles suivent cette logique :

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

La plage de semaines permet de limiter un conseil a une periode de culture.

Exemple :

- de la semaine `11` a la semaine `25`

### 9.5. Bon parametre avant creation

Avant de creer une regle, il faut verifier :

- la bonne culture,
- le bon indicateur auxiliaire sur la culture,
- la logique des seuils,
- l'absence de doublon ou de chevauchement.

### 9.6. Chevauchement des regles

Deux regles actives ne doivent pas se chevaucher pour une meme culture.

L'outil bloque l'enregistrement si un chevauchement est detecte entre regles actives.

Le message d'erreur identifie les regles concurrentes.

Ce point est important :

- des regles inactives peuvent se chevaucher,
- mais des regles actives concurrentes rendraient le conseil ambigu.

### 9.7. Regles actives / inactives

Une regle inactive :

- reste en base,
- peut servir de brouillon ou d'archive,
- n'est pas utilisee dans le moteur de conseil.

Une regle active :

- est prise en compte dans les recommandations.

### 9.8. Message si aucune regle ne correspond

Si aucune regle ne matche une situation, l'outil affiche :

- `Situation anormale, vous pouvez appeler votre technicien.`

Il est donc utile de couvrir correctement les principaux cas terrain.

## 10. Gestion des conseils : leviers de decision

Les `Leviers de decision` sont rattaches aux regles.

Ils correspondent aux actions proposees a l'utilisateur.

Pour chaque levier, le super-admin peut definir :

- un titre,
- une description,
- un type d'action preconfigure,
- une portee preconfiguree,
- une molecule optionnelle,
- un auxiliaire optionnel,
- un texte de details pre-rempli.

### 10.1. Usage

Quand un utilisateur clique sur un levier recommande :

- le formulaire d'action s'ouvre,
- les champs sont pre-remplis selon le levier.

### 10.2. Plusieurs leviers

Une meme regle peut comporter plusieurs leviers.

Cela permet de proposer plusieurs options de gestion selon la situation.

## 11. Conseils et suivi des recommandations

Le super-admin doit aussi surveiller :

- les recommandations produites,
- les recommandations suivies,
- les recommandations non suivies,
- les motifs de non-suivi.

Les motifs de non-suivi sont parametres dans l'admin.

Exemples :

- `Plus besoin d'agir`
- `Pas envie de mobiliser ce levier`
- `Autre`

Le motif `Autre` ouvre un texte libre.

## 12. Recommandations : bonnes pratiques de parametrage

Pour garder un systeme robuste :

- ne pas multiplier inutilement les regles,
- garder une logique simple par culture,
- documenter les seuils utilises,
- tester les cas limites,
- verifier les consequences d'une borne ouverte,
- desactiver une regle obsolete plutot que l'effacer tout de suite,
- verifier les chevauchements avant mise en production.

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

Les blocs `Financeurs` et `Footer` sont parametres via le CMS / les reglages de site.

Le super-admin peut y ajouter :

- du texte,
- des logos,
- des liens si necessaire.

Ces informations sont reutilisees sur les pages publiques ou institutionnelles de l'outil.

## 15. API et tokens

Le super-admin peut gerer les tokens d'API.

Cette fonction sert notamment a :

- connecter l'outil a n8n,
- lire ou pousser des donnees,
- automatiser certains traitements.

Les tokens doivent etre reserves a des usages identifies.

Bonnes pratiques :

- creer un token par usage,
- nommer clairement les tokens,
- supprimer les tokens inutiles,
- ne jamais diffuser un token en clair dans un document partage.

## 16. Dashboard et vues globales

Le super-admin peut consulter :

- les dashboards,
- les donnees de tous les producteurs,
- les vues technicien,
- les exports globaux.

Il peut donc verifier :

- la qualite de la saisie,
- la coherence des series,
- la dynamique de groupe,
- l'application pratique des recommandations.

## 17. Imports et qualite de donnees

Avant un import CSV de producteurs, verifier :

- l'orthographe des colonnes,
- la presence du bon technicien referent,
- les mails,
- les mobiles,
- les adresses,
- la coherence du departement.

Apres import :

- verifier quelques comptes,
- verifier les rattachements,
- verifier la geolocalisation si besoin.

## 18. Checks a faire avant ouverture aux utilisateurs

Avant de diffuser l'outil a un groupe :

1. verifier les cultures,
2. verifier les varietes,
3. verifier les auxiliaires et leurs photos,
4. verifier les molecules,
5. verifier les types d'action,
6. verifier les motifs de non-suivi,
7. verifier les regles de decision,
8. verifier les leviers associes,
9. verifier les pages d'information,
10. verifier les comptes techniciens,
11. verifier quelques comptes producteurs.

## 19. Strategie de maintenance

Je te recommande de distinguer trois niveaux :

- `parametrage stable`
  - cultures
  - types de conduite
  - types d'action
- `parametrage saisonnier`
  - varietes
  - series
  - campagnes annuelles
- `parametrage conseil`
  - regles de decision
  - leviers
  - molecules

Cette separation simplifie la maintenance.

## 20. Besoin d'aide

Si un comportement semble incoherent, verifier d'abord :

- la culture de la serie,
- l'annee,
- la semaine,
- l'indicateur auxiliaire configure sur la culture,
- les bornes des regles,
- l'etat actif / inactif des regles,
- les chevauchements entre regles actives.

Dans la plupart des cas, un probleme de conseil vient d'un parametrage de seuil ou d'un chevauchement logique entre regles.
