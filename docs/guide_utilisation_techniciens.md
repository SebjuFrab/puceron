# Guide d'utilisation technicien

## 1. Objet de l'outil

L'outil `PUCERON` permet au technicien de :

- suivre les comptages pucerons et auxiliaires des producteurs rattachés,
- consulter les recommandations en cours,
- saisir des données à la place d'un producteur,
- enregistrer des actions preventives ou curatives,
- créer ou mettre à jour des comptes producteurs,
- importer des producteurs par fichier CSV,
- exporter les données.

## 2. Connexion

Connectez-vous avec :

- votre identifiant ou votre adresse mail,
- votre mot de passe.

Si besoin, utilisez `Mot de passe oublié ?` sur la page de connexion.

## 3. Menu principal technicien

En tant que technicien, les onglets principaux sont :

- `Saisir`
- `Infos`
- `CMS` si vous avez un accès staff
- `Dashboard`
- `Mon profil`
- `Nouveau producteur`
- `Import producteurs`
- `Mes données`
- `Vue technicien`

Quand vous prenez le contrôle d'un producteur, l'interface bascule temporairement en mode producteur.

## 4. Mon profil

Dans `Mon profil`, vous pouvez vérifier ou mettre à jour :

- votre nom,
- votre mail,
- votre mobile,
- votre photo ou logo,
- votre adresse,
- votre position GPS.

Le département du technicien doit être correctement renseigné, car il est utilisé pour les rattachements et les filtres.

## 5. Créer un compte producteur

Dans `Nouveau producteur`, vous pouvez créer un producteur rattaché à votre compte.

Vous renseignez notamment :

- identifiant,
- prénom,
- nom,
- mot de passe si creation unitaire,
- nom de ferme,
- adresse,
- code postal,
- commune,
- mobile,
- technicien référent.

### Règle importante

- un technicien ne peut créer des producteurs que pour lui-même,
- seul un super-admin peut créer un producteur pour un autre technicien.

Le technicien référent est donc pré-rempli avec le technicien connecté.

## 6. Importer des producteurs par CSV

Dans `Import producteurs`, vous pouvez importer en masse des comptes producteurs.

### Colonnes attendues

Le fichier CSV doit contenir les colonnes suivantes :

- `Raison social`
- `Nom`
- `Prénom`
- `Département`
- `mail`
- `Adresse`
- `code postal`
- `commune`
- `IDtek referents`
- `mobile`

### Règles d'import

- les producteurs existants sont recherches par `email`,
- un technicien importe uniquement des producteurs rattachés a lui,
- un super-admin peut importer pour plusieurs techniciens,
- un bouton permet de telecharger le template d'import.

### Première connexion des producteurs importés

Les comptes importés doivent utiliser `Mot de passe oublié ?` pour définir leur mot de passe.

## 7. Vue technicien

La `Vue technicien` est la vue centrale de pilotage.

Elle permet de :

- voir la carte des producteurs rattachés,
- sélectionner un producteur,
- consulter ses séries,
- consulter ses comptages,
- consulter ses actions,
- modifier les informations du producteur,
- lancer une saisie à sa place,
- prendre le contrôle de son compte.

## 8. Prendre le contrôle d'un producteur

Depuis `Vue technicien`, vous pouvez cliquer sur `Prendre le contrôle du compte`.

Cela ouvre l'interface producteur, mais sur le compte du producteur sélectionné.

Vous pouvez alors :

- créer ou modifier ses séries,
- saisir un comptage,
- saisir une action préventive ou curative,
- consulter ses recommandations,
- consulter et modifier ses données.

Un bandeau permet ensuite de revenir en `Vue technicien`.

## 9. Saisir un comptage

Là saisie suit le protocole standard :

1. choisir une série,
2. choisir `Effectuer un comptage`,
3. saisir plant par plant,
4. pour chaque feuille :
   - cliquer sur `puceron` si la feuille est infestee,
   - cliquer sur `aux` pour ajouter les auxiliaires observés,
