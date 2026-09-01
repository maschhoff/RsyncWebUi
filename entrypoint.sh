#!/bin/sh
set -e

mkdir -p "${DATA_DIR:-/config}"

if [ -n "$TZ" ] && [ -f "/usr/share/zoneinfo/$TZ" ]; then
    ln -snf "/usr/share/zoneinfo/$TZ" /etc/localtime
    echo "$TZ" > /etc/timezone
fi

# SSH keys in the config volume get the permissions OpenSSH insists on.
if [ -d "${DATA_DIR:-/config}/ssh" ]; then
    chmod 700 "${DATA_DIR:-/config}/ssh" || true
    find "${DATA_DIR:-/config}/ssh" -type f -name 'id_*' ! -name '*.pub' -exec chmod 600 {} \; 2>/dev/null || true
fi

echo "RsyncWebUI starting on port ${PORT:-8080} (timezone ${TZ:-UTC})"
rsync --version | head -n 1

# One worker, many threads: the scheduler and running transfers share one process.
exec gunicorn \
    --bind "0.0.0.0:${PORT:-8080}" \
    --workers 1 \
    --threads "${THREADS:-16}" \
    --worker-class gthread \
    --timeout 0 \
    --graceful-timeout 20 \
    --access-logfile - \
    --error-logfile - \
    --log-level "${GUNICORN_LOG_LEVEL:-warning}" \
    wsgi:app
