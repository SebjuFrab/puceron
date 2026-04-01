# Deploiement Debian + Docker + Nginx

## 1. Prerequis serveur
```bash
apt update
apt install -y docker.io docker-compose-plugin nginx certbot python3-certbot-nginx git
systemctl enable docker
systemctl start docker
```

## 2. Dossiers
```bash
mkdir -p /var/www/puceron
cd /var/www/puceron
git clone https://github.com/SebjuFrab/puceron.git app
mkdir -p media staticfiles
cp app/.env.example .env
```

Editez ensuite `/var/www/puceron/.env`.

## 3. Lancer les conteneurs
```bash
cd /var/www/puceron/app
docker compose up -d --build
docker compose logs -f web
```

## 4. Creer l'admin Django
```bash
cd /var/www/puceron/app
docker compose exec web python manage.py createsuperuser
```

## 5. Mettre a jour le site Wagtail
Au premier deploiement, remplacez le site `localhost` par votre domaine :

```bash
cd /var/www/puceron/app
docker compose exec web python manage.py shell -c "from wagtail.models import Site; site=Site.objects.get(is_default_site=True); site.hostname='puceron.agrobio-bretagne.org'; site.port=443; site.site_name='PUCERON'; site.save(); print(site.hostname, site.port)"
```

## 6. Configurer Nginx
```bash
cp /var/www/puceron/app/deploy/nginx/puceron.agrobio-bretagne.org.conf /etc/nginx/sites-available/puceron.agrobio-bretagne.org
ln -s /etc/nginx/sites-available/puceron.agrobio-bretagne.org /etc/nginx/sites-enabled/puceron.agrobio-bretagne.org
nginx -t
systemctl reload nginx
```

## 7. HTTPS
```bash
certbot --nginx -d puceron.agrobio-bretagne.org
```

## 8. Mises a jour
```bash
cd /var/www/puceron/app
git pull
docker compose up -d --build
docker compose exec web python manage.py migrate
docker compose exec web python manage.py collectstatic --noinput
```
