# Guide d'utilisation technicien

## 1. Objet de l'outil

L'outil `PUCERON` permet au technicien de :

- suivre les comptages pucerons et auxiliaires des producteurs rattaches,
- consulter les recommandations en cours,
- saisir des donnees a la place d'un producteur,
- enregistrer des actions preventives ou curatives,
- creer ou mettre a jour des comptes producteurs,
- importer des producteurs par fichier CSV,
- exporter les donnees.

## 2. Connexion

Connectez-vous avec :

- votre identifiant ou votre adresse mail,
- votre mot de passe.

Si besoin, utilisez `Mot de passe oublie ?` sur la page de connexion.

## 3. Menu principal technicien

En tant que technicien, les onglets principaux sont :

- `Saisir`
- `Infos`
- `CMS` si vous avez un acces staff
- `Dashboard`
- `Mon profil`
- `Nouveau producteur`
- `Import producteurs`
- `Mes donnees`
- `Vue technicien`

Quand vous prenez le controle d'un producteur, l'interface bascule temporairement en mode producteur.

## 4. Mon profil

Dans `Mon profil`, vous pouvez verifier ou mettre a jour :

- votre nom,
- votre mail,
- votre mobile,
- votre photo ou logo,
- votre adresse,
- votre position GPS.

Le departement du technicien doit etre correctement renseigne, car il est utilise pour les rattachements et les filtres.

## 5. Creer un compte producteur

Dans `Nouveau producteur`, vous pouvez creer un producteur rattache a votre compte.

Vous renseignez notamment :

- identifiant,
- prenom,
- nom,
- mot de passe si creation unitaire,
- nom de ferme,
- adresse,
- code postal,
- commune,
- mobile,
- technicien referent.

### Regle importante

- un technicien ne peut creer des producteurs que pour lui-meme,
- seul un super-admin peut creer un producteur pour un autre technicien.

Le technicien referent est donc pre-rempli avec le technicien connecte.

## 6. Importer des producteurs par CSV

Dans `Import producteurs`, vous pouvez importer en masse des comptes producteurs.

### Colonnes attendues

Le fichier CSV doit contenir les colonnes suivantes :

- `Raison social`
- `Nom`
- `Prenom`
- `Departement`
- `mail`
- `Adresse`
- `code postal`
- `commune`
- `IDtek referents`
- `mobile`

### Regles d'import

- les producteurs existants sont recherches par `email`,
- un technicien importe uniquement des producteurs rattaches a lui,
- un super-admin peut importer pour plusieurs techniciens,
- un bouton permet de telecharger le template d'import.

### Premiere connexion des producteurs importes

Les comptes importes doivent utiliser `Mot de passe oublie ?` pour definir leur mot de passe.

## 7. Vue technicien

La `Vue technicien` est la vue centrale de pilotage.

Elle permet de :

- voir la carte des producteurs rattaches,
- selectionner un producteur,
- consulter ses series,
- consulter ses comptages,
- consulter ses actions,
- modifier les informations du producteur,
- lancer une saisie a sa place,
- prendre le controle de son compte.

## 8. Prendre le controle d'un producteur

Depuis `Vue technicien`, vous pouvez cliquer sur `Prendre le controle du compte`.

Cela ouvre l'interface producteur, mais sur le compte du producteur selectionne.

Vous pouvez alors :

- creer ou modifier ses series,
- saisir un comptage,
- saisir une action preventive ou curative,
- consulter ses recommandations,
- consulter et modifier ses donnees.

Un bandeau permet ensuite de revenir en `Vue technicien`.

## 9. Saisir un comptage

La saisie suit le protocole standard :

1. choisir une serie,
2. choisir `Effectuer un comptage`,
3. saisir plant par plant,
4. pour chaque feuille :
   - cliquer sur `puceron` si la feuille est infestee,
   - cliquer sur `aux` pour ajouter les auxiliaires observes,
5. enregistrer et passer au plant suivant,
6. apres le dernier plant, renseigner la date d'observation et un commentaire,
7. enregistrer le comptage.

### Regle pour la presence de puceron

La feuille est comptee en presence si elle porte :

- `plus de 5 pucerons`

## 10. Saisir une action preventive ou curative

Apres choix de la serie, vous pouvez saisir une action.

Les types d'action possibles sont definis dans l'admin, par exemple :

- action manuelle,
- traitement,
- lacher d'auxiliaires.

Selon le type d'action, des champs specifiques peuvent apparaitre :

- portee,
- molecule,
- auxiliaire lache,
- details.

## 11. Recommandations

Apres un comptage, l'outil peut calculer une recommandation selon :

- la culture,
- la semaine,
- le taux d'infestation,
- le niveau d'auxiliaires.

Le technicien peut consulter les recommandations :

- dans la vue du producteur,
- dans les series,
- dans les recommandations en cours lorsqu'il agit en mode producteur,
- dans la vue technicien avec le detail du producteur selectionne.

Une recommandation peut ensuite etre :

- suivie via un levier propose,
- ou non suivie avec un motif.

## 12. Dashboard technicien

Le `Dashboard` technicien permet de visualiser toutes les series :

- d'une meme culture,
- d'une meme annee,
- sur le perimetre du technicien.

### Filtres disponibles

- culture,
- annee,
- bio / non bio / les deux,
- variete,
- producteur,
- serie.

### Lecture du dashboard

Deux graphes sont affiches :

- `Taux d'infestation`
- `Auxiliaires / plant`

Le graphe `Taux d'infestation` reste toujours sur une echelle de :

- `0 a 100 %`

Les actions saisies sur les series visibles sont egalement listees sous les graphes.

## 13. Mes donnees

Dans `Mes donnees`, un technicien voit :

- tous les comptages de tous les producteurs rattaches,
- toutes les actions de tous ces producteurs.

Une colonne `Producteur` permet d'identifier l'origine des donnees.

Depuis cette page, vous pouvez :

- consulter les donnees,
- modifier certains comptages,
- exporter les donnees en Excel.

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

Les producteurs peuvent etre localises sur la carte a partir de :

- l'adresse,
- du code postal,
- de la commune.

Le point GPS peut aussi etre ajuste manuellement.

Cette carte facilite :

- la visualisation du portefeuille producteur,
- l'acces rapide a un producteur depuis la vue technicien.

## 16. Bonnes pratiques

- verifier que le producteur est rattache au bon technicien,
- verifier les coordonnees avant import ou creation,
- verifier la bonne serie avant toute saisie,
- utiliser le commentaire pour tracer les cas particuliers,
- utiliser la prise de controle uniquement quand il faut vraiment saisir a la place du producteur,
- exporter regulierement les donnees si un bilan de groupe est prevu.

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

- creer des producteurs pour un autre technicien,
- modifier les parametrages globaux,
- gerer les tokens API,
- administrer les pages CMS,
- administrer les regles de decision et les leviers,
- gerer les types d'action, molecules, auxiliaires, varietes et cultures.

## 19. Besoin d'aide

En cas de doute :

- verifier d'abord la serie et le producteur selectionnes,
- consulter les pages `Infos`,
- ou remonter le point au super-admin si le probleme releve du parametrage.
