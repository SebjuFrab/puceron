#!/bin/sh
set -eu

if [ "${RUN_STARTUP_TASKS:-1}" = "1" ]; then
    echo "Applying database migrations..."
    python manage.py migrate --noinput
    python manage.py collectstatic --noinput
fi

exec "$@"
