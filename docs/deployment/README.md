# Akesis Deployment & Infrastructure Guide

```text
Document ID: DEP-001
Status: Authoritative V1 Deployment Guide
Target Environment: Single-Host Production / Staging
Runtime: Python 3.12+ / Docker Engine / PostgreSQL 16
```

---

## 1. System Requirements & Prerequisites

### 1.1 Host Compute & OS
* **Operating System:** Linux (Ubuntu 22.04 LTS, Debian 12, or RHEL 9 recommended).
* **CPU:** Minimum 2 vCPUs (4 vCPUs recommended to support concurrent Docker sandboxes).
* **RAM:** Minimum 4 GB RAM (8 GB recommended; sandboxes consume 512 MB per job).
* **Disk:** Minimum 20 GB SSD storage (for git checkout workspaces and Docker image caches).

### 1.2 Required Software
1. **Python 3.12+** and **`uv`** package manager.
2. **Docker Engine** (>= 24.0). Only the dedicated validation executor user (`akesis-exec`) is in the `docker` group. The API and worker user (`akesis`) never is (Phase 1.14b).
3. **PostgreSQL 16** (either local, containerized, or managed RDS/Cloud SQL).
4. **Git** (>= 2.40) installed on system PATH.
5. **Reverse Proxy / TLS Terminator:** Nginx, Caddy, Traefik, or AWS ALB.

---

## 2. Secrets & Environment Configuration

Each service has its **own** `.env` in its own owner-only working directory (Phase 1.14c):
- the API: `/etc/akesis/api/.env`, owned by `akesis`
- the worker: `/etc/akesis/worker/.env`, owned by `akesis-worker`
- the executor has none

Each directory is 0700 and each file 0600. The settings loader reads `.env` from the working directory, so separate directories are what keep each service's credentials out of the other's process. Use the table in Step 4a to decide which variables go in which file. The example below lists every variable.

```ini
# Core Environment
ENVIRONMENT=production
LOG_LEVEL=INFO

# Database Connection (AsyncPG)
DATABASE_URL=postgresql+asyncpg://akesis_user:SECURE_DB_PASSWORD@localhost:5432/akesis_prod

# GitHub Integration
GITHUB_WEBHOOK_SECRET=your_generated_hmac_secret_32_chars_min
GITHUB_API_URL=https://api.github.com
# GitHub App identity: required for every GitHub call (verification, snapshots, branch and PR creation)
GITHUB_APP_ID=123456
# Multi-line PEM: place this in each service's .env as a double-quoted value, not in the systemd
# EnvironmentFile (systemd cannot carry multi-line values). The settings loader reads .env from the
# working directory. The settings loader is covered by tests (quoted multi-line and escaped forms).
# Confirm the same on the deployed host during the live smoke test.
GITHUB_APP_PRIVATE_KEY="-----BEGIN PRIVATE KEY-----
...PKCS#8 body...
-----END PRIVATE KEY-----"

# GitHub sign-in (ADR-0012); all required in production. See local-development.md Step 3b.
GITHUB_APP_CLIENT_ID=Iv1.xxxxxxxxxxxxxxxx
GITHUB_APP_CLIENT_SECRET=generated_in_the_github_app_settings
AUTH_REDIRECT_URI=https://akesis.example.com/auth/callback
AUTH_COOKIE_SECURE=true
AUTH_TOKEN_ENCRYPTION_KEY=output_of_Fernet.generate_key

# Retry worker and webhook retry policy (defaults shown; see ADR-0010)
WEBHOOK_RETRY_MAX_ATTEMPTS=5
WEBHOOK_RETRY_BASE_DELAY_SECONDS=60
WEBHOOK_RETRY_MAX_DELAY_SECONDS=900
WEBHOOK_RETRY_PROVIDER_DELAY_CEILING_SECONDS=86400
WORKER_POLL_SECONDS=15
WORKER_BATCH_SIZE=20
WORKER_HEARTBEAT_PATH=/run/akesis/worker-heartbeat.json
WORKER_HEARTBEAT_MAX_AGE_SECONDS=300

# Workflow log retrieval by the worker (Phase 1.10; defaults shown). The GitHub App needs
# Actions: read. Lease must exceed the deadline; every archive limit fails closed.
LOG_RETRIEVAL_MAX_ATTEMPTS=5
LOG_RETRIEVAL_DEADLINE_SECONDS=120
LOG_RETRIEVAL_LEASE_SECONDS=600
LOG_ARCHIVE_MAX_COMPRESSED_BYTES=20000000
LOG_ARCHIVE_MAX_ENTRIES=500
LOG_ARCHIVE_MAX_UNCOMPRESSED_BYTES=100000000
LOG_ARCHIVE_MAX_FILE_BYTES=20000000
LOG_ARCHIVE_MAX_COMPRESSION_RATIO=100
LOG_PARSE_MAX_CHARS=2000000
# Hosts GitHub may redirect log downloads to; anything else is refused.
LOG_DOWNLOAD_HOSTS=actions.githubusercontent.com,blob.core.windows.net

# Google Gemini AI Integration
GEMINI_API_KEY=your_gemini_api_key_from_google_ai_studio
GEMINI_MODEL=gemini-1.5-flash
GEMINI_TIMEOUT_SECONDS=30.0

# Slack Integration
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/T00/B00/YOUR_WEBHOOK_TOKEN
SLACK_SIGNING_SECRET=your_slack_app_signing_secret
SLACK_CHANNEL_ID=C0123456789
APPROVAL_TTL_HOURS=24

# Docker Sandbox Parameters
SANDBOX_IMAGE=akesis-validator:v1
SANDBOX_TIMEOUT_SECONDS=30.0
SANDBOX_MEMORY_LIMIT=512m
SANDBOX_CPU_LIMIT=1.0
SANDBOX_BASE_DIR=/tmp/akesis/sandbox
```

