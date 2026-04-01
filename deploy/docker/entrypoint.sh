#!/bin/sh
set -eu

echo "Waiting for database..."
python manage.py migrate --noinput
python manage.py collectstatic --noinput

exec "$@"
