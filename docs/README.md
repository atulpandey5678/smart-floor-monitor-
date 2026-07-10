# Cologic Shop Floor Tracker — Documentation

Welcome to the Cologic Shop Floor Tracker documentation! This directory contains comprehensive guides for deploying, configuring, and maintaining the system.

---

## 📚 Available Documentation

### Deployment Guides

#### 🖱️ [GCP UI Guide](./GCP_UI_GUIDE.md) (Visual/No Command Line)
**NEW!** Complete visual walkthrough with screen-by-screen instructions.

**Best for**: Visual learners, first-time GCP users, no terminal experience needed

**Covers**:
- ASCII art mockups of every GCP Console screen
- Exact button locations and click sequences
- Form field examples with sample values
- Pure UI approach - no command line needed
- 11 major sections with visual annotations

**Time to complete**: 30-45 minutes  
**Format**: Visual with ASCII diagrams

---

#### 🚀 [GCP Deployment Guide](./GCP_DEPLOYMENT_GUIDE.md) (Comprehensive)
Complete step-by-step guide for deploying on Google Cloud Platform.

**Best for**: First-time deployers, production deployments, detailed setup

**Covers**:
- Complete GCP project setup
- Compute Engine VM configuration
- Cloud Storage setup
- SSL/HTTPS configuration
- Edge agent installation (Windows & Linux)
- Monitoring and maintenance
- Security best practices
- Comprehensive troubleshooting

**Time to complete**: 30-45 minutes

---

#### ⚡ [GCP Quick Start Guide](./GCP_QUICK_START.md) (Fast Track)
Condensed version for quick deployments.

**Best for**: Experienced users, rapid deployments, testing

**Covers**:
- Automated resource creation commands
- Quick configuration steps
- Essential setup only
- Quick reference commands

**Time to complete**: 15-20 minutes

---

## 🎯 Which Guide Should I Use?

### Use the **Visual UI Guide** if:
- ✅ You prefer clicking buttons over typing commands
- ✅ This is your first time using GCP
- ✅ You want to see exactly what each screen looks like
- ✅ You're not comfortable with command line
- ✅ You want step-by-step screenshots (ASCII art)

### Use the **Comprehensive Guide** if:
- ✅ This is your first deployment
- ✅ You need detailed explanations
- ✅ You want security best practices
- ✅ You need troubleshooting help
- ✅ This is a production deployment
- ✅ You're comfortable with gcloud CLI commands

### Use the **Quick Start** if:
- ✅ You're familiar with GCP
- ✅ You've deployed before
- ✅ You need a quick reference
- ✅ This is a development/test deployment
- ✅ You prefer command-line automation

---

## 🏗️ Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                   Google Cloud Platform                      │
│  ┌───────────────────────────────────────────────────────┐  │
│  │  Cloud Server (GCP Compute Engine)                    │  │
│  │  • FastAPI application                                │  │
│  │  • SQLite database (WAL mode)                        │  │
│  │  • Staff dashboard                                    │  │
│  │  • Ingest API                                         │  │
│  └───────────────────────────────────────────────────────┘  │
│                           ▲                                  │
│  ┌───────────────────────────────────────────────────────┐  │
│  │  Cloud Storage (GCS)                                  │  │
│  │  • Alert event images                                 │  │
│  │  • Snapshot thumbnails                                │  │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
                           ▲
                           │ HTTPS (TLS 1.2+)
                           │
         ┌─────────────────┴──────────────────┐
         │                                     │