---

## 3. Step-by-Step Deployment Procedure

### Step 1: Provision Database & Run Migrations
Ensure PostgreSQL 16 is running and the database exists:

```bash
# Provision PostgreSQL via Docker Compose if running locally:
docker compose -f docker/docker-compose.yml up -d postgres

# Run Alembic async migrations to create 'approvals' and 'mutations' tables:
uv run alembic upgrade head
```

Verify migration status:
```bash
uv run alembic current
```

---

### Step 2: Build the Validator Docker Image
Akesis requires the local Docker image `akesis-validator:v1` to execute sandbox validations:

```bash
docker build -f docker/Dockerfile.validator -t akesis-validator:v1 .
```

Verify the image is present:
```bash
docker images | grep akesis-validator
```

Validation containers run with a read-only root filesystem and a 64 MiB `/tmp` tmpfs (Phase 1.13), plus `--network none`, `--cap-drop ALL`, `no-new-privileges`, uid 1000, `--pids-limit 256` and the configured memory and CPU limits. The tmpfs counts against `SANDBOX_MEMORY_LIMIT`. Run the Docker tests against the built image to confirm compatibility on a new host: `uv run pytest -m docker tests/integration/test_docker_sandbox.py`.

---

### Step 3: Install Dependencies
```bash
uv sync --no-dev
```

---

### Step 4: Configure systemd Service
Create a systemd unit file at `/etc/systemd/system/akesis.service`:

```ini
[Unit]
Description=Akesis CI/CD Remediation Service
After=network.target postgresql.service

[Service]
Type=simple
# The public-facing API: no Docker, no executor socket, no executor keys (Phase 1.14c).
User=akesis
Group=akesis
# Its own .env (API credentials only) lives here; the code is imported from /opt/akesis.
WorkingDirectory=/etc/akesis/api
Environment=PYTHONPATH=/opt/akesis
ExecStart=/opt/akesis/.venv/bin/uvicorn src.apps.api.main:app --host 127.0.0.1 --port 8000 --workers 2 --access-log
Restart=always
RestartSec=5s
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
```

Enable and start the service:
```bash
sudo systemctl daemon-reload
sudo systemctl enable akesis
sudo systemctl start akesis
sudo systemctl status akesis
```

---

### Step 4b (required in production): Validation Executor

Production refuses to start without `SANDBOX_EXECUTOR_URL` (Phase 1.14b). In production, `DockerSandboxRunner` cannot be constructed or run anywhere except inside the executor, so the API never runs Docker. Outside production, leaving `SANDBOX_EXECUTOR_URL` unset keeps local Docker validation for development.

