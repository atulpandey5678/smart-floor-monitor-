# Cologic Shop Floor Tracker — Deployment Checklist

Use this checklist to track your deployment progress. Print or copy this document and check off items as you complete them.

---

## 📋 Pre-Deployment Checklist

### Prerequisites
- [ ] GCP account created
- [ ] Billing enabled on GCP account
- [ ] gcloud CLI installed on local machine
- [ ] gcloud authenticated (`gcloud auth login`)
- [ ] Git installed on local machine
- [ ] (Optional) Domain name purchased and DNS accessible
- [ ] Edge agent machines identified and accessible

### Information Gathering
- [ ] GCP project ID decided: `____________________`
- [ ] GCP region selected: `____________________`
- [ ] GCP zone selected: `____________________`
- [ ] Domain name (if applicable): `____________________`
- [ ] Number of edge agent sites: `____________________`

---

## 🏗️ Part 1: GCP Infrastructure Setup

### Project Setup
- [ ] GCP project created
- [ ] Billing linked to project
- [ ] Budget alerts configured
- [ ] gcloud project set as active

### API Enablement
- [ ] Compute Engine API enabled
- [ ] Cloud Storage API enabled
- [ ] IAM API enabled

### Compute Engine
- [ ] VM instance created (`cologic-cloud-server`)
- [ ] VM specs verified:
  - [ ] Machine type: e2-medium or higher
  - [ ] OS: Ubuntu 22.04 LTS
  - [ ] Disk: 50 GB minimum
  - [ ] Tags: http-server, https-server
- [ ] SSH access to VM verified

### Networking
- [ ] Firewall rule created for HTTP (port 80)
- [ ] Firewall rule created for HTTPS (port 443)
- [ ] Firewall rule created for app port (port 8000) - temporary
- [ ] Static IP address reserved
- [ ] Static IP assigned to VM
- [ ] Static IP recorded: `____________________`

### Cloud Storage
- [ ] GCS bucket created
- [ ] Bucket name recorded: `____________________`
- [ ] Bucket region matches VM region
- [ ] (Optional) Lifecycle policy configured

### Service Account
- [ ] Service account created (`cologic-cloud-server`)
- [ ] Storage Object Admin role granted
- [ ] Service account key downloaded (`gcp-key.json`)
- [ ] Service account key stored securely (NOT in git)

---

## 🖥️ Part 2: Cloud Server Deployment

### VM Software Installation
- [ ] SSH into VM successful
- [ ] System packages updated (`sudo apt-get update && upgrade`)
- [ ] Docker installed
- [ ] Docker Compose installed
- [ ] Docker versions verified
- [ ] User added to docker group
- [ ] Git installed

### Application Setup
- [ ] Repository cloned from GitHub
- [ ] `.env.prod` file created from `.env.prod.example`
- [ ] Secrets generated:
  - [ ] `SECRET_KEY`: `____________________`
  - [ ] `INGEST_API_KEY`: `____________________`
  - [ ] `FERNET_KEY`: `____________________`
- [ ] Secrets stored securely (password manager, secrets file)
- [ ] `.env.prod` configured with:
  - [ ] `SECRET_KEY`
  - [ ] `INGEST_API_KEY`
  - [ ] `FERNET_KEY`
  - [ ] `CLOUD_SERVER_BASE_URL`
  - [ ] `GCS_BUCKET`
  - [ ] `GOOGLE_APPLICATION_CREDENTIALS`

### GCS Key Upload
- [ ] `gcp-key.json` uploaded to VM via gcloud scp
- [ ] `docker-compose.prod.yml` updated to mount GCS key
- [ ] GCS key path verified in container

### Container Deployment
- [ ] Docker containers built successfully
- [ ] Docker containers started
- [ ] Container health check passing
- [ ] Logs show no errors
- [ ] Database migrations applied successfully
- [ ] Health endpoint responding: `http://STATIC_IP:8000/health`

---

## 🔒 Part 3: HTTPS/SSL Setup

### Option A: Domain Name (Recommended)
- [ ] DNS A record created: `____________________` → `STATIC_IP`
- [ ] DNS propagation verified (nslookup)
- [ ] Caddy installed on VM
- [ ] Caddyfile configured with domain
- [ ] Caddy started and enabled
- [ ] SSL certificate automatically obtained
- [ ] HTTPS working: `https://yourdomain.com/health`

### Option B: IP Address with Self-Signed Certificate
- [ ] Self-signed certificate generated
- [ ] Nginx installed
- [ ] Nginx configured for HTTPS
- [ ] Nginx started and enabled
- [ ] HTTPS working: `https://STATIC_IP/health` (with warning)

### SSL Verification
- [ ] HTTPS endpoint accessible from browser
- [ ] Certificate details verified
- [ ] No SSL errors (or self-signed warning acceptable)
- [ ] HTTP redirects to HTTPS (if configured)

---

## 🏭 Part 4: Edge Agent Deployment

### Edge Agent 1

