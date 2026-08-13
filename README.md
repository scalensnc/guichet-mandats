# Base et guichet des mandats

Ce projet transforme les projets Bexio en une base géographique consultable
dans QGIS et dans un guichet web.

Le jeton Bexio reste exclusivement sur l'ordinateur qui effectue la mise à
jour. Le portail publié ne contient que le fichier JSON produit à partir du
CSV, jamais le jeton ni un accès direct à Bexio.

## Mise à jour complète

Dans PowerShell, depuis ce dossier :

```powershell
$env:BEXIO_API_TOKEN = "votre-jeton"
python .\update_portal.py
Remove-Item Env:BEXIO_API_TOKEN
```

Cette commande effectue, dans l'ordre :

1. téléchargement de tous les projets Bexio ;
2. reconnaissance de la commune et du bien-fonds ;
3. géocodage par geo.admin.ch et remplacement atomique de `projects.csv` ;
4. remplacement atomique de `portal/data/projects.json`.

La base est reconstruite complètement à chaque exécution. Les nouveaux
dossiers apparaissent donc dans le portail, mais les corrections et
suppressions faites dans Bexio sont également répercutées.

Pour reconstruire le portail à partir du CSV existant, sans réseau :

```powershell
python .\update_portal.py --from-csv
```

Options disponibles :

```powershell
python .\update_portal.py --help
python .\update_portal.py --snapshot-date 01-08-2026
python .\update_portal.py --from-csv --output .\autre-dossier\projects.json
```

## Prévisualisation locale

Un serveur HTTP local est nécessaire, car un navigateur bloque généralement
le chargement du JSON lorsqu'on ouvre directement `index.html` :

```powershell
python -m http.server 8000 --directory .\portal
```

Puis ouvrir <http://localhost:8000>. Le portail propose une recherche par
numéro, intitulé, commune ou parcelle, des filtres, une liste de résultats et
une carte OpenStreetMap.

## Publication

Le dossier `portal/` est publié automatiquement sur GitHub Pages à chaque
envoi vers la branche `main`. Pour mettre à jour Bexio puis publier :

```powershell
$env:BEXIO_API_TOKEN = "votre-jeton"
.\publier_portail.ps1
Remove-Item Env:BEXIO_API_TOKEN
```

Pour republier uniquement à partir du CSV local existant :

```powershell
.\publier_portail.ps1 -DepuisCsv
```

Le workflow `.github/workflows/pages.yml` n'envoie à GitHub Pages que le
dossier `portal/`. Le jeton Bexio n'est jamais enregistré dans Git.

Pour une diffusion hors de l'entreprise, vérifier au préalable que les noms de
dossiers et adresses peuvent être rendus publics. Pour un usage métier, un
hébergement protégé par authentification est recommandé.

## QGIS

Le projet `MANDATS.qgz` charge `projects.csv` comme couche de points WGS84
(`EPSG:4326`). Le script historique reste directement utilisable :

```powershell
$env:BEXIO_API_TOKEN = "votre-jeton"
python .\BDD-Mandats.py --verbose
Remove-Item Env:BEXIO_API_TOKEN
```

## Tests hors ligne

```powershell
python -m unittest -v
```

Les tests ne contactent ni Bexio, ni l'OFS, ni geo.admin.ch.