Create a dedicated executor user. It is the only member of the `docker` group, and it runs as UID 1000, the container user. Give it a working directory that contains no `.env`. Then remove Docker access from the application user:

```bash
sudo useradd --system --user-group --home-dir /opt/akesis-exec --shell /usr/sbin/nologin akesis-exec
sudo usermod -aG docker akesis-exec
sudo gpasswd -d akesis docker   # the API user must not be in the docker group
sudo useradd --system --user-group --home-dir /opt/akesis --shell /usr/sbin/nologin akesis-worker
sudo usermod -aG akesis-exec akesis-worker   # the worker reaches the executor socket; the API does not
sudo mkdir -p /opt/akesis-exec /etc/akesis-exec && sudo chown akesis-exec: /opt/akesis-exec
```

If UID 1000 is already taken on the host, the container user must change with it. That is not supported yet, so use a host where UID 1000 is free for the executor.

Then create `/etc/systemd/system/akesis-executor.service`:

```ini
[Unit]
Description=Akesis validation executor
After=docker.service
Requires=docker.service

[Service]
Type=simple
User=akesis-exec
Group=akesis-exec
SupplementaryGroups=docker
WorkingDirectory=/opt/akesis-exec
# Only AKESIS_EXECUTOR_* values: never the application .env
EnvironmentFile=/etc/akesis-exec/executor.env
Environment=PYTHONPATH=/opt/akesis
ExecStart=/opt/akesis/.venv/bin/python -m src.apps.executor
RuntimeDirectory=akesis-exec
RuntimeDirectoryMode=0750
StateDirectory=akesis-exec
StateDirectoryMode=0700
ProtectSystem=strict
NoNewPrivileges=yes
Restart=always
RestartSec=5s
```

Do not set `ENVIRONMENT` for the executor: it takes no application configuration, and the guard refuses to start if `ENVIRONMENT=production` is set.

`executor.env` sets:
- `AKESIS_EXECUTOR_SOCKET_PATH=/run/akesis-exec/exec.sock`
- `AKESIS_EXECUTOR_WORK_DIR=/var/lib/akesis-exec/work`
- `AKESIS_EXECUTOR_ALLOWED_UIDS` to the caller's UID
- the two keys (`AKESIS_EXECUTOR_REQUEST_KEY` and `AKESIS_EXECUTOR_RESPONSE_KEY`), each at least 32 random characters and different from each other

The worker's `.env` (`/etc/akesis/worker/.env`) sets `SANDBOX_EXECUTOR_URL=unix:///run/akesis-exec/exec.sock` and the same two keys (`SANDBOX_EXECUTOR_REQUEST_KEY`, `SANDBOX_EXECUTOR_RESPONSE_KEY`). The worker reaches the 0660 socket through `SupplementaryGroups=akesis-exec` in its unit, and `AKESIS_EXECUTOR_ALLOWED_UIDS` must name the `akesis-worker` UID (`id -u akesis-worker`). Since Phase 1.14c the remediation worker is the executor's only caller. The API holds no executor configuration, and production refuses to start the API if any `SANDBOX_EXECUTOR_*` value is set.

The executor refuses to start if its working directory has a `.env` or its environment holds any application credential. While the remediation hold is active it answers every job with a signed `rejected/held`.

Verify the isolation after `systemctl start akesis-executor akesis-worker akesis`:

```bash
id -nG akesis                       # API: neither docker nor akesis-exec
id -nG akesis-worker                # worker: akesis-exec, not docker
id -nG akesis-exec                  # executor: docker
stat -c '%a %U:%G' /run/akesis-exec/exec.sock        # 660 akesis-exec:akesis-exec
sudo -u akesis-worker curl -s --unix-socket /run/akesis-exec/exec.sock http://e/healthz  # {"status":"ok"}
sudo -u akesis curl -s --unix-socket /run/akesis-exec/exec.sock http://e/healthz         # Permission denied
sudo -u nobody curl -s --unix-socket /run/akesis-exec/exec.sock http://e/healthz         # Permission denied
sudo -u akesis docker ps            # must fail: permission denied on /var/run/docker.sock
sudo -u akesis-worker docker ps     # must fail as well
sudo grep -c SANDBOX_EXECUTOR /etc/akesis/api/.env   # 0
sudo ls -ld /etc/akesis/api /etc/akesis/worker        # drwx------ akesis / akesis-worker
```