**Site Name**: `____________________`  
**Machine ID**: `____________________`  
**OS**: [ ] Windows [ ] Linux

#### Installation
- [ ] Repository cloned on edge machine
- [ ] Python dependencies installed
- [ ] `.env` file created and configured:
  - [ ] `EDGE_AGENT_MODE=true`
  - [ ] `CLOUD_SERVER_BASE_URL` set
  - [ ] `INGEST_API_KEY` set (matches cloud)
  - [ ] `DB_PATH` set
- [ ] `camera_config.json` created with camera details

#### Camera Configuration
- [ ] Camera 1 RTSP URL tested: `____________________`
- [ ] Camera 2 RTSP URL tested: `____________________`
- [ ] Camera 3 RTSP URL tested: `____________________`
- [ ] (Add more as needed)

#### Service Installation
**Windows**:
- [ ] PowerShell script run as Administrator
- [ ] NSSM service installed
- [ ] Service started successfully
- [ ] Service set to auto-start

**Linux**:
- [ ] Systemd service file copied
- [ ] Service file paths updated
- [ ] Systemd daemon reloaded
- [ ] Service enabled
- [ ] Service started
- [ ] Service status verified

#### Verification
- [ ] Edge agent logs show successful startup
- [ ] Cloud connectivity verified in logs
- [ ] Metadata sync successful
- [ ] First event flush successful
- [ ] Machine appears as ONLINE in cloud dashboard
- [ ] Heartbeats updating in dashboard
- [ ] Snapshot thumbnails appearing

---

### Edge Agent 2 (Repeat for each site)

**Site Name**: `____________________`  
**Machine ID**: `____________________`  
**OS**: [ ] Windows [ ] Linux

[Repeat checklist items from Edge Agent 1]

---

## 🎛️ Part 5: Dashboard Access & Configuration

### Dashboard Access
- [ ] Dashboard accessible at: `https://____________________`
- [ ] Login page loads correctly
- [ ] Default admin login works (admin/admin)
- [ ] **CRITICAL**: Default admin password changed immediately
- [ ] New admin password recorded securely

### Dashboard Verification
- [ ] All edge agents showing in machine list
- [ ] Machine statuses showing correctly (ONLINE/OFFLINE)
- [ ] Live heartbeats updating
- [ ] Snapshot thumbnails displaying
- [ ] Historical sessions loading
- [ ] Alert images displaying (if any alerts triggered)

### User Management
- [ ] Additional admin users created (if needed)
- [ ] Staff user accounts created
- [ ] User roles configured
- [ ] Test logins for all users

---

## 📊 Part 6: Monitoring & Maintenance Setup

### Cloud Monitoring
- [ ] GCP Cloud Monitoring agent installed
- [ ] Monitoring dashboard created
- [ ] CPU utilization metric added
- [ ] Memory utilization metric added
- [ ] Disk I/O metric added
- [ ] Network traffic metric added

### Health Checks
- [ ] Health check script created (`monitor.sh`)
- [ ] Health check script made executable
- [ ] Health check added to cron (every 5 minutes)
- [ ] Test health check script manually

### Database Backups
- [ ] GCS bucket for backups created
- [ ] Backup script created (`backup-db.sh`)
- [ ] Backup script made executable
- [ ] Backup script added to cron (daily at 2 AM)
- [ ] Test backup manually
- [ ] Verify backup uploaded to GCS
- [ ] Document backup restoration procedure

### Log Management
- [ ] Docker log rotation configured (check docker-compose.yml)
- [ ] Caddy/Nginx log rotation configured (`/etc/logrotate.d/cologic`)
- [ ] Application logs accessible
- [ ] Log retention policy documented

### Alert Configuration
- [ ] Disk usage alert configured (80% threshold)
- [ ] Memory usage alert configured
- [ ] Service downtime alert configured
- [ ] Failed authentication alert configured (optional)
- [ ] Alert notification channel configured (email/SMS/Slack)

---

## 🧪 Part 7: Testing & Validation

### Functional Testing
- [ ] Staff can log in successfully
- [ ] Live tiles update in real-time
- [ ] Machines show correct status
- [ ] Sessions are recorded correctly
- [ ] Alerts are triggered correctly
- [ ] Alert images display correctly
- [ ] Session history is accurate
- [ ] Machine health indicators work

### Connectivity Testing
- [ ] Edge agent → Cloud server connectivity stable
- [ ] Cloud server → GCS connectivity stable
- [ ] Dashboard → Cloud server connectivity stable
- [ ] Test from multiple networks (if applicable)

### Failure Testing
- [ ] Simulate cloud outage (stop cloud server)
  - [ ] Edge agent continues processing locally
  - [ ] Events queue locally
  - [ ] No data loss when cloud returns
- [ ] Simulate network outage
  - [ ] Edge agent handles gracefully
  - [ ] Offline queue builds up
  - [ ] Events flush when network returns
