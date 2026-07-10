#!/bin/bash
#
# Cologic Shop Floor Tracker — Automated GCP Deployment Script
# This script automates the deployment of the cloud server on GCP
#
# Usage: ./deploy-gcp.sh
#
# Prerequisites:
# - gcloud CLI installed and authenticated
# - Billing enabled on GCP project
# - Domain name configured (optional)

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Logging functions
log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check prerequisites
check_prerequisites() {
    log_info "Checking prerequisites..."
    
    if ! command -v gcloud &> /dev/null; then
        log_error "gcloud CLI not found. Please install it first."
        exit 1
    fi
    
    if ! command -v git &> /dev/null; then
        log_error "git not found. Please install it first."
        exit 1
    fi
    
    log_info "✓ Prerequisites check passed"
}

# Prompt for configuration
get_configuration() {
    log_info "Configuration setup..."
    
    read -p "Enter GCP Project ID (default: cologic-shop-floor-tracker): " PROJECT_ID
    PROJECT_ID=${PROJECT_ID:-cologic-shop-floor-tracker}
    
    read -p "Enter GCP Region (default: us-central1): " REGION
    REGION=${REGION:-us-central1}
    
    read -p "Enter GCP Zone (default: us-central1-a): " ZONE
    ZONE=${ZONE:-us-central1-a}
    
    read -p "Enter VM Machine Type (default: e2-medium): " MACHINE_TYPE
    MACHINE_TYPE=${MACHINE_TYPE:-e2-medium}
    
    read -p "Enter GCS Bucket Name (default: cologic-alert-events): " BUCKET_NAME
    BUCKET_NAME=${BUCKET_NAME:-cologic-alert-events}
    
    read -p "Enter your domain name (leave empty for IP-only): " DOMAIN_NAME
    
    log_info "Configuration:"
    log_info "  Project ID: $PROJECT_ID"
    log_info "  Region: $REGION"
    log_info "  Zone: $ZONE"
    log_info "  Machine Type: $MACHINE_TYPE"
    log_info "  Bucket Name: $BUCKET_NAME"
    log_info "  Domain: ${DOMAIN_NAME:-None (will use IP)}"
    
    read -p "Continue with this configuration? (y/n): " CONFIRM
    if [[ "$CONFIRM" != "y" ]]; then
        log_error "Deployment cancelled"
        exit 1
    fi
}

# Create GCP project
create_project() {
    log_info "Creating GCP project: $PROJECT_ID"
    
    if gcloud projects describe $PROJECT_ID &> /dev/null; then
        log_warn "Project $PROJECT_ID already exists, skipping creation"
    else
        gcloud projects create $PROJECT_ID
        log_info "✓ Project created"
    fi
    
    gcloud config set project $PROJECT_ID
    log_info "✓ Project set as active"
}

# Enable APIs
enable_apis() {
    log_info "Enabling required APIs..."
    
    gcloud services enable compute.googleapis.com
    gcloud services enable storage.googleapis.com
    gcloud services enable iam.googleapis.com
    
    log_info "✓ APIs enabled"
}

# Create VM instance
create_vm() {
    log_info "Creating VM instance: cologic-cloud-server"
    
    if gcloud compute instances describe cologic-cloud-server --zone=$ZONE &> /dev/null; then
        log_warn "VM cologic-cloud-server already exists, skipping creation"
        return
    fi
    
    gcloud compute instances create cologic-cloud-server \
        --zone=$ZONE \
        --machine-type=$MACHINE_TYPE \
        --image-family=ubuntu-2204-lts \
        --image-project=ubuntu-os-cloud \
        --boot-disk-size=50GB \
        --boot-disk-type=pd-balanced \
        --network-tier=PREMIUM \
        --maintenance-policy=MIGRATE \
        --tags=http-server,https-server \
        --scopes=https://www.googleapis.com/auth/cloud-platform
    
    log_info "✓ VM instance created"
}

