# GCP Console UI Guide — Visual Screen Flow

> **🎯 Goal**: Deploy Cologic Shop Floor Tracker to Google Cloud Platform using only the web interface (no command line needed!)

---

## 📋 Table of Contents

1. [Getting Started](#1-getting-started)
2. [Create a Project](#2-create-a-project)
3. [Enable Billing](#3-enable-billing)
4. [Create a Virtual Machine](#4-create-a-virtual-machine)
5. [Set Up Firewall Rules](#5-set-up-firewall-rules)
6. [Reserve a Static IP](#6-reserve-a-static-ip)
7. [Create Cloud Storage Bucket](#7-create-cloud-storage-bucket)
8. [Create Service Account](#8-create-service-account)
9. [Connect to Your VM](#9-connect-to-your-vm)
10. [Set Up Application](#10-set-up-application)
11. [Configure Domain & SSL](#11-configure-domain--ssl)

---

## 1. Getting Started

### 🌐 **Open GCP Console**

**Step 1.1**: Go to [https://console.cloud.google.com](https://console.cloud.google.com)

**What you'll see**:
```
┌────────────────────────────────────────────────────────┐
│  Google Cloud Platform                    [User Icon]  │
├────────────────────────────────────────────────────────┤
│  ☰  [Select a project ▼]  [Search]        [? Bell 🔔]│
├────────────────────────────────────────────────────────┤
│                                                         │
│    Welcome to Google Cloud Platform                    │
│                                                         │
│    [ Create or select a project ]                      │
│                                                         │
└────────────────────────────────────────────────────────┘
```

**Step 1.2**: If you're new to GCP:
- You'll see a welcome screen
- Click **"Agree and Continue"** to accept terms
- You may get $300 free credits for 90 days!

---

## 2. Create a Project

### 📁 **Create Your Project**

**Step 2.1**: Click the **project dropdown** at the top
```
┌─────────────────────────────────┐
│  [Select a project ▼]           │  ← Click here
└─────────────────────────────────┘
```

**Step 2.2**: You'll see a popup:
```
┌──────────────────────────────────────────┐
│  Select a project                     ✕  │
├──────────────────────────────────────────┤
│  Recent    All    Starred                │
├──────────────────────────────────────────┤
│                                           │
│  No projects yet                          │
│                                           │
│  [➕ NEW PROJECT]                         │  ← Click here
│                                           │
└──────────────────────────────────────────┘
```

**Step 2.3**: Fill in project details:
```
┌──────────────────────────────────────────┐
│  New Project                          ✕  │
├──────────────────────────────────────────┤
│                                           │
│  Project name *                           │
│  ┌─────────────────────────────────────┐ │
│  │ cologic-shop-floor-tracker          │ │  ← Type your name
│  └─────────────────────────────────────┘ │
│                                           │
│  Project ID                               │
│  ┌─────────────────────────────────────┐ │
│  │ cologic-shop-floor-tracker-123456   │ │  ← Auto-generated
│  └─────────────────────────────────────┘ │
│  [Edit]                                   │
│                                           │
│  Organization                             │
│  ┌─────────────────────────────────────┐ │
│  │ No organization            ▼        │ │
│  └─────────────────────────────────────┘ │
│                                           │
│  Location                                 │
│  ┌─────────────────────────────────────┐ │
│  │ No organization                     │ │
│  └─────────────────────────────────────┘ │
│                                           │
│        [CANCEL]           [CREATE]        │  ← Click CREATE
│                                           │
└──────────────────────────────────────────┘
```

**Step 2.4**: Wait for project creation (5-10 seconds)
```
Creating project...  ⏳
```

**✅ Success**: You'll see a notification:
```
┌──────────────────────────────────────────┐
│  ✓ Project created successfully           │
└──────────────────────────────────────────┘
```

---

## 3. Enable Billing

### 💳 **Link a Billing Account**

**Step 3.1**: Click **☰ (hamburger menu)** → **Billing**
```
┌────────────────────────────┐
│  ☰ Navigation menu          │
├────────────────────────────┤
│  🏠 Home                    │
│  📊 Marketplace             │
│  💳 Billing            ← Click│
│  📁 IAM & Admin             │
│  ⚙️  APIs & Services        │
│  ...                        │
└────────────────────────────┘
```

**Step 3.2**: You'll see the billing page:
```
┌──────────────────────────────────────────┐
│  Billing                              ✕  │
├──────────────────────────────────────────┤
│                                           │
│  This project has no billing account      │
│                                           │
│  [LINK A BILLING ACCOUNT]            ← Click│
│                                           │
└──────────────────────────────────────────┘
```

**Step 3.3**: Select or create billing account:
```
┌──────────────────────────────────────────┐
│  Link a billing account               ✕  │
├──────────────────────────────────────────┤
│                                           │
│  ◉ My Billing Account                    │  ← Select existing
│  ○ Create a billing account              │  ← Or create new
│                                           │
│        [CANCEL]      [SET ACCOUNT]        │  ← Click this
│                                           │
└──────────────────────────────────────────┘
```

**If creating new billing account**:
- Enter credit/debit card details
- Verify your identity
- Accept billing terms

**✅ Success**: Billing linked!

---

## 4. Create a Virtual Machine

### 🖥️ **Create Compute Engine VM**

**Step 4.1**: Click **☰** → **Compute Engine** → **VM instances**
```
┌────────────────────────────┐
│  ☰ Navigation menu          │
├────────────────────────────┤
│  🖥️  Compute Engine     ▼   │  ← Expand this
│     │─ VM instances      ← Click│
│     │─ Instance groups       │
│     └─ Instance templates    │
└────────────────────────────┘
```

**Step 4.2**: Enable Compute Engine API (if first time):
```
┌──────────────────────────────────────────┐
│  Enable Compute Engine API                │
├──────────────────────────────────────────┤
│                                           │
│  Compute Engine API is required          │
│  to create virtual machines               │
│                                           │
│              [ENABLE]                ← Click│
│                                           │
└──────────────────────────────────────────┘
```
⏳ Wait 30 seconds for API to enable

**Step 4.3**: Click **[CREATE INSTANCE]**
```
┌──────────────────────────────────────────┐
│  VM instances                             │
├──────────────────────────────────────────┤
│                                           │
│  You have no VM instances                 │
│                                           │
│  [➕ CREATE INSTANCE]                ← Click│
│                                           │
└──────────────────────────────────────────┘
```

**Step 4.4**: Configure your VM (scroll through the form):


```
┌────────────────────────────────────────────────────────┐
│  Create an instance                                     │
├────────────────────────────────────────────────────────┤
│                                                         │
│  Name *                                                 │
│  ┌───────────────────────────────────────────────────┐ │
│  │ cologic-cloud-server                              │ │  ← Type name
│  └───────────────────────────────────────────────────┘ │
│                                                         │
│  Region *                                               │
│  ┌───────────────────────────────────────────────────┐ │
│  │ us-central1 (Iowa)                           ▼   │ │  ← Choose closest
│  └───────────────────────────────────────────────────┘ │
│                                                         │
│  Zone *                                                 │
│  ┌───────────────────────────────────────────────────┐ │
│  │ us-central1-a                                ▼   │ │
│  └───────────────────────────────────────────────────┘ │
│                                                         │
│  Machine configuration                                  │
│  ┌───────────────────────────────────────────────────┐ │
│  │ Series:  E2                                   ▼  │ │  ← Select E2
│  │ Machine type:  e2-medium (2 vCPU, 4 GB memory) ▼│ │  ← Select this
│  └───────────────────────────────────────────────────┘ │
│                                                         │
│  Est. monthly cost: $24.27                              │
│                                                         │
└────────────────────────────────────────────────────────┘
```

**Scroll down** to continue configuration...

```
┌────────────────────────────────────────────────────────┐
│  Boot disk                                              │
│  ┌───────────────────────────────────────────────────┐ │
│  │ Operating system: Debian GNU/Linux                │ │
│  │ Version: Debian 11                                │ │
│  │ Boot disk type: Balanced persistent disk          │ │
│  │ Size (GB): 10                                     │ │
│  │                                                   │ │
│  │             [CHANGE]                          ← Click│
│  └───────────────────────────────────────────────────┘ │
└────────────────────────────────────────────────────────┘
```

**In Boot disk popup**:
```
┌────────────────────────────────────────────────────────┐
│  Boot disk                                           ✕  │
├────────────────────────────────────────────────────────┤
│  Operating system                                       │
│  ○ Debian    ● Ubuntu    ○ CentOS    ○ Windows        │
│                  ↑ Click Ubuntu                         │
│  Version                                                │
│  ┌───────────────────────────────────────────────────┐ │
│  │ Ubuntu 22.04 LTS                             ▼   │ │  ← Select this
│  └───────────────────────────────────────────────────┘ │
│                                                         │
│  Boot disk type                                         │
│  ○ Standard    ● Balanced    ○ SSD                     │
│                                                         │
│  Size (GB)                                              │
│  ┌──────────┐                                          │
│  │   50     │                                      ← Type 50│
│  └──────────┘                                          │
│                                                         │
│              [CANCEL]         [SELECT]             ← Click│
└────────────────────────────────────────────────────────┘
```


**Scroll down** to Firewall section:
```
┌────────────────────────────────────────────────────────┐
│  Firewall                                               │
│  ☑ Allow HTTP traffic                              ← Check│
│  ☑ Allow HTTPS traffic                             ← Check│
└────────────────────────────────────────────────────────┘
```

**Step 4.5**: Click **[CREATE]** at the bottom
```
┌────────────────────────────────────────────────────────┐
│                                                         │
│        [CANCEL]                    [CREATE]        ← Click│
│                                                         │
└────────────────────────────────────────────────────────┘
```

**⏳ Wait**: VM creation takes 30-60 seconds

**✅ Success**: You'll see your VM in the list:
```
┌────────────────────────────────────────────────────────┐
│  VM instances                                           │
├────────────────────────────────────────────────────────┤
│  Name                  Zone          Status    IP       │
│  ────────────────────────────────────────────────────  │
│  ✓ cologic-cloud-      us-central1-a Running  34.x.x.x │
│     server                                   ↑ Your IP  │
└────────────────────────────────────────────────────────┘
```

💡 **Write down the External IP address!**

---

## 5. Set Up Firewall Rules

### 🔥 **Configure Firewall**

**Step 5.1**: Click **☰** → **VPC network** → **Firewall**
```
┌────────────────────────────┐
│  ☰ Navigation menu          │
├────────────────────────────┤
│  🌐 VPC network         ▼  │  ← Expand
│     │─ VPC networks          │
│     │─ Firewall          ← Click│
│     │─ Routes                │
│     └─ IP addresses          │
└────────────────────────────┘
```

**Step 5.2**: Click **[+ CREATE FIREWALL RULE]**
```
┌──────────────────────────────────────────┐
│  Firewall                                 │
├──────────────────────────────────────────┤
│  [🔍 Filter]  [+ CREATE FIREWALL RULE]←Click│
├──────────────────────────────────────────┤
│  Name                Direction   ...      │
│  default-allow-http   Ingress    ...      │
│  default-allow-https  Ingress    ...      │
└──────────────────────────────────────────┘
```

**Step 5.3**: Create rule for port 8000 (temporary testing):
```
┌────────────────────────────────────────────────────────┐
│  Create a firewall rule                                 │
├────────────────────────────────────────────────────────┤
│  Name *                                                 │
│  ┌───────────────────────────────────────────────────┐ │
│  │ allow-app-port-8000                               │ │  ← Type name
│  └───────────────────────────────────────────────────┘ │
│                                                         │
│  Direction of traffic                                   │
│  ● Ingress    ○ Egress                             ← Select│
│                                                         │
│  Targets                                                │
│  ┌───────────────────────────────────────────────────┐ │
│  │ Specified target tags                        ▼   │ │  ← Select
│  └───────────────────────────────────────────────────┘ │
│                                                         │
│  Target tags                                            │
│  ┌───────────────────────────────────────────────────┐ │
│  │ http-server                                       │ │  ← Type this
│  └───────────────────────────────────────────────────┘ │
│                                                         │
│  Source IPv4 ranges                                     │
│  ┌───────────────────────────────────────────────────┐ │
│  │ 0.0.0.0/0                                         │ │  ← Type this
│  └───────────────────────────────────────────────────┘ │
│                                                         │
│  Protocols and ports                                    │
│  ○ Allow all                                           │
│  ● Specified protocols and ports                   ← Select│
│     ☑ TCP                                          ← Check│
│  ┌───────────────────────────────────────────────────┐ │
│  │ 8000                                              │ │  ← Type 8000
│  └───────────────────────────────────────────────────┘ │
│                                                         │
│              [CANCEL]              [CREATE]        ← Click│
└────────────────────────────────────────────────────────┘
```

**✅ Success**: Firewall rule created!

---

## 6. Reserve a Static IP

### 📌 **Get a Permanent IP Address**

**Step 6.1**: Click **☰** → **VPC network** → **IP addresses**
```
┌────────────────────────────┐
│  🌐 VPC network         ▼  │
│     │─ VPC networks          │
│     │─ Firewall              │
│     │─ Routes                │
│     └─ IP addresses      ← Click│
└────────────────────────────┘
```

**Step 6.2**: Click **[RESERVE EXTERNAL STATIC ADDRESS]**
```
┌──────────────────────────────────────────────────┐
│  IP addresses                                     │
├──────────────────────────────────────────────────┤
│  [RESERVE EXTERNAL STATIC ADDRESS]           ← Click│
├──────────────────────────────────────────────────┤
│  Type        Address        In use by           │
│  Ephemeral   34.x.x.x       cologic-cloud-server│
└──────────────────────────────────────────────────┘
```

**Step 6.3**: Configure static IP:
```
┌────────────────────────────────────────────────────────┐
│  Reserve a static address                               │
├────────────────────────────────────────────────────────┤
│  Name *                                                 │
│  ┌───────────────────────────────────────────────────┐ │
│  │ cologic-cloud-server-ip                           │ │  ← Type name
│  └───────────────────────────────────────────────────┘ │
│                                                         │
│  Network Service Tier                                   │
│  ● Premium    ○ Standard                           ← Select│
│                                                         │
│  IP version                                             │
│  ● IPv4    ○ IPv6                                  ← Select│
│                                                         │
│  Type                                                   │
│  ● Regional    ○ Global                            ← Select│
│                                                         │
│  Region                                                 │
│  ┌───────────────────────────────────────────────────┐ │
│  │ us-central1                                  ▼   │ │  ← Same as VM
│  └───────────────────────────────────────────────────┘ │
│                                                         │
│  Attached to                                            │
│  ┌───────────────────────────────────────────────────┐ │
│  │ cologic-cloud-server                         ▼   │ │  ← Select VM
│  └───────────────────────────────────────────────────┘ │
│                                                         │
│  Est. monthly cost: $7.30                               │
│                                                         │
│              [CANCEL]              [RESERVE]       ← Click│
└────────────────────────────────────────────────────────┘
```

**✅ Success**: Static IP reserved!

💡 **Write down this static IP** — you'll use it everywhere!

---

## 7. Create Cloud Storage Bucket

### 🪣 **Set Up Image Storage**

**Step 7.1**: Click **☰** → **Cloud Storage** → **Buckets**
```
┌────────────────────────────┐
│  ☰ Navigation menu          │
├────────────────────────────┤
│  📦 Cloud Storage       ▼  │  ← Expand
│     │─ Buckets           ← Click│
│     │─ Browser              │
│     └─ Transfer              │
└────────────────────────────┘
```

**Step 7.2**: Click **[+ CREATE]**
```
┌──────────────────────────────────────────┐
│  Buckets                                  │
├──────────────────────────────────────────┤
│  [+ CREATE]                           ← Click│
├──────────────────────────────────────────┤
│  No buckets in this project               │
└──────────────────────────────────────────┘
```

**Step 7.3**: Configure bucket (step-by-step wizard):

**Page 1 - Name your bucket**:
```
┌────────────────────────────────────────────────────────┐
│  Create a bucket                                        │
├────────────────────────────────────────────────────────┤
│  Step 1: Name your bucket                               │
│                                                         │
│  Name *                                                 │
│  ┌───────────────────────────────────────────────────┐ │
│  │ cologic-alert-events-12345                        │ │  ← Unique name
│  └───────────────────────────────────────────────────┘ │
│  ⓘ Must be globally unique                              │
│                                                         │
│                        [CONTINUE]                   ← Click│
└────────────────────────────────────────────────────────┘
```

**Page 2 - Choose where to store**:
```
┌────────────────────────────────────────────────────────┐
│  Step 2: Choose where to store your data               │
│                                                         │
│  Location type                                          │
│  ○ Multi-region                                        │
│  ● Region                                          ← Select│
│  ○ Dual-region                                         │
│                                                         │
│  Location                                               │
│  ┌───────────────────────────────────────────────────┐ │
│  │ us-central1 (Iowa)                           ▼   │ │  ← Same as VM
│  └───────────────────────────────────────────────────┘ │
│                                                         │
│                        [CONTINUE]                   ← Click│
└────────────────────────────────────────────────────────┘
```

**Page 3 - Choose storage class**:
```
┌────────────────────────────────────────────────────────┐
│  Step 3: Choose a storage class for your data           │
│                                                         │
│  ● Standard                                        ← Select│
│  ○ Nearline                                            │
│  ○ Coldline                                            │
│  ○ Archive                                             │
│                                                         │
│                        [CONTINUE]                   ← Click│
└────────────────────────────────────────────────────────┘
```

**Page 4 - Access control**:
```
┌────────────────────────────────────────────────────────┐
│  Step 4: Choose how to control access to objects        │
│                                                         │
│  ● Uniform                                         ← Select│
│  ○ Fine-grained                                        │
│                                                         │
│                        [CONTINUE]                   ← Click│
└────────────────────────────────────────────────────────┘
```

**Page 5 - Protection**:
```
┌────────────────────────────────────────────────────────┐
│  Step 5: Choose how to protect object data              │
│                                                         │
│  ☐ Enable object versioning                            │  ← Leave unchecked
│  ☐ Set a retention policy                              │  ← Leave unchecked
│                                                         │
│                        [CREATE]                     ← Click│
└────────────────────────────────────────────────────────┘
```

**✅ Success**: Bucket created!

---

## 8. Create Service Account

### 🔑 **Set Up Cloud Access**

**Step 8.1**: Click **☰** → **IAM & Admin** → **Service Accounts**
```
┌────────────────────────────┐
│  ☰ Navigation menu          │
├────────────────────────────┤
│  👥 IAM & Admin         ▼  │  ← Expand
│     │─ IAM                  │
│     │─ Service Accounts  ← Click│
│     │─ Roles                │
│     └─ Quotas                │
└────────────────────────────┘
```

**Step 8.2**: Click **[+ CREATE SERVICE ACCOUNT]**
```
┌──────────────────────────────────────────┐
│  Service Accounts                         │
├──────────────────────────────────────────┤
│  [+ CREATE SERVICE ACCOUNT]           ← Click│
└──────────────────────────────────────────┘
```

**Step 8.3**: Fill in service account details:

**Page 1 - Service account details**:
```
┌────────────────────────────────────────────────────────┐
│  Create service account                                 │
├────────────────────────────────────────────────────────┤
│  Service account details                                │
│                                                         │
│  Service account name *                                 │
│  ┌───────────────────────────────────────────────────┐ │
│  │ cologic-cloud-server                              │ │  ← Type name
│  └───────────────────────────────────────────────────┘ │
│                                                         │
│  Service account ID (auto-generated)                    │
│  cologic-cloud-server@project.iam.gserviceaccount.com  │
│                                                         │
│  Service account description                            │
│  ┌───────────────────────────────────────────────────┐ │
│  │ Cloud server GCS access                           │ │  ← Optional
│  └───────────────────────────────────────────────────┘ │
│                                                         │
│            [CANCEL]   [CREATE AND CONTINUE]        ← Click│
└────────────────────────────────────────────────────────┘
```

**Page 2 - Grant permissions**:
```
┌────────────────────────────────────────────────────────┐
│  Grant this service account access to project           │
│                                                         │
│  Select a role                                          │
│  ┌───────────────────────────────────────────────────┐ │
│  │ Storage Object Admin                         ▼   │ │  ← Search & select
│  └───────────────────────────────────────────────────┘ │
│                                                         │
│  [+ ADD ANOTHER ROLE]                                  │
│                                                         │
│            [CANCEL]         [CONTINUE]             ← Click│
└────────────────────────────────────────────────────────┘
```

**Page 3 - Grant users access** (skip this):
```
┌────────────────────────────────────────────────────────┐
│  Grant users access to this service account (optional)  │
│                                                         │
│  (Leave blank)                                          │
│                                                         │
│            [CANCEL]             [DONE]             ← Click│
└────────────────────────────────────────────────────────┘
```

**Step 8.4**: Download the key:

Find your service account in the list:
```
┌──────────────────────────────────────────────────────┐
│  Email                                     Actions    │
│  cologic-cloud-server@...                  ⋮      ← Click│
└──────────────────────────────────────────────────────┘
```

Click **⋮** → **Manage keys**:
```
┌──────────────────────────┐
│  ⋮                       │
├──────────────────────────┤
│  Manage keys         ← Click│
│  Delete                  │
│  Permissions             │
└──────────────────────────┘
```

Click **[ADD KEY]** → **Create new key**:
```
┌──────────────────────────────────────────┐
│  ADD KEY                               ▼  │
├──────────────────────────────────────────┤
│  Create new key                       ← Click│
│  Upload existing key                     │
└──────────────────────────────────────────┘
```

Choose JSON format:
```
┌──────────────────────────────────────────┐
│  Create private key                    ✕  │
├──────────────────────────────────────────┤
│  Key type                                 │
│  ● JSON                               ← Select│
│  ○ P12                                   │
│                                           │
│  [CANCEL]             [CREATE]        ← Click│
└──────────────────────────────────────────┘
```

**✅ Success**: JSON key file downloads automatically!

💡 **Save this file securely** — you'll need it later!
- File name: `project-name-abc123.json`
- Keep it safe, never commit to git!

---

## 9. Connect to Your VM

### 💻 **SSH into Your Server**

**Step 9.1**: Go back to **☰** → **Compute Engine** → **VM instances**

**Step 9.2**: Find your VM and click **SSH**:
```
┌────────────────────────────────────────────────────────┐
│  VM instances                                           │
├────────────────────────────────────────────────────────┤
│  Name                  Zone          External IP  SSH   │
│  cologic-cloud-        us-central1-a 34.x.x.x   [SSH]  │
│  server                                            ↑    │
│                                                  Click  │
└────────────────────────────────────────────────────────┘
```

**Step 9.3**: A browser SSH window opens:
```
┌────────────────────────────────────────────────────────┐
│  SSH-in-browser: cologic-cloud-server               ✕  │
├────────────────────────────────────────────────────────┤
│                                                         │
│  Linux debian 5.10.0-20-cloud-amd64 #1 SMP ...         │
│                                                         │
│  The programs included with the Debian GNU/Linux ...   │
│                                                         │
│  username@cologic-cloud-server:~$  █                   │
│                                    ↑ Ready to type!    │
│                                                         │
└────────────────────────────────────────────────────────┘
```

**✅ Success**: You're now connected to your VM!

---

## 10. Set Up Application

### 📦 **Install Docker & Deploy**

Now you'll follow commands in the SSH terminal. I'll show you what to type:

**Step 10.1**: Update system
```bash
sudo apt-get update && sudo apt-get upgrade -y
```
⏳ Wait 1-2 minutes

**Step 10.2**: Install Docker
```bash
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
sudo usermod -aG docker $USER
```

**Step 10.3**: Install Docker Compose
```bash
sudo apt-get install docker-compose-plugin -y
```

**Step 10.4**: Log out and back in (close SSH window and reopen)
- Close the SSH browser window
- Click **[SSH]** button again in GCP Console
- New window opens

**Step 10.5**: Verify Docker
```bash
docker --version
docker compose version
```

**Step 10.6**: Clone your repository
```bash
git clone https://github.com/atulpandey5678/smart-floor-monitor-.git
cd smart-floor-monitor-
```

**Step 10.7**: Upload the service account key

**On your local computer**:
1. Go to GCP Console → **Compute Engine** → **VM instances**
2. Click the **⋮** (three dots) next to SSH
3. Select **Upload file**
```
┌──────────────────────────┐
│  ⋮                       │
├──────────────────────────┤
│  Upload file         ← Click│
│  Download file           │
└──────────────────────────┘
```

4. Choose the JSON key file you downloaded earlier
5. File uploads to `/home/youruser/`

**Step 10.8**: Move the key file
```bash
mv ~/project-name-*.json ~/smart-floor-monitor-/gcp-key.json
cd ~/smart-floor-monitor-
```

**Step 10.9**: Create environment file
```bash
cp .env.prod.example .env.prod
nano .env.prod
```

You'll see a text editor. **Fill in these values**:
```
SECRET_KEY=<generate with: openssl rand -hex 32>
INGEST_API_KEY=<generate with: openssl rand -hex 32>
FERNET_KEY=<generate with: python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())">
CLOUD_SERVER_BASE_URL=https://YOUR_STATIC_IP_OR_DOMAIN
GCS_BUCKET=your-bucket-name-from-step-7
```

**To generate secrets**, open another SSH window and run:
```bash
openssl rand -hex 32
# Copy output, paste into .env.prod as SECRET_KEY

openssl rand -hex 32
# Copy output, paste into .env.prod as INGEST_API_KEY

python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# Copy output, paste into .env.prod as FERNET_KEY
```

**Save and exit nano**:
- Press `Ctrl+X`
- Press `Y` to confirm
- Press `Enter` to save

**Step 10.10**: Update docker-compose to mount key
```bash
nano deploy/cloud/docker-compose.prod.yml
```

Find the `volumes:` section and add:
```yaml
volumes:
  - cloud-db:/app/data
  - ./gcp-key.json:/run/secrets/gcp-key.json:ro
```

Save and exit (`Ctrl+X`, `Y`, `Enter`)

**Step 10.11**: Start the application
```bash
docker compose -f deploy/cloud/docker-compose.prod.yml up -d
```
⏳ Wait 2-3 minutes for first build

**Step 10.12**: Check logs
```bash
docker compose -f deploy/cloud/docker-compose.prod.yml logs -f
```

Look for:
```
✓ Database migrations applied
✓ Server started on 0.0.0.0:8000
```

Press `Ctrl+C` to stop watching logs

**Step 10.13**: Test the application

Open in your browser:
```
http://YOUR_STATIC_IP:8000/health
```

You should see:
```json
{"status":"healthy","timestamp":"..."}
```

**✅ Success**: Application is running!

---

## 11. Configure Domain & SSL

### 🔒 **Set Up HTTPS (Optional but Recommended)**

**Step 11.1**: Configure DNS (do this on your domain registrar website)

Go to your domain registrar (GoDaddy, Namecheap, etc.) and add:
- **Type**: A Record
- **Name**: `tracker` (or `@` for root)
- **Value**: Your static IP from step 6
- **TTL**: 3600

Wait 5-10 minutes for DNS to propagate.

**Step 11.2**: Install Caddy (back in SSH terminal)
```bash
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update
sudo apt install caddy
```

**Step 11.3**: Configure Caddy
```bash
sudo nano /etc/caddy/Caddyfile
```

Replace contents with:
```
tracker.yourdomain.com {
    reverse_proxy localhost:8000
}
```

Replace `tracker.yourdomain.com` with your actual domain.

Save and exit (`Ctrl+X`, `Y`, `Enter`)

**Step 11.4**: Reload Caddy
```bash
sudo systemctl reload caddy
```

**Step 11.5**: Test HTTPS

Open in your browser:
```
https://tracker.yourdomain.com/health
```

**✅ Success**: HTTPS is working!

---

## 🎉 **You're Done!**

### Access Your Dashboard

Open in browser:
```
https://tracker.yourdomain.com
```

Or if no domain:
```
http://YOUR_STATIC_IP:8000
```

**Default login**:
- Username: `admin`
- Password: `admin`

**⚠️ IMPORTANT**: Change the password immediately after first login!

---

## 📝 **Quick Reference**

### What You Created

| Resource | Name | Purpose |
|----------|------|---------|
| Project | cologic-shop-floor-tracker | Container for all resources |
| VM | cologic-cloud-server | Cloud server application |
| IP | Static IP | Permanent address |
| Bucket | cologic-alert-events | Image storage |
| Service Account | cologic-cloud-server | GCS access |
| Firewall Rules | allow-http, allow-https, allow-app-port | Network access |

### Important URLs

- **GCP Console**: https://console.cloud.google.com
- **Your VM Dashboard**: https://YOUR_DOMAIN or http://YOUR_IP:8000
- **GitHub Repo**: https://github.com/atulpandey5678/smart-floor-monitor-

### Next Steps

1. ✅ Change default admin password
2. ✅ Install edge agents at factory sites
3. ✅ Set up monitoring and backups
4. ✅ Review the full deployment guide for production hardening

---

**Last Updated**: January 2025  
**Version**: 2.0 (UI Guide)
