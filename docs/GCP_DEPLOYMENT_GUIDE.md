# Cologic Shop Floor Tracker — Complete GCP Deployment Guide

## Table of Contents

1. [Overview](#overview)
2. [Architecture](#architecture)
3. [Prerequisites](#prerequisites)
4. [Part 1: GCP Project Setup](#part-1-gcp-project-setup)
5. [Part 2: Compute Engine Instance Setup](#part-2-compute-engine-instance-setup)
6. [Part 3: Google Cloud Storage Setup](#part-3-google-cloud-storage-setup)
7. [Part 4: Deploy Cloud Server](#part-4-deploy-cloud-server)
8. [Part 5: Configure Edge Agents](#part-5-configure-edge-agents)
9. [Part 6: SSL/HTTPS Setup](#part-6-sslhttps-setup)
10. [Part 7: Monitoring & Maintenance](#part-7-monitoring--maintenance)
11. [Troubleshooting](#troubleshooting)
12. [Security Best Practices](#security-best-practices)

---

## Overview

This guide walks you through deploying the Cologic Shop Floor Tracker on Google Cloud Platform (GCP). The system uses an **edge-cloud split architecture**:

- **Cloud Server**: Runs on GCP Compute Engine, hosts the dashboard and ingest API
- **Edge Agents**: Run on-site at each production facility, handle camera feeds and CV processing

### What You'll Deploy

- A GCP Compute Engine VM running the Cloud Server (FastAPI + SQLite)
- A Google Cloud Storage (GCS) bucket for alert event images
- SSL/HTTPS certificate for secure communication
- Edge agents at each production site (Windows/Linux)

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      Google Cloud Platform                   │
│  ┌───────────────────────────────────────────────────────┐  │
│  │  Compute Engine (Cloud Server)                        │  │
│  │  • FastAPI application                                │  │
│  │  • SQLite database (WAL mode)                        │  │
│  │  • Staff dashboard                                    │  │
│  │  • Ingest API                                         │  │
│  └───────────────────────────────────────────────────────┘  │
│                           ▲                                  │
│  ┌───────────────────────────────────────────────────────┐  │
│  │  Cloud Storage (GCS Bucket)                           │  │
│  │  • Alert event images                                 │  │
│  │  • Snapshot thumbnails                                │  │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
                           ▲
                           │ HTTPS (TLS)
                           │
         ┌─────────────────┴──────────────────┐
         │                                     │
┌────────▼────────┐                  ┌────────▼────────┐
│  Edge Agent 1   │                  │  Edge Agent 2   │
│  (Factory Site) │                  │  (Factory Site) │
│  • Camera feeds │                  │  • Camera feeds │
│  • CV pipeline  │                  │  • CV pipeline  │
│  • Local queue  │                  │  • Local queue  │
└─────────────────┘                  └─────────────────┘
```

---

## Prerequisites

### Required Accounts & Tools

- **GCP Account**: Active Google Cloud Platform account with billing enabled
- **Domain Name**: (Optional but recommended) For HTTPS setup
- **Local Development Machine**: Windows, macOS, or Linux with:
  - Google Cloud SDK (`gcloud` CLI)
  - SSH client
  - Git
  - Text editor

### Cost Estimate (Monthly)

- **Compute Engine VM** (e2-medium): ~$24/month
- **Cloud Storage**: ~$0.02/GB/month
- **Egress bandwidth**: ~$0.12/GB (first 1TB)
- **Total estimated**: $30-50/month for small deployments

---

## Part 1: GCP Project Setup

### Step 1.1: Create a New GCP Project

1. Go to [GCP Console](https://console.cloud.google.com/)
2. Click the project dropdown at the top
3. Click **"New Project"**
4. Enter project details:
   - **Project name**: `cologic-shop-floor-tracker`
   - **Organization**: (Your organization if applicable)
   - **Location**: (Your organization or "No organization")
5. Click **"Create"**

### Step 1.2: Enable Required APIs

```bash
# Install gcloud CLI if not already installed
# See: https://cloud.google.com/sdk/docs/install

# Authenticate
gcloud auth login

# Set your project
gcloud config set project cologic-shop-floor-tracker

# Enable required APIs
gcloud services enable compute.googleapis.com
gcloud services enable storage.googleapis.com
gcloud services enable iam.googleapis.com
gcloud services enable servicenetworking.googleapis.com
```

### Step 1.3: Set Up Billing

1. Go to **Billing** in the GCP Console
2. Link your project to a billing account
3. Set up budget alerts (recommended: $50/month threshold)

---

## Part 2: Compute Engine Instance Setup

### Step 2.1: Create the VM Instance

```bash
# Create a VM instance for the Cloud Server
gcloud compute instances create cologic-cloud-server \
    --project=cologic-shop-floor-tracker \
    --zone=us-central1-a \
    --machine-type=e2-medium \
    --image-family=ubuntu-2204-lts \
    --image-project=ubuntu-os-cloud \
    --boot-disk-size=50GB \
    --boot-disk-type=pd-balanced \
    --network-tier=PREMIUM \
    --maintenance-policy=MIGRATE \
    --tags=http-server,https-server \
    --scopes=https://www.googleapis.com/auth/cloud-platform
```

**Instance Configuration:**
- **Machine type**: `e2-medium` (2 vCPUs, 4 GB RAM) — adjust based on load
- **OS**: Ubuntu 22.04 LTS
- **Disk**: 50 GB balanced persistent disk
- **Zone**: `us-central1-a` — choose closest to your factories

### Step 2.2: Configure Firewall Rules

```bash
# Allow HTTP traffic
gcloud compute firewall-rules create allow-http \
    --project=cologic-shop-floor-tracker \
    --direction=INGRESS \
    --priority=1000 \
    --network=default \
    --action=ALLOW \
    --rules=tcp:80 \
    --source-ranges=0.0.0.0/0 \
    --target-tags=http-server

# Allow HTTPS traffic
gcloud compute firewall-rules create allow-https \
    --project=cologic-shop-floor-tracker \
    --direction=INGRESS \
    --priority=1000 \
    --network=default \
    --action=ALLOW \
    --rules=tcp:443 \
    --source-ranges=0.0.0.0/0 \
    --target-tags=https-server

# Allow the application port (8000) temporarily for testing
gcloud compute firewall-rules create allow-app-port \
    --project=cologic-shop-floor-tracker \
    --direction=INGRESS \
    --priority=1000 \
    --network=default \
    --action=ALLOW \
    --rules=tcp:8000 \
    --source-ranges=0.0.0.0/0 \
    --target-tags=http-server
```

### Step 2.3: Reserve a Static IP Address

```bash
# Reserve a static external IP
gcloud compute addresses create cologic-cloud-server-ip \
    --project=cologic-shop-floor-tracker \
    --region=us-central1

# Get the IP address
gcloud compute addresses describe cologic-cloud-server-ip \
    --region=us-central1 \
    --format="value(address)"
```

Save this IP address — you'll need it for DNS and edge agent configuration.

### Step 2.4: Assign Static IP to VM

```bash
# Stop the VM
gcloud compute instances stop cologic-cloud-server \
    --zone=us-central1-a

# Assign static IP
gcloud compute instances delete-access-config cologic-cloud-server \
    --zone=us-central1-a \
    --access-config-name="external-nat"

gcloud compute instances add-access-config cologic-cloud-server \
    --zone=us-central1-a \
    --access-config-name="external-nat" \
    --address=cologic-cloud-server-ip

# Start the VM
gcloud compute instances start cologic-cloud-server \
    --zone=us-central1-a
```

---

## Part 3: Google Cloud Storage Setup

### Step 3.1: Create a GCS Bucket

```bash
# Create a bucket for alert event images
gsutil mb -p cologic-shop-floor-tracker \
    -c STANDARD \
    -l us-central1 \
    gs://cologic-alert-events/

# Set lifecycle policy to delete old images after 90 days (optional)
cat > lifecycle.json << EOF
{
  "lifecycle": {
    "rule": [
      {
        "action": {"type": "Delete"},
        "condition": {"age": 90}
      }
    ]
  }
}
EOF

gsutil lifecycle set lifecycle.json gs://cologic-alert-events/
```

### Step 3.2: Create a Service Account

```bash
# Create service account for cloud server
gcloud iam service-accounts create cologic-cloud-server \
    --project=cologic-shop-floor-tracker \
    --display-name="Cologic Cloud Server" \
    --description="Service account for cloud server GCS access"

# Grant Storage Object Admin role
gcloud projects add-iam-policy-binding cologic-shop-floor-tracker \
    --member="serviceAccount:cologic-cloud-server@cologic-shop-floor-tracker.iam.gserviceaccount.com" \
    --role="roles/storage.objectAdmin"

# Create and download key
gcloud iam service-accounts keys create gcp-key.json \
    --iam-account=cologic-cloud-server@cologic-shop-floor-tracker.iam.gserviceaccount.com

# IMPORTANT: Keep this file secure and never commit to version control
```

---

## Part 4: Deploy Cloud Server

### Step 4.1: SSH into the VM

```bash
gcloud compute ssh cologic-cloud-server \
    --project=cologic-shop-floor-tracker \
    --zone=us-central1-a
```

### Step 4.2: Install Docker

```bash
# Update system
sudo apt-get update && sudo apt-get upgrade -y

# Install Docker
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh

# Add your user to docker group
sudo usermod -aG docker $USER

# Install Docker Compose
sudo apt-get install docker-compose-plugin -y

# Verify installation
docker --version
docker compose version

# Log out and back in for group changes to take effect
exit
```

SSH back in:
```bash
gcloud compute ssh cologic-cloud-server \
    --project=cologic-shop-floor-tracker \
    --zone=us-central1-a
```

### Step 4.3: Clone the Repository

```bash
# Install git
sudo apt-get install git -y

# Clone the repository
git clone https://github.com/atulpandey5678/smart-floor-monitor-.git
cd smart-floor-monitor-
```

### Step 4.4: Configure Environment Variables

```bash
# Copy the production environment template
cp .env.prod.example .env.prod

# Generate secrets
SECRET_KEY=$(openssl rand -hex 32)
INGEST_API_KEY=$(openssl rand -hex 32)
FERNET_KEY=$(python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")

# Edit the environment file
nano .env.prod
```

**Fill in the following values:**

```bash
# Server
API_HOST=0.0.0.0
API_PORT=8000

# Database
DB_PATH=/app/data/tracker.db

# Secrets (generated above)
SECRET_KEY=<paste SECRET_KEY here>
INGEST_API_KEY=<paste INGEST_API_KEY here>
FERNET_KEY=<paste FERNET_KEY here>

# Ingest API
INGEST_MAX_BODY_BYTES=10485760

# Cloud server URL (use your static IP or domain)
CLOUD_SERVER_BASE_URL=https://YOUR_STATIC_IP_OR_DOMAIN

# GCS configuration
GCS_BUCKET=cologic-alert-events
GOOGLE_APPLICATION_CREDENTIALS=/run/secrets/gcp-key.json

# Live state cache
LIVE_STATE_STALENESS_SECONDS=6

# AI integration (optional)
CLAUDE_API_KEY=
```

**IMPORTANT**: Save the `INGEST_API_KEY` — you'll need it for edge agent configuration.

### Step 4.5: Upload GCS Service Account Key

From your **local machine** (not the VM):

```bash
# Upload the GCS service account key
gcloud compute scp gcp-key.json cologic-cloud-server:~/smart-floor-monitor-/gcp-key.json \
    --project=cologic-shop-floor-tracker \
    --zone=us-central1-a
```

### Step 4.6: Update Docker Compose for GCS Key

Back on the **VM**, edit the docker-compose file:

```bash
cd ~/smart-floor-monitor-
nano deploy/cloud/docker-compose.prod.yml
```

Add the GCS key mount under `volumes`:

```yaml
volumes:
  - cloud-db:/app/data
  - ./gcp-key.json:/run/secrets/gcp-key.json:ro  # Add this line
```

### Step 4.7: Start the Cloud Server

```bash
# Build and start the container
docker compose -f deploy/cloud/docker-compose.prod.yml up -d

# Check logs
docker compose -f deploy/cloud/docker-compose.prod.yml logs -f

# Verify health
curl http://localhost:8000/health
```

Expected output:
```json
{"status": "healthy", "timestamp": "2024-01-15T10:30:00Z"}
```

### Step 4.8: Test the Dashboard

1. Get your static IP: `gcloud compute addresses describe cologic-cloud-server-ip --region=us-central1 --format="value(address)"`
2. Open in browser: `http://YOUR_STATIC_IP:8000`
3. You should see the login page

**Default admin credentials** (change immediately after first login):
- Username: `admin`
- Password: `admin`

---

## Part 5: Configure Edge Agents

Edge agents run on-site at each production facility. They connect to the cloud server to push events.

### Step 5.1: Prepare Edge Agent Configuration

On each edge machine (Windows or Linux), you'll need:

1. **INGEST_API_KEY**: From Step 4.4
2. **CLOUD_SERVER_BASE_URL**: Your static IP or domain (HTTPS)
3. **Camera configuration**: RTSP URLs and credentials

### Step 5.2: Windows Edge Agent Installation

On the **Windows edge machine**:

```powershell
# Clone the repository
git clone https://github.com/atulpandey5678/smart-floor-monitor-.git
cd smart-floor-monitor-

# Copy and configure environment
cp .env.example .env
notepad .env
```

Configure `.env`:

```bash
# Edge Agent Mode
EDGE_AGENT_MODE=true

# Cloud Server
CLOUD_SERVER_BASE_URL=https://YOUR_DOMAIN_OR_IP
INGEST_API_KEY=<paste your INGEST_API_KEY>

# Local configuration
DB_PATH=./edge_queue.db
SECRET_KEY=not_used_in_edge_mode
```

Create `camera_config.json`:

```json
{
  "machines": [
    {
      "machine_id": "M001",
      "name": "Assembly Line 1",
      "rtsp_url": "rtsp://username:password@192.168.1.100:554/stream1"
    }
  ]
}
```

Install as Windows service:

```powershell
# Run as Administrator
cd deploy\edge
.\install-windows-service.ps1
```

### Step 5.3: Linux Edge Agent Installation

On the **Linux edge machine**:

```bash
# Clone repository
git clone https://github.com/atulpandey5678/smart-floor-monitor-.git
cd smart-floor-monitor-

# Install Python dependencies
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Configure environment
cp .env.example .env
nano .env
```

Configure `.env` (same as Windows above).

Install as systemd service:

```bash
# Copy service file
sudo cp deploy/edge/cologic-edge-agent.service /etc/systemd/system/

# Edit service file to set correct paths
sudo nano /etc/systemd/system/cologic-edge-agent.service

# Enable and start
sudo systemctl daemon-reload
sudo systemctl enable cologic-edge-agent
sudo systemctl start cologic-edge-agent

# Check status
sudo systemctl status cologic-edge-agent
```

### Step 5.4: Verify Edge Agent Connection

Check edge agent logs:

**Windows**:
```powershell
Get-Content C:\ProgramData\Cologic\EdgeAgent\logs\edge-agent.log -Tail 50
```

**Linux**:
```bash
sudo journalctl -u cologic-edge-agent -f
```

Look for:
- `✓ Cloud connectivity verified`
- `✓ Metadata sync successful`
- `✓ Event flush completed`

On the **cloud server dashboard**, you should see:
- Machine status showing as "ONLINE"
- Live heartbeats updating
- Snapshot thumbnails appearing

---

## Part 6: SSL/HTTPS Setup

**IMPORTANT**: Edge agents require HTTPS to connect to the cloud server (Requirement 13.5).

### Option A: Using a Domain Name (Recommended)

#### Step 6.1: Configure DNS

1. Go to your domain registrar (GoDaddy, Namecheap, etc.)
2. Create an A record pointing to your static IP:
   - **Type**: A
   - **Name**: `tracker` (or `@` for root domain)
   - **Value**: Your GCP static IP
   - **TTL**: 3600

Wait 5-10 minutes for DNS propagation.

Verify:
```bash
nslookup tracker.yourdomain.com
```

#### Step 6.2: Install Caddy (Automatic HTTPS)

On the **VM**:

```bash
# Install Caddy
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update
sudo apt install caddy

# Create Caddyfile
sudo nano /etc/caddy/Caddyfile
```

**Caddyfile content**:

```
tracker.yourdomain.com {
    reverse_proxy localhost:8000
    
    # Enable logging
    log {
        output file /var/log/caddy/access.log
        format json
    }
    
    # Security headers
    header {
        Strict-Transport-Security "max-age=31536000; includeSubDomains; preload"
        X-Content-Type-Options "nosniff"
        X-Frame-Options "SAMEORIGIN"
        X-XSS-Protection "1; mode=block"
    }
}
```

```bash
# Reload Caddy
sudo systemctl reload caddy

# Check status
sudo systemctl status caddy
```

Caddy will automatically obtain and renew Let's Encrypt SSL certificates.

#### Step 6.3: Test HTTPS

```bash
curl https://tracker.yourdomain.com/health
```

### Option B: Using IP Address with Self-Signed Certificate

If you don't have a domain, you can use a self-signed certificate (not recommended for production):

```bash
# Generate self-signed certificate
sudo openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
    -keyout /etc/ssl/private/selfsigned.key \
    -out /etc/ssl/certs/selfsigned.crt \
    -subj "/C=US/ST=State/L=City/O=Organization/CN=YOUR_STATIC_IP"

# Install nginx
sudo apt-get install nginx -y

# Configure nginx
sudo nano /etc/nginx/sites-available/cologic
```

**Nginx configuration**:

```nginx
server {
    listen 443 ssl;
    server_name YOUR_STATIC_IP;

    ssl_certificate /etc/ssl/certs/selfsigned.crt;
    ssl_certificate_key /etc/ssl/private/selfsigned.key;

    location / {
        proxy_pass http://localhost:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

```bash
# Enable site
sudo ln -s /etc/nginx/sites-available/cologic /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

**Note**: Edge agents will need to disable SSL verification (insecure) when using self-signed certificates.

---

## Part 7: Monitoring & Maintenance

### Step 7.1: Set Up Monitoring

#### Cloud Monitoring (GCP Native)

```bash
# Install monitoring agent
curl -sSO https://dl.google.com/cloudagents/add-google-cloud-ops-agent-repo.sh
sudo bash add-google-cloud-ops-agent-repo.sh --also-install

# Verify
sudo systemctl status google-cloud-ops-agent
```

In GCP Console:
1. Go to **Monitoring** > **Dashboards**
2. Create a new dashboard
3. Add metrics:
   - CPU utilization
   - Memory utilization
   - Disk I/O
   - Network traffic

#### Application Health Checks

Create a monitoring script:

```bash
nano ~/monitor.sh
```

```bash
#!/bin/bash
HEALTH_URL="http://localhost:8000/health"
RESPONSE=$(curl -s -o /dev/null -w "%{http_code}" $HEALTH_URL)

if [ "$RESPONSE" != "200" ]; then
    echo "Health check failed with status $RESPONSE"
    # Send alert (email, Slack, etc.)
    # Restart container
    docker compose -f ~/smart-floor-monitor-/deploy/cloud/docker-compose.prod.yml restart
fi
```

```bash
chmod +x ~/monitor.sh

# Add to cron (run every 5 minutes)
(crontab -l 2>/dev/null; echo "*/5 * * * * ~/monitor.sh") | crontab -
```

### Step 7.2: Database Backups

Create a backup script:

```bash
nano ~/backup-db.sh
```

```bash
#!/bin/bash
BACKUP_DIR=~/backups
DB_PATH=~/smart-floor-monitor-/deploy/cloud/data/tracker.db
DATE=$(date +%Y%m%d_%H%M%S)

mkdir -p $BACKUP_DIR

# Backup SQLite database
sqlite3 $DB_PATH ".backup $BACKUP_DIR/tracker_$DATE.db"

# Compress
gzip $BACKUP_DIR/tracker_$DATE.db

# Upload to GCS
gsutil cp $BACKUP_DIR/tracker_$DATE.db.gz gs://cologic-backups/

# Keep only last 30 days locally
find $BACKUP_DIR -name "tracker_*.db.gz" -mtime +30 -delete

echo "Backup completed: tracker_$DATE.db.gz"
```

```bash
chmod +x ~/backup-db.sh

# Create GCS bucket for backups
gsutil mb -p cologic-shop-floor-tracker gs://cologic-backups/

# Schedule daily backups at 2 AM
(crontab -l 2>/dev/null; echo "0 2 * * * ~/backup-db.sh") | crontab -
```

### Step 7.3: Log Rotation

```bash
# Docker logs are already rotated (see docker-compose.prod.yml)
# For application logs:

sudo nano /etc/logrotate.d/cologic
```

```
/var/log/caddy/*.log {
    daily
    rotate 14
    compress
    delaycompress
    notifempty
    create 0640 caddy caddy
    sharedscripts
    postrotate
        systemctl reload caddy
    endscript
}
```

### Step 7.4: Update Procedures

```bash
# Create update script
nano ~/update.sh
```

```bash
#!/bin/bash
set -e

echo "Starting update..."

cd ~/smart-floor-monitor-

# Pull latest code
git pull origin main

# Rebuild containers
docker compose -f deploy/cloud/docker-compose.prod.yml build

# Restart with zero downtime
docker compose -f deploy/cloud/docker-compose.prod.yml up -d

echo "Update completed successfully"
```

```bash
chmod +x ~/update.sh
```

---

## Troubleshooting

### Issue: Cloud Server Not Starting

**Check logs**:
```bash
docker compose -f deploy/cloud/docker-compose.prod.yml logs
```

**Common causes**:
- Missing environment variables in `.env.prod`
- Invalid GCS credentials
- Port 8000 already in use

**Solution**:
```bash
# Verify environment
cat .env.prod | grep -v '^#' | grep -v '^$'

# Check port usage
sudo netstat -tlnp | grep 8000

# Restart container
docker compose -f deploy/cloud/docker-compose.prod.yml restart
```

### Issue: Edge Agent Can't Connect

**Check edge agent logs**:
```bash
# Linux
sudo journalctl -u cologic-edge-agent -f

# Windows
Get-Content C:\ProgramData\Cologic\EdgeAgent\logs\edge-agent.log -Tail 50
```

**Common causes**:
- Wrong `CLOUD_SERVER_BASE_URL`
- Invalid `INGEST_API_KEY`
- Firewall blocking HTTPS
- SSL certificate issues

**Solution**:
```bash
# Test connectivity from edge machine
curl -v https://tracker.yourdomain.com/health

# Test ingest API
curl -X POST https://tracker.yourdomain.com/api/ingest/status \
  -H "X-Ingest-Key: YOUR_INGEST_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"machine_id": "test", "status": "ONLINE"}'
```

### Issue: GCS Images Not Uploading

**Check service account permissions**:
```bash
# On VM
gcloud auth activate-service-account --key-file=gcp-key.json
gsutil ls gs://cologic-alert-events/

# Test write access
echo "test" | gsutil cp - gs://cologic-alert-events/test.txt
```

**Common causes**:
- Service account missing Storage Object Admin role
- GCS bucket doesn't exist
- Invalid `GOOGLE_APPLICATION_CREDENTIALS` path

### Issue: Dashboard Not Loading

**Check Caddy status**:
```bash
sudo systemctl status caddy
sudo journalctl -u caddy -f
```

**Check cloud server health**:
```bash
curl http://localhost:8000/health
```

**Common causes**:
- Caddy not running
- Cloud server container stopped
- Firewall blocking port 443

### Issue: High Memory Usage

**Check container stats**:
```bash
docker stats
```

**Solutions**:
- Upgrade VM to e2-standard-2 (2 vCPUs, 8 GB RAM)
- Reduce `LIVE_STATE_STALENESS_SECONDS`
- Clear old events from database

```bash
# Connect to database
docker exec -it <container_id> sqlite3 /app/data/tracker.db

# Delete events older than 90 days
DELETE FROM sessions WHERE start_time < datetime('now', '-90 days');
VACUUM;
```

---

## Security Best Practices

### 1. Secrets Management

- ✅ **DO**: Store secrets in `.env.prod` (git-excluded)
- ✅ **DO**: Use GCP Secret Manager for production
- ❌ **DON'T**: Hardcode secrets in code
- ❌ **DON'T**: Commit `.env.prod` to git

```bash
# Use GCP Secret Manager
gcloud secrets create ingest-api-key --data-file=- <<< "YOUR_KEY"

# Access in VM
gcloud secrets versions access latest --secret=ingest-api-key
```

### 2. Network Security

- ✅ **DO**: Use HTTPS only (never HTTP in production)
- ✅ **DO**: Restrict firewall rules to specific IPs when possible
- ✅ **DO**: Use VPC for edge-to-cloud communication if possible
- ❌ **DON'T**: Expose port 8000 directly to internet

```bash
# Restrict ingest API to known edge agent IPs
gcloud compute firewall-rules update allow-https \
    --source-ranges=EDGE_IP_1,EDGE_IP_2
```

### 3. Database Security

- ✅ **DO**: Enable automated backups
- ✅ **DO**: Use separate volumes for database files
- ✅ **DO**: Encrypt backups before uploading to GCS
- ❌ **DON'T**: Store sensitive data unencrypted

```bash
# Encrypt backups
gpg --symmetric --cipher-algo AES256 tracker_backup.db
gsutil cp tracker_backup.db.gpg gs://cologic-backups/
```

### 4. Access Control

- ✅ **DO**: Use separate service accounts for different components
- ✅ **DO**: Follow principle of least privilege
- ✅ **DO**: Rotate API keys regularly
- ❌ **DON'T**: Use the same key for multiple environments

```bash
# Rotate INGEST_API_KEY
NEW_KEY=$(openssl rand -hex 32)

# Update cloud server
nano .env.prod  # Update INGEST_API_KEY

# Restart cloud server
docker compose -f deploy/cloud/docker-compose.prod.yml restart

# Update all edge agents with new key
```

### 5. Monitoring & Alerts

- ✅ **DO**: Set up uptime monitoring
- ✅ **DO**: Alert on failed authentication attempts
- ✅ **DO**: Monitor disk usage and set alerts
- ✅ **DO**: Review logs regularly

```bash
# Set up disk usage alert
gcloud alpha monitoring policies create \
    --notification-channels=YOUR_CHANNEL_ID \
    --display-name="Disk usage > 80%" \
    --condition-display-name="Disk usage" \
    --condition-threshold-value=0.8 \
    --condition-threshold-duration=300s
```

---

## Additional Resources

- **GitHub Repository**: https://github.com/atulpandey5678/smart-floor-monitor-
- **GCP Documentation**: https://cloud.google.com/docs
- **Docker Documentation**: https://docs.docker.com
- **FastAPI Documentation**: https://fastapi.tiangolo.com

---

## Support

For issues or questions:
1. Check the [Troubleshooting](#troubleshooting) section
2. Review application logs
3. Check GCP status page: https://status.cloud.google.com
4. Open an issue on GitHub

---

**Last Updated**: January 2025  
**Version**: 2.0 (Edge-Cloud Split Architecture)