# Create firewall rules
create_firewall_rules() {
    log_info "Creating firewall rules..."
    
    # HTTP
    if gcloud compute firewall-rules describe allow-http &> /dev/null; then
        log_warn "Firewall rule allow-http already exists, skipping"
    else
        gcloud compute firewall-rules create allow-http \
            --direction=INGRESS \
            --priority=1000 \
            --network=default \
            --action=ALLOW \
            --rules=tcp:80 \
            --source-ranges=0.0.0.0/0 \
            --target-tags=http-server
    fi
    
    # HTTPS
    if gcloud compute firewall-rules describe allow-https &> /dev/null; then
        log_warn "Firewall rule allow-https already exists, skipping"
    else
        gcloud compute firewall-rules create allow-https \
            --direction=INGRESS \
            --priority=1000 \
            --network=default \
            --action=ALLOW \
            --rules=tcp:443 \
            --source-ranges=0.0.0.0/0 \
            --target-tags=https-server
    fi
    
    # App port (temporary for testing)
    if gcloud compute firewall-rules describe allow-app-port &> /dev/null; then
        log_warn "Firewall rule allow-app-port already exists, skipping"
    else
        gcloud compute firewall-rules create allow-app-port \
            --direction=INGRESS \
            --priority=1000 \
            --network=default \
            --action=ALLOW \
            --rules=tcp:8000 \
            --source-ranges=0.0.0.0/0 \
            --target-tags=http-server
    fi
    
    log_info "✓ Firewall rules configured"
}

# Reserve static IP
reserve_static_ip() {
    log_info "Reserving static IP address..."
    
    if gcloud compute addresses describe cologic-cloud-server-ip --region=$REGION &> /dev/null; then
        log_warn "Static IP cologic-cloud-server-ip already exists, skipping creation"
    else
        gcloud compute addresses create cologic-cloud-server-ip --region=$REGION
    fi
    
    STATIC_IP=$(gcloud compute addresses describe cologic-cloud-server-ip --region=$REGION --format="value(address)")
    log_info "✓ Static IP reserved: $STATIC_IP"
    
    # Assign to VM
    log_info "Assigning static IP to VM..."
    gcloud compute instances stop cologic-cloud-server --zone=$ZONE
    gcloud compute instances delete-access-config cologic-cloud-server --zone=$ZONE --access-config-name="external-nat" || true
    gcloud compute instances add-access-config cologic-cloud-server --zone=$ZONE --access-config-name="external-nat" --address=cologic-cloud-server-ip
    gcloud compute instances start cologic-cloud-server --zone=$ZONE
    
    log_info "✓ Static IP assigned"
}

# Create GCS bucket
create_gcs_bucket() {
    log_info "Creating GCS bucket: $BUCKET_NAME"
    
    if gsutil ls -b gs://$BUCKET_NAME &> /dev/null; then
        log_warn "Bucket gs://$BUCKET_NAME already exists, skipping creation"
    else
        gsutil mb -p $PROJECT_ID -c STANDARD -l $REGION gs://$BUCKET_NAME/
        log_info "✓ GCS bucket created"
    fi
}

# Create service account
create_service_account() {
    log_info "Creating service account..."
    
    SA_EMAIL="cologic-cloud-server@$PROJECT_ID.iam.gserviceaccount.com"
    
    if gcloud iam service-accounts describe $SA_EMAIL &> /dev/null; then
        log_warn "Service account already exists, skipping creation"
    else
        gcloud iam service-accounts create cologic-cloud-server \
            --display-name="Cologic Cloud Server" \
            --description="Service account for cloud server GCS access"
        
        gcloud projects add-iam-policy-binding $PROJECT_ID \
            --member="serviceAccount:$SA_EMAIL" \
            --role="roles/storage.objectAdmin"
        
        log_info "✓ Service account created"
    fi
    
    # Download key
    log_info "Downloading service account key..."
    gcloud iam service-accounts keys create gcp-key.json \
        --iam-account=$SA_EMAIL
    
    log_info "✓ Service account key downloaded: gcp-key.json"
    log_warn "IMPORTANT: Keep gcp-key.json secure and never commit to version control"
}

