# PUCERON

Application Django mobile-first pour le suivi des pucerons, auxiliaires, actions culturales et recommandations.

## Stack
- Django 5.2
- Wagtail 7
- Django REST Framework
- PostgreSQL en production
- Docker + Gunicorn
- Nginx en frontal

## Lancement local Windows
```powershell
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
```

## Variables d'environnement
Le projet tourne en local sans `.env`, avec SQLite.

Pour la production, copier `.env.example` en `.env` et adapter les valeurs :

```env
DJANGO_SECRET_KEY=change-me
DJANGO_DEBUG=False
DJANGO_ALLOWED_HOSTS=puceron.agrobio-bretagne.org
DJANGO_CSRF_TRUSTED_ORIGINS=https://puceron.agrobio-bretagne.org
DJANGO_WAGTAILADMIN_BASE_URL=https://puceron.agrobio-bretagne.org/cms

POSTGRES_DB=puceron
POSTGRES_USER=puceron
POSTGRES_PASSWORD=change-me
DATABASE_HOST=db
DATABASE_PORT=5432
```

## Déploiement cible
Machine Debian avec :
- Docker
- Docker Compose plugin
- Nginx installe sur l'hote

Le mode de déploiement prévu est :
- conteneur `web` : Django + Gunicorn
- conteneur `db` : PostgreSQL
- Nginx Debian : reverse proxy vers `127.0.0.1:8001`

## Arborescence recommandée VPS
```bash
/var/www/puceron/
  .env
  media/
  staticfiles/
  app/
```

## Déploiement VPS
Guide complet :

- [deploy/DEPLOY_DOCKER_DEBIAN.md](deploy/DEPLOY_DOCKER_DEBIAN.md)

Fichiers utiles :
- [Dockerfile](Dockerfile)
- [docker-compose.yml](docker-compose.yml)
- [deploy/docker/entrypoint.sh](deploy/docker/entrypoint.sh)
- [deploy/nginx/puceron.agrobio-bretagne.org.conf](deploy/nginx/puceron.agrobio-bretagne.org.conf)

## GitHub
Si le dossier n'est pas encore un depot Git :

```powershell
git init
git branch -M main
git remote add origin https://github.com/SebjuFrab/puceron.git
git add .
git commit -m "Initial deploy-ready version"
git push -u origin main
```
