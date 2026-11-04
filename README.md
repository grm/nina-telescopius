# Telescopius → NINA

Un faux **Stellarium** pour [N.I.N.A.](https://nighttime-imaging.eu/) alimenté par
[Telescopius](https://telescopius.com). NINA croit parler au plugin *Remote Control*
de Stellarium ; en réalité, les cibles proviennent de Telescopius :

- **Ce soir** : les cibles du moment pour ton site (visibilité, temps de pose, distance à la Lune) ;
- **Recherche** : tout le catalogue Telescopius, planètes et comètes comprises, avec filtres ;
- **Mes listes** : tes listes d'observation Telescopius, y compris les angles de cadrage et les mosaïques.

Tu choisis une cible dans la page web, puis dans NINA tu cliques sur l'icône planétarium
**« Obtenir les coordonnées du logiciel du planétarium »** (Assistant de cadrage ou cible
du séquenceur).
NINA n'est pas modifié et aucun plugin n'est nécessaire.

## Installation

Il faut Python 3.9 ou plus récent, sans aucune autre dépendance. Sous Windows :
`winget install Python.Python.3.12`, ou l'installeur de python.org.

1. Copie ce dossier sur le PC qui fait tourner NINA.
2. Lance `start-bridge.bat` (Windows) ou `./start-bridge.sh`. La page s'ouvre sur
   <http://localhost:8090/>. Laisse la fenêtre ouverte pendant la session.
3. Dans **Réglages** :
   - colle ta clé API Telescopius (telescopius.com → Settings → API) ;
   - renseigne ton site (latitude, longitude, altitude), ou utilise le bouton de géolocalisation ;
   - choisis le nom envoyé à NINA (« Heart Nebula », « IC 1805 » ou les deux).

   Ces réglages sont enregistrés dans `config.json`, qui n'est pas versionné.
   Tu peux aussi copier `config.example.json`.
4. Dans NINA : **Options → Équipement → Planétarium**, choisis **Stellarium** avec
   l'hôte `localhost` et le port `8090` (ce sont les valeurs par défaut).

> Si le vrai Stellarium tourne avec Remote Control sur le port 8090, le bridge ne
> démarrera pas (port occupé). Ferme Stellarium, ou lance le bridge avec `--port 8091`
> et reporte ce port dans NINA.

## Utilisation

| Dans NINA | Ce que reçoit NINA |
|---|---|
| Assistant de cadrage → *Obtenir les coordonnées du logiciel du planétarium* | nom + RA/Dec J2000 de la cible choisie, et la rotation si elle est activée |
| Séquenceur, conteneur de cible DSO → même bouton | idem ; l'angle de position est 0 si la rotation n'est pas transmise |
| Options → Général → Astrométrie → *Obtenir la localisation depuis le logiciel Planétarium* | le site configuré dans le bridge |

- Le bandeau en haut de la page indique la cible actuellement servie à NINA. La pastille
  « NINA : contacté il y a… » confirme que NINA interroge bien le bridge (survole-la
  pour voir les dernières requêtes).
- **Rotation** : coche « Transmettre la rotation » et saisis l'angle de position (PA,
  en degrés, mesuré du nord vers l'est, comme dans NINA). Pour les cibles d'une liste
  Telescopius qui ont un angle de cadrage, celui-ci est pré-rempli et activé.
  Si la rotation n'est pas transmise, le Framing conserve sa rotation actuelle.
- Si aucune cible n'est sélectionnée, NINA affiche une erreur au lieu d'importer des
  coordonnées arbitraires.
- Pour les **planètes et comètes**, la position transmise est celle de l'heure de la
  recherche. Relance la recherche juste avant de cadrer.
- La sélection survit au redémarrage du bridge (`state.json`).

### NINA sur une autre machine

Lance `python -m telescopius_bridge --host 0.0.0.0` et mets l'IP de la machine du
bridge dans NINA. La page web est alors accessible depuis tout le réseau local,
sans authentification, et permet de modifier les réglages. Ne le fais que sur un
réseau de confiance.

## Fonctionnement

```
Navigateur ──/ui/api/*──▶ bridge (Python, stdlib) ──HTTPS──▶ api.telescopius.com
NINA ──────────/api/*───▶ bridge (émulation Stellarium Remote Control)
```

Le bridge émule les seules routes que lit le driver Stellarium de NINA
(`NINA.Equipment/Equipment/MyPlanetarium/Stellarium.cs`) :

| Route | Réponse |
|---|---|
| `GET /api/main/status` | `location.latitude/longitude/altitude` |
| `GET /api/objects/info?format=json` | `name`, `localized-name`, `raJ2000`, `decJ2000` en degrés (404 si rien n'est sélectionné) |
| `GET /api/stelproperty/list` | `Oculars.enableCCD` (vrai si une rotation est transmise) et `Oculars.selectedCCDRotationAngle` = 360 − PA |
| `GET /api/main/view` | vecteurs `j2000`/`jNow`/`altAz` pointant sur la cible (NINA les utilise quand l'oculaire CCD est actif) |

Les réponses Telescopius sont mises en cache 10 minutes (60 s pour les listes), car
l'API est limitée à 60 requêtes par minute. Le bouton « Recharger » des listes
contourne le cache.

## Tests

```sh
python3 -m unittest discover -s tests -t .
```

- `tests/test_astro.py` : altitudes, azimuts et précession comparés à astropy ;
- `tests/test_telescopius.py` : normalisation des réponses Telescopius réelles
  (`tests/fixtures/`), client HTTP (cache, erreurs 401/429, réseau) ;
- `tests/test_server.py` : émulation Stellarium et API de la page web, plus un test de
  **compatibilité NINA** qui compile et exécute le *vrai* driver Stellarium de NINA
  (C#, code source non modifié) contre le bridge. Ce test nécessite le SDK .NET 8 et
  `tests/nina_compat/fetch_nina_sources.sh` ; sinon il est ignoré :

  ```sh
  tests/nina_compat/fetch_nina_sources.sh   # télécharge Stellarium.cs & co (MPL-2.0)
  DOTNET=/chemin/vers/dotnet python3 -m unittest tests.test_server -v
  ```