### Step 4a: Configure the Retry Worker (systemd)

The worker runs scheduled webhook verification retries, workflow-log retrieval and, since Phase 1.14c, Slack-approved remediation. It is a separate process from the API, with its own Unix user (`akesis-worker`) and its own environment file. Both services share the PostgreSQL database. Either can be restarted independently.

**Credentials per service (Phase 1.14c).** Each service has its own `.env` in its own working directory. Do not leave a `.env` in `/opt/akesis`.

| Variable | `/etc/akesis/api/.env` (owner `akesis`, 0600) | `/etc/akesis/worker/.env` (owner `akesis-worker`, 0600) |
| :--- | :--- | :--- |
| `DATABASE_URL` | yes | yes |
| `GITHUB_APP_ID`, `GITHUB_APP_PRIVATE_KEY` | yes (webhook provenance verification) | yes (retries, logs, snapshots, mutation) |
| `GITHUB_WEBHOOK_SECRET`, `SLACK_SIGNING_SECRET`, `GITHUB_APP_CLIENT_SECRET`, `AUTH_TOKEN_ENCRYPTION_KEY` | yes | **never** (startup refuses) |
| `GITHUB_APP_CLIENT_ID`, `AUTH_REDIRECT_URI`, `AUTH_COOKIE_SECURE` | yes | no |
| `SLACK_WORKSPACE_ID`, `SLACK_CHANNEL_ID`, `CORS_ALLOWED_ORIGINS`, `TRUSTED_HOSTS` | yes | no (not secrets; unused) |
| `SLACK_APPROVER_USER_IDS` | yes (decision authorization) | yes (re-authorized at execution) |
| `SANDBOX_EXECUTOR_URL`, `SANDBOX_EXECUTOR_REQUEST_KEY`, `SANDBOX_EXECUTOR_RESPONSE_KEY` | **never** (startup refuses) | yes |
| `SERVICE_ROLE` | `api` (default) | `worker` (set in the unit) |
| `GEMINI_API_KEY`, `SLACK_WEBHOOK_URL` | no | not yet: used only by diagnosis and card posting, which nothing schedules until the condition-6 handoff |

Create `/etc/systemd/system/akesis-worker.service`:

```ini
[Unit]
Description=Akesis worker (webhook retries, workflow logs, approved remediation)
After=network.target postgresql.service akesis-executor.service
Requires=postgresql.service
Wants=akesis-executor.service

[Service]
Type=simple
User=akesis-worker
Group=akesis-worker
# The executor socket (0660, group akesis-exec). Never the docker group.
SupplementaryGroups=akesis-exec
# Its own .env (worker credentials only) lives here; the code is imported from /opt/akesis.
WorkingDirectory=/etc/akesis/worker
Environment=PYTHONPATH=/opt/akesis
Environment=SERVICE_ROLE=worker
ExecStart=/opt/akesis/.venv/bin/python -m src.apps.worker
# Heartbeat lives in a service-owned runtime directory (default /run/akesis), never /tmp.
RuntimeDirectory=akesis
RuntimeDirectoryMode=0750
Restart=always
RestartSec=5s
# Watchdog: the worker pings systemd only when a delivery or a pass completes. A frozen or blocked
# worker stops pinging and is restarted. Keep WatchdogSec above the remediation deadline (480s).
Type=simple
NotifyAccess=main
WatchdogSec=600s
# A stop finishes only the work item in flight and releases every unstarted claim. The longest item
# is one approved remediation, bounded by REMEDIATION_DEADLINE_SECONDS (480s), so allow more than
# that before SIGKILL. A remediation killed mid-mutation is never retried automatically.
KillSignal=SIGTERM
TimeoutStopSec=540s
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes

[Install]
WantedBy=multi-user.target
```

Enable and start the worker, then confirm readiness:

```bash
sudo systemctl daemon-reload
sudo systemctl enable akesis-worker
sudo systemctl start akesis-worker
/opt/akesis/.venv/bin/python -m src.apps.worker.healthcheck
```

On SIGTERM the worker finishes only the item in flight and releases every claim it has not started. The longest item is one approved remediation, bounded by the 480-second remediation deadline; a delivery verification is bounded by 240 seconds. `TimeoutStopSec=540s` therefore covers the worst case. Validated in user scope: a SIGKILL restarts the worker, a frozen worker (SIGSTOP) is restarted by the watchdog within WatchdogSec, pings advance during normal operation, a clean stop exits 0 and systemd removes the runtime directory (so the probe then reports no heartbeat), and a frozen worker is reported stale. Not validated here: the `akesis` system account, `/run` ownership, `ProtectSystem`, and `ProtectHome`. One interaction to know about: `PrivateTmp=yes` hides `/tmp` from the service, so `WorkingDirectory` must not be under `/tmp`. A worker killed mid-verification leaves that one row `verifying`; it is reclaimed after 10 minutes and counts as an attempt. Claims it had not started are not left behind on a clean stop. The worker requires `GITHUB_APP_ID` and `GITHUB_APP_PRIVATE_KEY`. Without them it claims nothing, so due deliveries wait visibly and no attempts are consumed.

---

### Step 5: Configure Reverse Proxy (Nginx)
Create `/etc/nginx/sites-available/akesis.conf`:

```nginx
server {
    listen 80;
    server_name akesis.yourdomain.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name akesis.yourdomain.com;

    ssl_certificate /etc/letsencrypt/live/akesis.yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/akesis.yourdomain.com/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Request-ID $request_id;

        proxy_connect_timeout 10s;
        proxy_read_timeout 60s;
        proxy_send_timeout 60s;
    }
}
```

Enable configuration and reload Nginx:
```bash
sudo ln -s /etc/nginx/sites-available/akesis.conf /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

---

## 4. Webhook & Integration Setup

### 4.1 GitHub Repository Webhook
1. Navigate to your GitHub repository $\rightarrow$ **Settings** $\rightarrow$ **Webhooks** $\rightarrow$ **Add webhook**.
2. **Payload URL:** `https://akesis.yourdomain.com/v1/webhooks/github`
3. **Content type:** `application/json`
4. **Secret:** The exact value of `GITHUB_WEBHOOK_SECRET` in the API's `.env`.
5. **Events:** Select *"Let me select individual events"* $\rightarrow$ Check **Workflow runs**.
6. Click **Add webhook**.

---

### 4.2 Slack App Interactive Components
1. Go to [api.slack.com/apps](https://api.slack.com/apps) $\rightarrow$ Select your Slack App.
2. Navigate to **Interactivity & Shortcuts**.
3. Toggle **Interactivity** to `ON`.
4. **Request URL:** `https://akesis.yourdomain.com/v1/slack/interactions`
5. Navigate to **Incoming Webhooks** and generate a webhook URL for your target channel.
6. Copy the **Signing Secret** from **Basic Information** into `SLACK_SIGNING_SECRET`.

---

## 5. Health Check & Validation

Verify deployment status using curl:

```bash
# Check process liveness
curl -i https://akesis.yourdomain.com/health/liveness

# Check readiness
curl -i https://akesis.yourdomain.com/health/readiness
```

Check the retry worker (no network access needed):

```bash
/opt/akesis/.venv/bin/python -m src.apps.worker.healthcheck
```

Exit code 0 means the worker's heartbeat is fresh and in the `ok` state. Exit code 1 prints the reason. Run the probe as the `akesis` user or as root: the runtime directory is `0750`, so other users cannot read the heartbeat.

---

## 6. Rollback Procedure

If a deployed version causes errors:
1. Stop the application service:
   ```bash
   sudo systemctl stop akesis
   ```
2. Check out the previous known-good release commit or tag:
   ```bash
   git checkout v0.1.0-previous
   uv sync --no-dev
   ```
3. If database migrations need to be downgraded:
   ```bash
   uv run alembic downgrade -1
   ```
4. Restart service and verify health:
   ```bash
   sudo systemctl start akesis
   curl -f http://127.0.0.1:8000/health/liveness
   ```