# Generate secrets
generate_secrets() {
    log_info "Generating application secrets..."
    
    SECRET_KEY=$(openssl rand -hex 32)
    INGEST_API_KEY=$(openssl rand -hex 32)
    
    # Try to generate Fernet key
    if command -v python3 &> /dev/null; then
        FERNET_KEY=$(python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" 2>/dev/null || openssl rand -base64 32)
    else
        FERNET_KEY=$(openssl rand -base64 32)
    fi
    
    log_info "✓ Secrets generated"
    
    # Save to file
    cat > deployment-secrets.txt << EOF
=================================================================
COLOGIC SHOP FLOOR TRACKER - DEPLOYMENT SECRETS
=================================================================
IMPORTANT: Store these securely. You will need them for configuration.

SECRET_KEY (session signing):
$SECRET_KEY

INGEST_API_KEY (edge agent authentication):
$INGEST_API_KEY

FERNET_KEY (encryption):
$FERNET_KEY

STATIC_IP:
$STATIC_IP

GCS_BUCKET:
$BUCKET_NAME

PROJECT_ID:
$PROJECT_ID
=================================================================
EOF
    
    log_info "✓ Secrets saved to: deployment-secrets.txt"
    log_warn "IMPORTANT: Store deployment-secrets.txt securely"
}

# Display next steps
display_next_steps() {
    log_info ""
    log_info "=============================================="
    log_info "GCP Infrastructure Setup Complete!"
    log_info "=============================================="
    log_info ""
    log_info "Your static IP: $STATIC_IP"
    log_info ""
    log_info "Next Steps:"
    log_info ""
    log_info "1. Configure DNS (if using domain):"
    if [[ -n "$DOMAIN_NAME" ]]; then
        log_info "   Add A record: $DOMAIN_NAME -> $STATIC_IP"
    else
        log_info "   (Skipped - using IP address)"
    fi
    log_info ""
    log_info "2. SSH into VM and deploy application:"
    log_info "   gcloud compute ssh cologic-cloud-server --zone=$ZONE"
    log_info ""
    log_info "3. On the VM, run:"
    log_info "   curl -fsSL https://get.docker.com -o get-docker.sh"
    log_info "   sudo sh get-docker.sh"
    log_info "   sudo usermod -aG docker \$USER"
    log_info "   sudo apt-get install docker-compose-plugin -y"
    log_info "   exit  # Then SSH back in"
    log_info ""
    log_info "4. Clone and configure:"
    log_info "   git clone https://github.com/atulpandey5678/smart-floor-monitor-.git"
    log_info "   cd smart-floor-monitor-"
    log_info "   cp .env.prod.example .env.prod"
    log_info "   nano .env.prod  # Use secrets from deployment-secrets.txt"
    log_info ""
    log_info "5. Upload GCS key from your local machine:"
    log_info "   gcloud compute scp gcp-key.json cologic-cloud-server:~/smart-floor-monitor-/gcp-key.json --zone=$ZONE"
    log_info ""
    log_info "6. Start the application on VM:"
    log_info "   cd ~/smart-floor-monitor-"
    log_info "   docker compose -f deploy/cloud/docker-compose.prod.yml up -d"
    log_info ""
    log_info "7. Setup HTTPS (Caddy or Nginx) - See full documentation"
    log_info ""
    log_info "For detailed instructions, see:"
    log_info "  docs/GCP_DEPLOYMENT_GUIDE.md"
    log_info "  docs/GCP_QUICK_START.md"
    log_info ""
}

# Main execution
main() {
    echo ""
    log_info "=========================================="
    log_info "Cologic Shop Floor Tracker"
    log_info "GCP Automated Deployment Script"
    log_info "=========================================="
    echo ""
    
    check_prerequisites
    get_configuration
    create_project
    enable_apis
    create_vm
    create_firewall_rules
    reserve_static_ip
    create_gcs_bucket
    create_service_account
    generate_secrets
    display_next_steps
    
    echo ""
    log_info "✓ Deployment script completed successfully!"
    echo ""
}

# Run main function
main "$@"
