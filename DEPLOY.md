# Deploying circle-be on the shared Hetzner VPS

The box already runs **avora** (its Caddy owns ports 80/443). circle-be runs as:
- its **own Postgres** (`db`) on the VPS — data migrated in from Neon once,
- the **API** (`api`),
- **no second Caddy** — the API joins avora's reverse-proxy network and avora's
  existing Caddy routes `api.circle.optiminastic.com` → `circle-be-api:8000`.

We never touch the avora containers — we only add **one site block** to the
shared Caddy (additive, reversible). A bad edit can't break avora: `caddy reload`
validates first and keeps the old config on error.

Files: `Dockerfile`, `.dockerignore`, `docker-compose.yml`, `.env.production.example`.

---

## 0. Gather
- SSH to the VPS. The **Neon** `DATABASE_URL` (migration source). `AWS_*`,
  `GOOGLE_CLIENT_ID/SECRET`, and the Gmail app password.

## 1. Find avora's Caddy network + Caddyfile
```bash
docker inspect deploy-caddy-1 -f '{{range $k,$_ := .NetworkSettings.Networks}}{{println $k}}{{end}}'   # -> PROXY_NETWORK
docker inspect deploy-caddy-1 -f '{{range .Mounts}}{{.Source}} -> {{.Destination}}{{"\n"}}{{end}}'      # -> host Caddyfile path
```

## 2. Code + env
```bash
cd /opt/circle-be
git pull                        # already a clone; brings Dockerfile/compose/etc.
cp .env.production.example .env  # if you don't have .env yet
nano .env
```
Set in `.env`:
- `POSTGRES_PASSWORD=<strong>` and `DATABASE_URL=postgresql+psycopg://circle:<strong>@db:5432/circle`
- `PROXY_NETWORK=<network from step 1>`
- `CORS_ORIGINS`, `FRONTEND_URL`, `GOOGLE_*` (no trailing `\n` in the secret!), `AWS_*`, `SMTP_*`.

## 3. Build & start (Postgres + API; nothing published)
```bash
docker compose up -d --build
docker compose logs -f api      # "Database engine initialized" + "... started"
```

## 4. Migrate the data Neon → VPS Postgres
```bash
docker run --rm postgres:17-alpine pg_dump "<NEON_DATABASE_URL>" \
  --no-owner --no-privileges -Fc > /tmp/circle.dump
docker compose exec -T db pg_restore --no-owner --clean --if-exists -U circle -d circle < /tmp/circle.dump
docker compose restart api
```
(Match the `postgres:17-alpine` tag to Neon's major version if it warns.)

## 5. Route the domain through avora's Caddy
Append to the Caddyfile (host path from step 1):
```
api.circle.optiminastic.com {
    reverse_proxy circle-be-api:8000
}
```
Reload without restarting avora:
```bash
docker exec deploy-caddy-1 caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile
```

## 6. DNS
Point `api.circle.optiminastic.com` A-record at the VPS IP. Caddy auto-issues TLS.

## 7. Verify
```bash
curl -fsS https://api.circle.optiminastic.com/api/health   # {"status":"ok","database":"up"}
```
Then load the Vercel frontend and confirm data + Question Library.

## Day-2
- Redeploy: `git pull && docker compose up -d --build`
- Logs/restart: `docker compose logs -f api` · `docker compose restart api`
- **DB backup (you own it now):**
  `docker compose exec -T db pg_dump -U circle circle | gzip > /opt/backups/circle-$(date +%F).sql.gz`
- Remove: `docker compose down`, delete the Caddy block, reload Caddy.