┌────────▼────────┐                  ┌────────▼────────┐
│  Edge Agent 1   │                  │  Edge Agent 2   │
│  (Factory Site) │                  │  (Factory Site) │
│  • Camera feeds │                  │  • Camera feeds │
│  • CV pipeline  │                  │  • CV pipeline  │
│  • Local queue  │                  │  • Local queue  │
│  • Offline mode │                  │  • Offline mode │
└─────────────────┘                  └─────────────────┘
```

---

## 🔑 Key Components

### Cloud Server
- **Location**: GCP Compute Engine VM
- **Purpose**: Central hub for data aggregation and staff dashboard
- **Database**: SQLite (WAL mode)
- **Storage**: Google Cloud Storage for images
- **Communication**: HTTPS ingest API

### Edge Agents
- **Location**: On-site at each production facility
- **Purpose**: Camera feed processing and CV pipeline
- **Database**: Local SQLite queue (durable, offline-capable)
- **Communication**: HTTPS client to cloud server
- **Resilience**: Continues operating during cloud outages

---

## 📋 Prerequisites

### For Cloud Deployment
- GCP account with billing enabled
- gcloud CLI installed
- Domain name (optional, recommended for HTTPS)
- Basic knowledge of Linux/Unix commands

### For Edge Agents
- Windows 10/11 or Linux machine at each site
- Python 3.10+
- Network access to cameras (RTSP)
- HTTPS connectivity to cloud server

---

## 🚦 Deployment Steps Overview

### 1. Cloud Infrastructure Setup
1. Create GCP project
2. Enable required APIs
3. Create Compute Engine VM
4. Reserve static IP
5. Create GCS bucket
6. Create service account

### 2. Cloud Application Deployment
1. SSH into VM
2. Install Docker
3. Clone repository
4. Configure environment variables
5. Deploy with Docker Compose
6. Setup HTTPS (Caddy or Nginx)

### 3. Edge Agent Installation
1. Install on each factory machine
2. Configure cloud server URL
3. Configure camera connections
4. Install as Windows service or systemd unit
5. Verify connectivity

---

## 📊 System Requirements

### Cloud Server (GCP VM)
- **Minimum**: e2-medium (2 vCPUs, 4 GB RAM)
- **Recommended**: e2-standard-2 (2 vCPUs, 8 GB RAM)
- **Disk**: 50 GB (SSD recommended)
- **OS**: Ubuntu 22.04 LTS

### Edge Agent Machine
- **CPU**: 4+ cores (for CV processing)
- **RAM**: 8+ GB
- **Disk**: 50+ GB
- **OS**: Windows 10/11 or Linux (Ubuntu 20.04+)
- **GPU**: Optional (NVIDIA for accelerated CV)

---

## 💰 Cost Estimate (Monthly)

### GCP Resources
| Resource | Configuration | Est. Cost |
|----------|--------------|-----------|
| Compute Engine | e2-medium | $24/month |
| Static IP | 1 address | $7/month |
| Cloud Storage | ~10 GB | $0.20/month |
| Egress Bandwidth | ~50 GB/month | $6/month |
| **Total** | | **$35-50/month** |

### Scaling Considerations
- Add $24/month per VM upgrade (e.g., e2-standard-2)
- Add $0.02/GB/month for additional storage
- Multiple edge agents: No additional cloud cost

---

## 🔒 Security Features

- **HTTPS Only**: All communication encrypted (TLS 1.2+)
- **API Key Authentication**: Ingest API requires secret key
- **Session-Based Auth**: Staff dashboard uses secure cookies
- **Credential Isolation**: RTSP credentials never leave edge agents
- **Principle of Least Privilege**: Separate service accounts per component
- **Audit Logging**: All API calls logged

---

## 🛠️ Automation Scripts

### `scripts/deploy-gcp.sh`
Automated GCP infrastructure setup script.

**Usage**:
```bash
cd scripts
./deploy-gcp.sh
```

**What it does**:
- Creates GCP project
- Enables APIs
- Creates VM and firewall rules
- Reserves static IP
- Creates GCS bucket
- Creates service account
- Generates secrets
- Saves configuration

---

## 📖 Quick Reference

### Essential Commands

#### Cloud Server Management
```bash
# SSH into VM
gcloud compute ssh cologic-cloud-server --zone=us-central1-a

# View logs
docker compose -f deploy/cloud/docker-compose.prod.yml logs -f

# Restart server
docker compose -f deploy/cloud/docker-compose.prod.yml restart

# Check health
curl http://localhost:8000/health
```

#### Edge Agent Management (Linux)
```bash
# Check status
sudo systemctl status cologic-edge-agent

# View logs
sudo journalctl -u cologic-edge-agent -f

# Restart
sudo systemctl restart cologic-edge-agent
```

#### Edge Agent Management (Windows)
```powershell
# Check status
Get-Service "Cologic Edge Agent"

# View logs
Get-Content C:\ProgramData\Cologic\EdgeAgent\logs\edge-agent.log -Tail 50

# Restart
Restart-Service "Cologic Edge Agent"
```

---

## 🆘 Getting Help

### Troubleshooting Checklist
1. Check cloud server logs
2. Verify environment variables
3. Test HTTPS connectivity
4. Check firewall rules
5. Verify API keys match
6. Check disk space
7. Review GCS permissions

### Common Issues

#### Cloud Server Not Accessible
- Verify firewall rules allow ports 80 and 443
- Check Caddy/Nginx is running
- Verify VM is running
- Check static IP is assigned

#### Edge Agent Can't Connect
- Verify HTTPS is working (test with curl)
- Check INGEST_API_KEY matches
- Verify CLOUD_SERVER_BASE_URL is correct
- Check edge agent logs for errors

#### Images Not Uploading to GCS
- Verify service account has Storage Object Admin role
- Check GCS_BUCKET name is correct
- Verify GOOGLE_APPLICATION_CREDENTIALS path
- Test GCS access manually with gsutil

---

## 📞 Support Resources

- **GitHub Repository**: https://github.com/atulpandey5678/smart-floor-monitor-
- **GCP Documentation**: https://cloud.google.com/docs
- **Docker Documentation**: https://docs.docker.com
- **FastAPI Documentation**: https://fastapi.tiangolo.com

---

## 📝 Contributing

Found an error in the documentation? Have suggestions for improvement?

1. Open an issue on GitHub
2. Submit a pull request
3. Contact the development team

---

## 🔄 Version History

- **v2.0** (January 2025): Edge-cloud split architecture
- **v1.0** (December 2024): Initial monolithic deployment

---

## 📄 License

See the main repository LICENSE file for details.

---

**Last Updated**: January 2025  
**Documentation Version**: 2.0
