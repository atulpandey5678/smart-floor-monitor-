# Cologic Shop Floor Tracker — GCP Quick Start Guide

## 🚀 Deploy in 15 Minutes

This is a condensed version of the full deployment guide. Use this for quick deployments when you're already familiar with GCP.

---

## Prerequisites

- GCP account with billing enabled
- `gcloud` CLI installed and authenticated
- Domain name (optional, but recommended for HTTPS)

---

## Step 1: Create GCP Resources (5 minutes)

```bash
# Set variables
export PROJECT_ID="cologic-shop-floor-tracker"
export REGION="us-central1"
export ZONE="us-central1-a"
export BUCKET_NAME="cologic-alert-events"

# Create project
gcloud projects create $PROJECT_ID
gcloud config set project $PROJECT_ID

# Enable billing (do this manually in console)

# Enable APIs
gcloud services enable compute.googleapis.com storage.googleapis.com iam.googleapis.com

# Create VM
gcloud compute instances create cologic-cloud-server \
    --zone=$ZONE \
    --machine-type=e2-medium \
    --image-family=ubuntu-2204-lts \
    --image-project=ubuntu-os-cloud \
    --boot-disk-size=50GB \
    --tags=http-server,https-server \
    --scopes=https://www.googleapis.com/auth/cloud-platform

# Create firewall rules
gcloud compute firewall-rules create allow-http --direction=INGRESS --priority=1000 --network=default --action=ALLOW --rules=tcp:80 --source-ranges=0.0.0.0/0 --target-tags=http-server
gcloud compute firewall-rules create allow-https --direction=INGRESS --priority=1000 --network=default --action=ALLOW --rules=tcp:443 --source-ranges=0.0.0.0/0 --target-tags=https-server
gcloud compute firewall-rules create allow-app-port --direction=INGRESS --priority=1000 --network=default --action=ALLOW --rules=tcp:8000 --source-ranges=0.0.0.0/0 --target-tags=http-server

# Reserve static IP
gcloud compute addresses create cologic-cloud-server-ip --region=$REGION

# Get IP
export STATIC_IP=$(gcloud compute addresses describe cologic-cloud-server-ip --region=$REGION --format="value(address)")
echo "Static IP: $STATIC_IP"

# Assign to VM (stop, assign, start)
gcloud compute instances stop cologic-cloud-server --zone=$ZONE
gcloud compute instances delete-access-config cologic-cloud-server --zone=$ZONE --access-config-name="external-nat"
gcloud compute instances add-access-config cologic-cloud-server --zone=$ZONE --access-config-name="external-nat" --address=cologic-cloud-server-ip
gcloud compute instances start cologic-cloud-server --zone=$ZONE

# Create GCS bucket
gsutil mb -p $PROJECT_ID -c STANDARD -l $REGION gs://$BUCKET_NAME/

# Create service account
gcloud iam service-accounts create cologic-cloud-server --display-name="Cologic Cloud Server"
gcloud projects add-iam-policy-binding $PROJECT_ID \
    --member="serviceAccount:cologic-cloud-server@$PROJECT_ID.iam.gserviceaccount.com" \
    --role="roles/storage.objectAdmin"

# Download service account key
gcloud iam service-accounts keys create gcp-key.json \
    --iam-account=cologic-cloud-server@$PROJECT_ID.iam.gserviceaccount.com

echo "✅ GCP resources created successfully"
echo "📌 Static IP: $STATIC_IP"
echo "📌 Configure DNS A record pointing to this IP"
```

---

## Step 2: Install Docker on VM (3 minutes)

```bash
# SSH into VM
gcloud compute ssh cologic-cloud-server --zone=$ZONE

# Install Docker
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
sudo usermod -aG docker $USER
sudo apt-get install docker-compose-plugin -y

# Verify
docker --version
docker compose version

# Log out and back in
exit
gcloud compute ssh cologic-cloud-server --zone=$ZONE
```

---

## Step 3: Deploy Application (5 minutes)

