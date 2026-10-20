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
2. **Docker Engine** (>= 24.0) with Docker daemon access enabled for the application user.
3. **PostgreSQL 16** (either local, containerized, or managed RDS/Cloud SQL).
4. **Git** (>= 2.40) installed on system PATH.
5. **Reverse Proxy / TLS Terminator:** Nginx, Caddy, Traefik, or AWS ALB.

---

## 2. Secrets & Environment Configuration

Create the production `.env` file on the deployment host. Ensure permissions are restricted (`chmod 600 .env`).

```ini
# Core Environment
ENVIRONMENT=production
LOG_LEVEL=INFO

# Database Connection (AsyncPG)
DATABASE_URL=postgresql+asyncpg://akesis_user:SECURE_DB_PASSWORD@localhost:5432/akesis_prod

# GitHub Integration
GITHUB_WEBHOOK_SECRET=your_generated_hmac_secret_32_chars_min
GITHUB_TOKEN=ghp_your_github_personal_access_token_or_app_token
GITHUB_API_URL=https://api.github.com

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
REPO_CHECKOUT_BASE_DIR=/tmp/akesis/repos
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
After=network.target postgresql.service docker.service
Requires=docker.service

[Service]
Type=simple
User=akesis
Group=akesis
WorkingDirectory=/opt/akesis
EnvironmentFile=/opt/akesis/.env
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
4. **Secret:** The exact value of `GITHUB_WEBHOOK_SECRET` in `.env`.
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
