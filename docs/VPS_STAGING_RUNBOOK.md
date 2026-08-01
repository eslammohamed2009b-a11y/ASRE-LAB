# Single-VPS staging topology

Use one Ubuntu 24.04 LTS VPS with at least 2 vCPU, 4 GB RAM, and 60 GB SSD.
This is the lowest-cost topology that keeps the FastAPI process, a separate
Celery worker, and persistent Redis on one host while Supabase remains the
managed database, Auth, and private artifact store.

`Caddy` is the only public service. It terminates TLS and routes the app host
to Next.js and the API host to FastAPI. Redis, API, worker, and frontend have
no host ports and communicate only over the private Compose network.

## Host preparation

1. Point two DNS names at the VPS: one app host and one API host.
2. Install Docker Engine with the Compose plugin and clone this branch on the VPS.
3. Allow inbound TCP 22 (restricted to administrator IPs where possible), TCP 80,
   and TCP 443. Do not open 3000, 6379, or 8000. Permit normal outbound HTTPS
   and DNS so the services can reach Supabase and Caddy can issue certificates.
4. Copy `deploy/vps.env.example` to `deploy/vps.env`, replace every placeholder,
   and set the exact app origin in `ALLOWED_ORIGINS`. Keep this file mode 600 and
   outside Git; it is ignored by the repository.

## Bring up staging compute

Run this from the repository root after DNS resolves:

```text
docker compose --env-file deploy/vps.env -f docker-compose.vps.yml up -d --build
docker compose --env-file deploy/vps.env -f docker-compose.vps.yml ps
docker compose --env-file deploy/vps.env -f docker-compose.vps.yml logs --tail=100 caddy api worker redis
```

Expected healthy services are `caddy`, `frontend`, `api`, `worker`, and `redis`.
Only Caddy publishes ports. Redis uses an append-only file plus a named durable
volume; the API and worker use Supabase only, never local SQLite or local file
storage. `restart: unless-stopped` restores services after a host reboot.

Before live workflow checks, confirm `https://<api-host>/health` returns HTTP
200 and `https://<app-host>` renders over HTTPS. Do not run `supabase db push`:
the SQL Editor migrations must have their migration history reconciled first.

## Operations and recovery

Use `docker compose ... restart worker` for a non-destructive worker restart.
For the release recovery probe, follow `backend/scripts/validate_worker_loss_recovery.ps1`
from a Docker-capable environment configured exclusively for staging. Never run
the probe against production or against a local fallback persistence configuration.