```bash
# Clone repository
git clone https://github.com/atulpandey5678/smart-floor-monitor-.git
cd smart-floor-monitor-

# Generate secrets
export SECRET_KEY=$(openssl rand -hex 32)
export INGEST_API_KEY=$(openssl rand -hex 32)
export FERNET_KEY=$(python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")

echo "🔑 SAVE THESE CREDENTIALS:"
echo "SECRET_KEY: $SECRET_KEY"
echo "INGEST_API_KEY: $INGEST_API_KEY"
echo "FERNET_KEY: $FERNET_KEY"

# Create .env.prod
cat > .env.prod << EOF
API_HOST=0.0.0.0
API_PORT=8000
DB_PATH=/app/data/tracker.db
SECRET_KEY=$SECRET_KEY
INGEST_API_KEY=$INGEST_API_KEY
FERNET_KEY=$FERNET_KEY
INGEST_MAX_BODY_BYTES=10485760
CLOUD_SERVER_BASE_URL=https://YOUR_DOMAIN_OR_IP
GCS_BUCKET=$BUCKET_NAME
GOOGLE_APPLICATION_CREDENTIALS=/run/secrets/gcp-key.json
LIVE_STATE_STALENESS_SECONDS=6
CLAUDE_API_KEY=
EOF

# Update docker-compose to mount GCS key
cat >> deploy/cloud/docker-compose.prod.yml << EOF
      - ./gcp-key.json:/run/secrets/gcp-key.json:ro
EOF

# Start container
docker compose -f deploy/cloud/docker-compose.prod.yml up -d

# Check logs
docker compose -f deploy/cloud/docker-compose.prod.yml logs -f
```

From your **local machine**, upload the GCS key:

```bash
gcloud compute scp gcp-key.json cologic-cloud-server:~/smart-floor-monitor-/gcp-key.json --zone=$ZONE
```

Restart the container on the VM:

```bash
docker compose -f deploy/cloud/docker-compose.prod.yml restart
```

---

## Step 4: Setup HTTPS (2 minutes)

### Option A: With Domain Name (Recommended)

```bash
# Configure DNS first: Add A record pointing to $STATIC_IP

# Install Caddy
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update
sudo apt install caddy

# Configure Caddy
sudo tee /etc/caddy/Caddyfile > /dev/null << EOF
tracker.yourdomain.com {
    reverse_proxy localhost:8000
    log {
        output file /var/log/caddy/access.log
        format json
    }
}
EOF

# Reload Caddy
sudo systemctl reload caddy

# Test
curl https://tracker.yourdomain.com/health
```

### Option B: Without Domain (Self-Signed Certificate)

```bash
# Generate self-signed certificate
sudo openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
    -keyout /etc/ssl/private/selfsigned.key \
    -out /etc/ssl/certs/selfsigned.crt \
    -subj "/C=US/ST=State/L=City/O=Organization/CN=$STATIC_IP"

# Install nginx
sudo apt-get install nginx -y

# Configure nginx
sudo tee /etc/nginx/sites-available/cologic > /dev/null << EOF
server {
    listen 443 ssl;
    server_name $STATIC_IP;
    ssl_certificate /etc/ssl/certs/selfsigned.crt;
    ssl_certificate_key /etc/ssl/private/selfsigned.key;
    location / {
        proxy_pass http://localhost:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
    }
}
EOF

sudo ln -s /etc/nginx/sites-available/cologic /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

---

## Step 5: Access Dashboard

1. Open browser: `https://tracker.yourdomain.com` or `https://YOUR_STATIC_IP`
2. Login with default credentials:
   - Username: `admin`
   - Password: `admin`
3. **Change password immediately**

---

## Edge Agent Setup (Quick)

### Windows:

