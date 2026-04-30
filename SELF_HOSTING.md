# Self-Hosting Operations Guide

This guide is for **operators of self-hosted OpenLucid deployments** —
recovering admin access, configuring email, checking health, etc. If
you're looking for "how to use OpenLucid as a product," start with the
[main README](../README.md) instead.

---

## OpenLucid has two CLIs — pick the one for your task

OpenLucid ships with two separate command-line entry points. They look
similar but solve opposite failure modes; reach for the right one.

### 1. `openlucid` — host-side, talks over HTTP

```bash
openlucid list-merchants
openlucid kb-qa --offer xxx --question "..."
```

- Runs **on your laptop / server**, outside the container
- Talks to the running app over the REST API
- Needs a logged-in session (token cached in `~/.openlucid.json`)
- Installed via `bash tools/install.sh` (puts a symlink in `~/.local/bin/`)
- Every command has a 1:1 MCP tool counterpart, so AI agents see the
  same surface
- **Use this for routine data operations** — querying offers, writing
  scripts, kicking off topic studio, anything you'd do in the web UI

### 2. `python -m app.cli` — in-container, direct DB

```bash
docker compose exec app python -m app.cli <subcommand>
```

- Runs **inside the container**
- Talks **directly to PostgreSQL**, bypassing HTTP / auth / email
- No login, no token — `docker exec` access *is* the trust boundary
- **Use this for recovery operations** — when the normal CLI can't
  reach the app (forgot admin password, email not configured, no admin
  user exists yet, etc.)

> **Why two CLIs?** A single binary can't both `talk-via-HTTP` (which
> needs the app to be working) *and* `bypass-HTTP-when-the-app-is-broken`
> (which needs to bypass the same path). The two failure modes are
> opposite and require opposite approaches. Dify's split is the same:
> `flask <command>` for direct DB, `dify-cloud` API for routine ops.

---

## Account Recovery

The most common reason to reach for `python -m app.cli` is account
trouble. All three commands here bypass HTTP / email / auth entirely.

### Forgot admin password

If `RESEND_API_KEY` / `SMTP_HOST` are configured in `docker/.env`, use
the normal **Forgot Password** flow on the sign-in page — a reset link
will arrive in your inbox.

If email is **not** configured, the sign-in page surfaces a red error
pointing you here. Run:

```bash
docker compose exec app python -m app.cli reset-password \
  --email admin@example.com --new-password NewSecurePw123
```

Password requirements: at least 8 characters, including **at least one
letter and one digit**.

### Lost access to the original email

If you can no longer receive mail at the address tied to the admin
account (job change, decommissioned domain, etc.), change it in place
without going through email verification:

```bash
docker compose exec app python -m app.cli reset-email \
  --email old@example.com \
  --new-email new@example.com \
  --email-confirm new@example.com
```

`--email-confirm` must match `--new-email` exactly — typo guard.

### Headless first-time setup (IaC / CI)

The web installer at `/install.html` is convenient for humans but
inconvenient for automation. Provision the first admin from a script:

```bash
docker compose exec app python -m app.cli create-admin \
  --email admin@example.com --password InitialPw123
```

Once a user exists, the installer route auto-redirects future visitors
to `/signin.html`, so this is safe to run idempotently in your bootstrap
pipeline (subsequent runs error with `user already exists`, exit code 2).

---

## Email configuration

OpenLucid uses email for password resets and (optionally) the in-app
feedback widget. Without it the **Forgot Password** flow fails fast
with a clear error rather than silently dropping the link into the
container logs.

Configure one of two providers in `docker/.env`:

**Resend (HTTP API, simplest)**:
```
RESEND_API_KEY=re_xxxxxxxxxxxxxxxxxxxx
MAIL_FROM=OpenLucid <noreply@your-domain.com>
```

**SMTP (any provider)**:
```
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USER=noreply@example.com
SMTP_PASSWORD=...
MAIL_FROM=OpenLucid <noreply@example.com>
```

Then `docker compose up -d --force-recreate app` to reload the env.
The mail provider is auto-detected — set `MAIL_TYPE=resend` or
`MAIL_TYPE=smtp` only if you have *both* configured and want to force
a specific one.

---

## Health checks

A container-friendly liveness probe is exposed at `/health` (auth-less,
returns DB connection status):

```bash
$ curl -s http://localhost/health
{"status": "ok", "version": "1.3.7", "database": "connected"}
```

The Docker health check (in `docker/docker-compose.yml`) already polls
this endpoint, so `docker compose ps` shows `healthy` once the app is
actually serving traffic — not just after the process starts.

---

## Upgrading

Database migrations run **automatically on container startup** (see
`app/main.py`'s lifespan hook → `alembic upgrade head`). For most
upgrades, the only step is:

```bash
cd docker
git pull
docker compose up -d --build
```

The new image starts, lifespan runs migrations, and the app is ready.
There is no separate `migrate` command to remember.

If a migration fails at startup the container stops with a clear error
in the logs (`docker compose logs app`); fix the underlying issue and
re-up.

---

## Backup & restore

> Coming soon — `pg_dump` recipe + asset directory backup.
> For now, back up `docker_db_data` volume + `docker_uploads` volume
> via standard Docker volume backup tooling.

---

## Where to file issues

If something here doesn't work or you hit a self-hosted scenario this
guide doesn't cover, please open an issue at
<https://github.com/agidesigner/OpenLucid/issues> with:

- Your OS / Docker version
- The output of `docker compose ps`
- Relevant lines from `docker compose logs app`
