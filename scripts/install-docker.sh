#!/bin/sh
set -eu

CFDNS_REPOSITORY="${CFDNS_REPOSITORY:-owner/cfdns}"
CFDNS_IMAGE="${CFDNS_IMAGE:-ghcr.io/${CFDNS_REPOSITORY}:latest}"
CFDNS_INSTALL_DIR="${CFDNS_INSTALL_DIR:-${HOME}/.local/share/cfdns}"
CFDNS_PORT="${CFDNS_PORT:-8000}"
RAW_BASE="https://raw.githubusercontent.com/${CFDNS_REPOSITORY}/main"

command -v docker >/dev/null 2>&1 || {
    echo "Docker is required but was not found." >&2
    exit 1
}
docker compose version >/dev/null 2>&1 || {
    echo "Docker Compose v2 is required but was not found." >&2
    exit 1
}
command -v curl >/dev/null 2>&1 || {
    echo "curl is required but was not found." >&2
    exit 1
}
command -v openssl >/dev/null 2>&1 || {
    echo "openssl is required but was not found." >&2
    exit 1
}

mkdir -p "$CFDNS_INSTALL_DIR"
curl --fail --silent --show-error --location "$RAW_BASE/compose.yaml" \
    --output "$CFDNS_INSTALL_DIR/compose.yaml"

if [ ! -f "$CFDNS_INSTALL_DIR/.docker.env" ]; then
    database_password="$(openssl rand -hex 24)"
    encryption_key="$(openssl rand -base64 32 | tr '+/' '-_')"
    admin_password="${ADMIN_PASSWORD:-$(openssl rand -base64 18 | tr '+/' '-_' | tr -d '=')}"
    umask 077
    {
        echo "POSTGRES_USER=cfdns"
        echo "POSTGRES_PASSWORD=$database_password"
        echo "POSTGRES_DB=cfdns"
        echo "DATABASE_URL=postgresql+asyncpg://cfdns:$database_password@postgres/cfdns"
        echo "ENCRYPTION_KEY=$encryption_key"
        echo "ADMIN_PASSWORD=$admin_password"
        echo "SYNC_INTERVAL_MINUTES=15"
        echo "CLOUDFLARE_API_BASE=https://api.cloudflare.com/client/v4"
    } > "$CFDNS_INSTALL_DIR/.docker.env"
    echo "Generated administrator password: $admin_password"
    echo "Save this password now. It is also stored in $CFDNS_INSTALL_DIR/.docker.env"
fi

cd "$CFDNS_INSTALL_DIR"
export CFDNS_IMAGE CFDNS_PORT
docker compose pull
docker compose up --detach

echo "CFDNS is starting at http://127.0.0.1:$CFDNS_PORT"
echo "Installation directory: $CFDNS_INSTALL_DIR"