```powershell
git clone https://github.com/atulpandey5678/smart-floor-monitor-.git
cd smart-floor-monitor-

# Configure .env
@"
EDGE_AGENT_MODE=true
CLOUD_SERVER_BASE_URL=https://YOUR_DOMAIN
INGEST_API_KEY=YOUR_INGEST_API_KEY
DB_PATH=./edge_queue.db
SECRET_KEY=not_used
"@ | Out-File -Encoding ASCII .env

# Configure cameras
@"
{
  "machines": [
    {
      "machine_id": "M001",
      "name": "Assembly Line 1",
      "rtsp_url": "rtsp://user:pass@192.168.1.100:554/stream1"
    }
  ]
}
"@ | Out-File -Encoding ASCII camera_config.json

# Install service (run as Administrator)
cd deploy\edge
.\install-windows-service.ps1
```

### Linux:

```bash
git clone https://github.com/atulpandey5678/smart-floor-monitor-.git
cd smart-floor-monitor-

# Configure .env
cat > .env << EOF
EDGE_AGENT_MODE=true
CLOUD_SERVER_BASE_URL=https://YOUR_DOMAIN
INGEST_API_KEY=YOUR_INGEST_API_KEY
DB_PATH=./edge_queue.db
SECRET_KEY=not_used
EOF

# Configure cameras
cat > camera_config.json << EOF
{
  "machines": [
    {
      "machine_id": "M001",
      "name": "Assembly Line 1",
      "rtsp_url": "rtsp://user:pass@192.168.1.100:554/stream1"
    }
  ]
}
EOF

# Install dependencies
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Install service
sudo cp deploy/edge/cologic-edge-agent.service /etc/systemd/system/
sudo nano /etc/systemd/system/cologic-edge-agent.service  # Update paths
sudo systemctl daemon-reload
sudo systemctl enable cologic-edge-agent
sudo systemctl start cologic-edge-agent
sudo systemctl status cologic-edge-agent
```

---

## Verify Deployment

```bash
# Check cloud server health
curl https://YOUR_DOMAIN/health

# Check container status
docker ps

# Check logs
docker compose -f deploy/cloud/docker-compose.prod.yml logs -f

# Check edge agent (Linux)
sudo systemctl status cologic-edge-agent
sudo journalctl -u cologic-edge-agent -f

# Check edge agent (Windows)
Get-Service "Cologic Edge Agent"
Get-Content C:\ProgramData\Cologic\EdgeAgent\logs\edge-agent.log -Tail 50
```

---

## Next Steps

1. **Change default admin password**
2. **Set up backups** (see full guide)
3. **Configure monitoring** (GCP Cloud Monitoring)
4. **Add more edge agents** at additional sites
5. **Review security settings**

---

## Quick Commands Reference

```bash
# Restart cloud server
docker compose -f deploy/cloud/docker-compose.prod.yml restart

# View logs
docker compose -f deploy/cloud/docker-compose.prod.yml logs -f

# Update application
cd ~/smart-floor-monitor-
git pull origin main
docker compose -f deploy/cloud/docker-compose.prod.yml build
docker compose -f deploy/cloud/docker-compose.prod.yml up -d

# Backup database
docker exec <container_id> sqlite3 /app/data/tracker.db ".backup /app/data/backup.db"

# Check disk usage
df -h

# Check memory
free -h

# Restart edge agent (Linux)
sudo systemctl restart cologic-edge-agent

# Restart edge agent (Windows)
Restart-Service "Cologic Edge Agent"
```

---

## Troubleshooting

| Issue | Quick Fix |
|-------|-----------|
| Cloud server not accessible | Check firewall rules, verify Caddy/nginx is running |
| Edge agent can't connect | Verify HTTPS is working, check INGEST_API_KEY |
| GCS upload fails | Check service account permissions, verify bucket exists |
| High memory usage | Upgrade VM to e2-standard-2, clear old database records |
| Dashboard not loading | Check container logs, verify database migrations ran |

---

## Cost Estimate

- **VM (e2-medium)**: ~$24/month
- **Static IP**: ~$7/month
- **Cloud Storage**: ~$0.02/GB/month
- **Total**: ~$35-50/month

---

## Support

- **Full Guide**: See `docs/GCP_DEPLOYMENT_GUIDE.md`
- **GitHub**: https://github.com/atulpandey5678/smart-floor-monitor-
- **Issues**: Open an issue on GitHub

---

**Last Updated**: January 2025  
**Version**: 2.0
