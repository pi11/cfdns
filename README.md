# CFDNS

CFDNS is a small self-hosted Cloudflare DNS manager. It keeps a searchable local SQLite or PostgreSQL cache of all zones and records while sending record changes directly to Cloudflare.

## Features

- Multiple Cloudflare accounts connected with scoped API tokens
- Fernet-encrypted token storage
- Full DNS record create, edit, and delete workflow
- Search across zone, hostname, record content (including IP addresses), Cloudflare comments, and local comments
- Manual synchronization and automatic synchronization every 15 minutes
- Local comments that survive Cloudflare synchronization
- FastAPI, SQLite or PostgreSQL, SQLAlchemy, Alembic, Jinja, and HTMX

## Requirements

- Python 3.12 or newer
- SQLite (included) or PostgreSQL 14 or newer
- A Cloudflare API token with `Zone:Read` and `DNS:Edit` for the relevant zones

## Installation

Choose a database. SQLite is the simplest option for a single-user installation:

```bash
cp .env.sqlite.example .env
mkdir -p data
```

For PostgreSQL, create a user and database as a PostgreSQL administrator:

```sql
CREATE ROLE cfdns WITH LOGIN PASSWORD 'replace-this-password';
CREATE DATABASE cfdns OWNER cfdns;
```

Create the application environment:

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e .
# Skip this when .env was created from .env.sqlite.example above.
cp .env.example .env
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Put the generated key in `.env`. For PostgreSQL, also set the correct database password. Keep `ENCRYPTION_KEY` stable: changing or losing it makes stored tokens unreadable.

Set `ADMIN_PASSWORD` to a strong administrator password. It defaults to `cfdns` for initial local setup and should be changed before exposing the service to other devices.

Apply migrations and start the server:

```bash
alembic upgrade head
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000>. For LAN access, bind to a private interface and put the application behind an HTTPS reverse proxy. The administrator login protects the UI, but CFDNS should still not be exposed directly to the public internet.

## Optional Docker installation

The manual installation above remains fully supported. Docker uses SQLite by default, so only the application image is required. The database file is kept in a persistent Docker volume.

After cloning the repository:

```bash
cp .docker.env.example .docker.env
# Replace every placeholder in .docker.env, then run:
docker compose up --detach --build
```

The application container applies Alembic migrations and starts FastAPI. SQLite data is stored in the `cfdns-data` Docker volume.

Published releases can be installed without cloning:

```bash
curl -fsSL https://raw.githubusercontent.com/pi11/cfdns/master/scripts/install-docker.sh | sh
```

The installer generates a Fernet encryption key and an administrator password, then starts the published GHCR image with SQLite. Set `ADMIN_PASSWORD` before the command to choose the initial password:

```bash
curl -fsSL https://raw.githubusercontent.com/pi11/cfdns/master/scripts/install-docker.sh | ADMIN_PASSWORD='choose-a-strong-password' sh
```

Useful Docker commands:

```bash
docker compose ps
docker compose logs --follow app
docker compose pull && docker compose up --detach
docker compose down
```

### Docker with PostgreSQL

For a larger installation, use the PostgreSQL Compose variant:

```bash
cp .docker-postgres.env.example .docker-postgres.env
# Replace every placeholder, then run:
docker compose --file compose.postgres.yaml up --detach --build
```

SQLite runs with WAL mode, foreign keys, a 30-second busy timeout, and one Uvicorn worker. PostgreSQL remains the better choice if multiple application processes or many users will write concurrently.

## Development

```bash
pip install -e '.[dev]'
pytest
ruff check .
```

The automatic sync interval can be changed with `SYNC_INTERVAL_MINUTES`. A failed account sync is recorded and displayed in the account panel; other accounts continue synchronizing.

## SSL certificate monitoring

SSL monitoring can be enabled per non-proxied A, AAAA, or CNAME record. Checks connect directly to every IP on port 443 while using the DNS record hostname for SNI and full certificate validation. CNAME targets are resolved to all current IPv4 and IPv6 addresses. The records table shows the earliest expiry, while the edit page shows every checked hostname/IP result.

Run all enabled checks manually:

```bash
./scripts/check_ssl.sh
```

Run the checks every six hours with crontab:

```cron
0 */6 * * * /opt/cfdns/scripts/check_ssl.sh >> /opt/cfdns/ssl-check.log 2>&1
```

The checker uses the system CA trust store. An invalid chain, hostname mismatch, expired certificate, DNS failure, timeout, or connection failure is stored and displayed as an error. When validation fails after the server presents a certificate, the checker makes an unverified second connection only to capture its expiration date.