5. enregistrer et passer au plant suivant,
6. apres le dernier plant, renseigner la date d'observation et un commentaire,
7. enregistrer le comptage.

### Règle pour la presence de puceron

La feuille est comptee en presence si elle porte :

- `plus de 5 pucerons`

## 10. Saisir une action préventive ou curative

Après choix de la série, vous pouvez saisir une action.

Les types d'action possibles sont definis dans l'admin, par exemple :

- action manuelle,
- traitement,
- lâcher d'auxiliaires.

Selon le type d'action, des champs specifiques peuvent apparaitre :

- portee,
- molécule,
- auxiliaire lâché,
- details.

## 11. Recommandations

Après un comptage, l'outil peut calculer une recommandation selon :

- la culture,
- la semaine,
- le taux d'infestation,
- le niveau d'auxiliaires.

Le technicien peut consulter les recommandations :

- dans la vue du producteur,
- dans les séries,
- dans les recommandations en cours lorsqu'il agit en mode producteur,
- dans la vue technicien avec le détail du producteur sélectionné.

Une recommandation peut ensuite être :

- suivie vià un levier propose,
- ou non suivie avec un motif.

## 12. Dashboard technicien

Le `Dashboard` technicien permet de visualiser toutes les séries :

- d'une même culture,
- d'une même année,
- sur le périmètre du technicien.

### Filtres disponibles

- culture,
- année,
- bio / non bio / les deux,
- variété,
- producteur,
- série.

### Lecture du dashboard

Deux graphes sont affichés :

- `Taux d'infestation`
- `Auxiliaires / plant`

Le graphe `Taux d'infestation` reste toujours sur une echelle de :

- `0 a 100 %`

Les actions saisies sur les séries visibles sont également listees sous les graphes.

## 13. Mes données

Dans `Mes données`, un technicien voit :

- tous les comptages de tous les producteurs rattachés,
- toutes les actions de tous ces producteurs.

Une colonne `Producteur` permet d'identifier l'origine des données.

Depuis cette page, vous pouvez :

- consulter les données,
- modifier certains comptages,
- exporter les données en Excel.

## 14. Modification d'un producteur

Depuis la `Vue technicien`, vous pouvez modifier un producteur :

- informations de contact,
- nom de ferme,
- mail,
- mobile,
- adresse,
- GPS,
- rattachement technicien selon vos droits.

## 15. GPS et cartographie

Les producteurs peuvent être localisés sur la carte à partir de :

- l'adresse,
- du code postal,
- de la commune.

Le point GPS peut aussi être ajusté manuellement.

Cette carte facilite :

- la visualisation du portefeuille producteur,
- l'accès rapide à un producteur depuis la vue technicien.

## 16. Bonnes pratiques

- vérifier que le producteur est rattaché au bon technicien,
- vérifier les coordonnées avant import ou creation,
- vérifier la bonne série avant toute saisie,
- utiliser le commentaire pour tracer les cas particuliers,
- utiliser la prise de contrôle uniquement quand il faut vraiment saisir à la place du producteur,
- exporter régulièrement les données si un bilan de groupe est prévu.

## 17. Rappel du protocole de comptage

Le protocole standard est :

1. `10 plants`,
2. `3 feuilles par plant` :
   - basse,
   - milieu,
   - haute,
3. presence de puceron si :
   - `plus de 5 pucerons sur la feuille`,
4. comptage des auxiliaires sur ces memes feuilles.

## 18. Quand faire appel au super-admin

Le super-admin intervient notamment pour :

- créer des producteurs pour un autre technicien,
- modifier les parametrages globaux,
- gérer les tokens API,
- administrer les pages CMS,
- administrer les règles de décision et les leviers,
- gérer les types d'action, molécules, auxiliaires, variétés et cultures.

## 19. Besoin d'aide

En cas de doute :

- vérifier d'abord la série et le producteur sélectionnés,
- consulter les pages `Infos`,
- ou remonter le point au super-admin si le probleme releve du paramétrage.
