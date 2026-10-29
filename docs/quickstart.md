# Set up sopops with Docker

Run sopops and PostgreSQL on your machine. This guide is for self-hosting. It
does not configure app.sopops.dev.

## Start a local server

Before you begin, install Git and Docker with Docker Compose. Make sure that
port `8484` is free.

```sh
git clone https://github.com/sopops-dev/sopops
cd sopops
cp .env.example .env
docker compose up --build -d
```

Docker starts sopops and PostgreSQL. Check that the server is ready:

```sh
curl -fsS http://127.0.0.1:8484/healthz
```

The command returns:

```json
{"ok":true}
```

Open `http://127.0.0.1:8484`. On a fresh server, **Get set up** asks you for a
name, team, and the models to use for checks. Without model settings, sopops
stores calls but does not check them.

## Keep data when you restart containers

Docker stores PostgreSQL data in the `pgdata` volume. `docker compose down`
stops the containers and keeps that volume. Do not run `docker compose down -v`
unless you want to delete every workspace, release, and session.

Back up the database before you upgrade or change the host:

```sh
docker compose exec -T db pg_dump -U sopops sopops > sopops-backup.sql
```

To update a checkout, pull the new source and rebuild the application. sopops
runs database migrations when it starts.

```sh
git pull
docker compose up --build -d
```

## Set secrets before you expose the server

The Compose file binds sopops to `127.0.0.1`, so only your machine can reach it.
Before you make it reachable from another machine, set a secret key, public
URL, and sign-in policy in `.env`.

Set `SOPOPS_SECRET_KEY` before you save a platform key, GitHub token, webhook
secret, or a team's model key.

Generate the Fernet key after the containers start:

```sh
docker compose exec app python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Copy the result into `.env`, then set values like these:

```dotenv
SOPOPS_SECRET_KEY=<GENERATED_FERNET_KEY>
SOPOPS_PUBLIC_URL=https://sopops.example.com
SOPOPS_AUTH_REQUIRED=true
SOPOPS_OPEN_SIGNUP=false
```

Use a TLS proxy in front of the application. Change the `app` port binding in
`docker-compose.yml` only after you set `SOPOPS_PUBLIC_URL`, turn on sign-in,
and configure that proxy. See [configuration](configuration.md) and
[sign-in](sign-in.md) for every setting and provider.

## Check logs when startup fails

Use Compose logs to inspect the application and database:

```sh
docker compose logs -f app db
```

If the health check does not return `{"ok":true}`, confirm that port `8484` is
free and that the database container is healthy:

```sh
docker compose ps
```

## Continue with the agent workflow

After the server is ready, [import your agents](import.md), then [connect an
agent](connect.md). To see the whole loop before you use your own calls, run
the [local demo](first-agent.md).