- [ ] Simulate edge agent restart
  - [ ] Service auto-restarts
  - [ ] Queued events preserved
  - [ ] CV pipeline resumes
- [ ] Simulate database corruption (backup restoration test)

### Performance Testing
- [ ] Monitor CPU usage under load
- [ ] Monitor memory usage over time
- [ ] Monitor disk I/O
- [ ] Verify no memory leaks
- [ ] Check database size growth rate
- [ ] Verify GCS upload performance

---

## 📝 Part 8: Documentation

### Deployment Documentation
- [ ] Deployment date recorded: `____________________`
- [ ] Deployment configuration documented
- [ ] Secrets stored in secure location (password manager)
- [ ] Architecture diagram updated (if customized)
- [ ] Network diagram created (if complex setup)

### Operational Procedures
- [ ] Update procedure documented
- [ ] Rollback procedure documented
- [ ] Backup restoration procedure documented
- [ ] Edge agent installation guide created for on-site staff
- [ ] Troubleshooting guide customized for your deployment
- [ ] Emergency contact list created

### Handoff Documentation
- [ ] Admin credentials provided to stakeholders
- [ ] Edge agent credentials documented
- [ ] GCP access provided to operations team
- [ ] Monitoring dashboard access provided
- [ ] Escalation procedures documented

---

## ✅ Part 9: Production Readiness

### Security Review
- [ ] All default passwords changed
- [ ] Secrets not committed to git
- [ ] Service account keys secured
- [ ] Firewall rules reviewed and minimal
- [ ] HTTPS enforced (no HTTP access)
- [ ] Edge agent API keys unique per site
- [ ] GCP IAM roles follow least privilege
- [ ] Audit logging enabled

### Compliance
- [ ] Data retention policy documented
- [ ] Privacy policy updated (if applicable)
- [ ] Camera recording consent (if required)
- [ ] GDPR compliance reviewed (if applicable)
- [ ] Access control policies documented

### Performance
- [ ] Expected load documented
- [ ] Scaling plan documented
- [ ] Resource usage baselines recorded
- [ ] Performance benchmarks documented

### Disaster Recovery
- [ ] Backup schedule confirmed
- [ ] Backup restoration tested
- [ ] RPO (Recovery Point Objective) documented: `____________________`
- [ ] RTO (Recovery Time Objective) documented: `____________________`
- [ ] DR plan documented
- [ ] DR plan tested

---

## 🎉 Part 10: Go-Live

### Pre-Launch
- [ ] Final testing complete
- [ ] Stakeholders notified of go-live date
- [ ] On-call schedule established
- [ ] Monitoring alerts configured
- [ ] Backup verification complete

### Launch
- [ ] Production traffic enabled
- [ ] Edge agents switched to production mode
- [ ] Real-time monitoring active
- [ ] No critical errors in logs
- [ ] Dashboard accessible to all users

### Post-Launch
- [ ] 24-hour monitoring period complete
- [ ] 7-day stability period complete
- [ ] User feedback collected
- [ ] Performance metrics baseline established
- [ ] Issues log started
- [ ] Lessons learned documented

---

## 📞 Support Information

### Key Contacts

**Cloud Operations Team**  
Name: `____________________`  
Email: `____________________`  
Phone: `____________________`

**Edge Agent Site Contact (Site 1)**  
Name: `____________________`  
Email: `____________________`  
Phone: `____________________`

**GCP Account Administrator**  
Name: `____________________`  
Email: `____________________`  
Phone: `____________________`

### External Support
- GitHub Issues: https://github.com/atulpandey5678/smart-floor-monitor-/issues
- GCP Support: https://cloud.google.com/support
- Emergency Escalation: `____________________`

---

## 📊 Deployment Metrics

Record these for reference:

- **Deployment Start Date**: `____________________`
- **Deployment End Date**: `____________________`
- **Total Deployment Time**: `____________________`
- **Number of Edge Agent Sites**: `____________________`
- **Total Cameras Deployed**: `____________________`
- **Cloud Server VM Size**: `____________________`
- **Estimated Monthly Cost**: `$____________________`
- **Production Go-Live Date**: `____________________`

---

## ✍️ Sign-Off

### Deployment Team

**Deployed By**:  
Name: `____________________`  
Signature: `____________________`  
Date: `____________________`

**Reviewed By**:  
Name: `____________________`  
Signature: `____________________`  
Date: `____________________`

**Approved By**:  
Name: `____________________`  
Signature: `____________________`  
Date: `____________________`

---

## 📝 Notes & Customizations

Use this space to record any deployment-specific notes, customizations, or deviations from the standard procedure:

```
_________________________________________________________________________

_________________________________________________________________________

_________________________________________________________________________

_________________________________________________________________________

_________________________________________________________________________
```

---

**Checklist Version**: 2.0  
**Last Updated**: January 2025  
**Compatible With**: Edge-Cloud Split Architecture v2.0

---

**END OF CHECKLIST**

Congratulations on completing your deployment! 🎉
