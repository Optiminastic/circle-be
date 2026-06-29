# Deploying circle-be on the shared Hetzner VPS

This box already runs another app (**avora**): its **Caddy owns ports 80/443**
and it has its own Postgres. So circle-be is deployed as **just one API
container** that:
- uses the **external Neon** database (already holds candidates, jobs, and the
  migrated question banks) — no Postgres on the VPS,
- joins **avora's reverse-proxy network** so avora's existing Caddy can route
  `api.circle.optiminastic.com` → circle-be over HTTPS.

**We never touch the avora containers.** We only (a) run our own container and
(b) add one site block to the shared Caddy's config (additive, reversible).

Files: `Dockerfile`, `.dockerignore`, `docker-compose.yml`, `.env.production.example`.

---

## 0. Prereqs (gather)
- SSH to the VPS (the same user that runs `docker` for avora).
- The **Neon** `DATABASE_URL` (icy-flower prod DB).
- `AWS_*` (B2/S3) and `GOOGLE_CLIENT_ID/SECRET` values.
- The Gmail app password for `careers@optiminastic.com` (if using SMTP email).

## 1. Discover avora's proxy network + Caddy config
```bash
docker ps   # confirm the avora caddy container name (e.g. deploy-caddy-1)

# Which Docker network is that Caddy on?  (usually "deploy_default")
docker inspect deploy-caddy-1 -f '{{range $k,$_ := .NetworkSettings.Networks}}{{$k}}{{"\n"}}{{end}}'

# Where is its Caddyfile mounted from on the host?  (note the Source path)
docker inspect deploy-caddy-1 -f '{{range .Mounts}}{{.Source}} -> {{.Destination}}{{"\n"}}{{end}}'
```
Note the **network name** (call it `<NET>`) and the host path of the **Caddyfile**
(call it `<CADDYFILE>`).

## 2. Get circle-be onto the VPS
```bash
sudo mkdir -p /opt/circle-be && sudo chown "$USER":"$USER" /opt/circle-be
git clone <circle-be repo URL> /opt/circle-be      # or scp the folder up
cd /opt/circle-be
```

## 3. Configure `.env`
```bash
cp .env.production.example .env
nano .env
```
Set:
- `DATABASE_URL=` the Neon URL (`postgresql://…neon.tech/neondb?sslmode=require&channel_binding=require`)
- `PROXY_NETWORK=<NET>` from step 1 (e.g. `deploy_default`)
- `CORS_ORIGINS=https://circle.optiminastic.com`, `FRONTEND_URL=https://circle.optiminastic.com`
- `GOOGLE_REDIRECT_URI=https://api.circle.optiminastic.com/api/calendar/oauth/callback`
- `AWS_*`, `GOOGLE_CLIENT_ID/SECRET`
- Email (Gmail SMTP): `SMTP_USER=careers@optiminastic.com`, `SMTP_PASSWORD=<app password>`,
  `SMTP_FROM_EMAIL=careers@optiminastic.com` (leave `RESEND_API_KEY`/`SENDGRID_API_KEY` blank).

## 4. Build & start the API (no ports published — Caddy reaches it internally)
```bash
docker compose up -d --build
docker compose logs -f api      # expect "Database engine initialized" + "... started"
```
`AUTO_CREATE_TABLES=true` ensures any missing tables exist on the Neon DB (idempotent).

Quick internal check (from a container on the same network):
```bash
docker exec deploy-caddy-1 wget -qO- http://circle-be-api:8000/api/health ; echo
# -> {"status":"ok","database":"up"}
```

## 5. Add the site to avora's Caddy (additive)
Append this block to `<CADDYFILE>` (the host path from step 1):
```
api.circle.optiminastic.com {
    reverse_proxy circle-be-api:8000
}
```
Then reload Caddy **without restarting avora**:
```bash
docker exec deploy-caddy-1 caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile
```
(Use the in-container config path Caddy was started with — the `Destination` from
step 1's mounts, typically `/etc/caddy/Caddyfile`.)

## 6. DNS cutover
Point `api.circle.optiminastic.com`'s **A record** at the **VPS IP** (it currently
points at Render). Once it resolves to the VPS, Caddy auto-issues the TLS cert on
first request.

## 7. Verify
```bash
curl -fsS https://api.circle.optiminastic.com/api/health   # {"status":"ok","database":"up"}
curl -fsS https://api.circle.optiminastic.com/             # app info JSON
```
Then load the Vercel frontend (`https://circle.optiminastic.com`) and confirm it
reads data + the Question Library shows the banks.

## 8. Decommission Render
Once the VPS serves the live domain and everything works, suspend/delete the
Render service so only one backend runs.

---

## Day-2 ops
- **Redeploy:** `cd /opt/circle-be && git pull && docker compose up -d --build`
- **Logs / restart:** `docker compose logs -f api` · `docker compose restart api`
- **Change env:** edit `.env` → `docker compose up -d`.
- **Remove circle-be:** `docker compose down`, then delete the Caddy site block and reload Caddy.

## Notes
- DB backups are Neon's responsibility (branching/PITR) — nothing to back up on the VPS.
- The avora stack is untouched: we only added one container + one Caddy site block.
- Real secrets live only in the server `.env` (git-ignored); `.env.production.example` has placeholders.
