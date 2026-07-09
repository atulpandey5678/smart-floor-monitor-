# Edge_Agent Service Deployment

Service-manager units that run the Cologic Shop Floor Tracker **Edge_Agent**
(`python -m edge.agent`) as a resilient background service — starting on boot
and auto-restarting on unexpected termination — on both Linux and Windows.

Covers Requirements **12.1** (start on boot), **12.2** (auto-restart on
unexpected termination), and **14.1** (installable on Windows and Linux).

| File | Platform | Purpose |
|------|----------|---------|
| `cologic-edge-agent.service` | Linux | systemd unit |
| `install-windows-service.ps1` | Windows | NSSM-based service installer |
| `README.md` | — | this document |

## What the Edge_Agent needs at runtime

The agent runs the CV compute stack locally and pushes results to the
Cloud_Server. It reads **all** secrets and connection settings from a
git-excluded local configuration, never from the service definition:

- **`.env`** (project root) — `INGEST_API_KEY`, `CLOUD_SERVER_BASE_URL` (must be
  `https://`), `CAMERA_CONFIG_PATH`, `FERNET_KEY`, `SYNC_*`, and
  `METADATA_POLL_INTERVAL_SECONDS`. Copy `.env.example` to `.env` and fill it in.
- **`camera_config.json`** (Local_Camera_Config, path from `CAMERA_CONFIG_PATH`)
  — the machine-ID → RTSP URL + camera credentials mapping. Copy
  `camera_config.example.json` and fill it in.

Both files stay on the on-site machine and are excluded from version control.
RTSP URLs and camera credentials never leave the local network (Req 13). The
service units below only set the **working directory** to the project root so
the agent can load these files itself — no secret is baked into the service.

Before installing the service, verify the agent runs by hand from the project
root:

```
python -m edge.agent
```

---

## Linux (systemd)

The unit `cologic-edge-agent.service` uses `Restart=on-failure` with
`RestartSec=5`, so any non-zero exit, signal kill, or timeout restarts the
agent automatically (Req 12.2), while a clean operator stop does not loop. It is
enabled into `multi-user.target` so it starts on boot (Req 12.1).

### Install

1. Put the project at `/opt/cologic-edge` (or edit the paths in the unit file to
   match your location). Create a virtualenv and install dependencies:

   ```
   cd /opt/cologic-edge
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   ```

2. Create a dedicated unprivileged service account and lock down `.env`:

   ```
   sudo useradd --system --no-create-home --shell /usr/sbin/nologin cologic
   sudo chown -R cologic:cologic /opt/cologic-edge
   sudo chmod 600 /opt/cologic-edge/.env
   ```

3. Install and enable the unit:

   ```
   sudo cp deploy/edge/cologic-edge-agent.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now cologic-edge-agent.service
   ```

### Operate

```
systemctl status cologic-edge-agent          # current state
journalctl -u cologic-edge-agent -f          # live logs
sudo systemctl restart cologic-edge-agent    # manual restart
sudo systemctl stop cologic-edge-agent       # stop (no auto-restart on clean stop)
sudo systemctl disable cologic-edge-agent    # stop starting on boot
```

If you edit the unit file, re-run `sudo systemctl daemon-reload` before
restarting.

### Verify the restart policy

- Confirm boot-start: `systemctl is-enabled cologic-edge-agent` → `enabled`.
- Confirm restart-on-failure: `systemctl show cologic-edge-agent -p Restart`
  → `Restart=on-failure`. Kill the process (`sudo systemctl kill -s SIGKILL
  cologic-edge-agent`) and watch it come back within ~5 s in `journalctl`.

The unit also sets `StartLimitBurst=5` / `StartLimitIntervalSec=60`: if the
agent crash-loops more than 5 times in 60 s, systemd stops retrying and marks
the unit failed so a genuine fault is surfaced rather than hidden.

---

## Windows (NSSM)

Native Windows services cannot host a console program directly, so
`install-windows-service.ps1` wraps `python -m edge.agent` with
[NSSM](https://nssm.cc/) (the Non-Sucking Service Manager). NSSM supervises the
process and provides:

- **Auto-start on boot** — `Start = SERVICE_AUTO_START` (Req 12.1).
- **Auto-restart on unexpected termination** — `AppExit Default Restart` with a
  5 s `AppRestartDelay` and a 60 s `AppThrottle` crash-loop guard (Req 12.2).

### Install

1. Install NSSM: download from <https://nssm.cc/>, unzip, and either add
   `nssm.exe` to `PATH` or note its full path.

2. Put the project somewhere like `C:\cologic-edge`, create a virtualenv, and
   install dependencies:

   ```
   cd C:\cologic-edge
   python -m venv .venv
   .venv\Scripts\pip install -r requirements.txt
   ```

3. Create `.env` and `camera_config.json` in the project root (see above).

4. From an **elevated (Administrator)** PowerShell prompt, run the installer:

   ```
   cd C:\cologic-edge\deploy\edge
   .\install-windows-service.ps1
   ```

   Override paths as needed:

   ```
   .\install-windows-service.ps1 -ProjectRoot "C:\cologic-edge" -NssmExe "C:\tools\nssm\nssm.exe"
   ```

   The script is idempotent — it stops and removes any prior install of the
   same service name before reinstalling, then starts the service.

### Operate

```
nssm status  CologicEdgeAgent          # current state
nssm restart CologicEdgeAgent          # manual restart
nssm stop    CologicEdgeAgent          # stop
nssm remove  CologicEdgeAgent confirm  # uninstall
```

Logs are written to `logs\edge-agent.out.log` and `logs\edge-agent.err.log`
under the project root (rotated online at ~10 MB). You can also manage the
service from `services.msc` under **Cologic Shop Floor Tracker - Edge Agent**.

### Verify the restart policy

- Confirm boot-start: in `services.msc` the service **Startup type** is
  **Automatic**, or run `nssm get CologicEdgeAgent Start` → `SERVICE_AUTO_START`.
- Confirm restart-on-failure: `nssm get CologicEdgeAgent AppExit Default`
  → `Restart`. Kill the `python.exe` in Task Manager and confirm NSSM restarts
  it within ~5 s.

### Native-Windows alternative (no NSSM)

If NSSM is not permitted in your environment, you can register the agent with
the built-in Service Control Manager and enable restart-on-failure with:

```
sc.exe create CologicEdgeAgent binPath= "\"C:\cologic-edge\.venv\Scripts\python.exe\" -m edge.agent" start= auto
sc.exe failure CologicEdgeAgent reset= 60 actions= restart/5000/restart/5000/restart/5000
```

This still requires a small `pywin32` service wrapper or a scheduled-task shim
because `python.exe` is not a native service binary; NSSM is the recommended
path because it handles the wrapping and log rotation for you.

---

## Where secrets come from (summary)

| Setting | Source | Notes |
|---------|--------|-------|
| `INGEST_API_KEY` | `.env` | Authenticates to Cloud_Server `/api/ingest/*` |
| `CLOUD_SERVER_BASE_URL` | `.env` | Must be `https://` |
| `CAMERA_CONFIG_PATH` | `.env` | Path to `camera_config.json` |
| RTSP URLs + camera credentials | `camera_config.json` | Never sent to cloud (Req 13) |
| `FERNET_KEY` | `.env` | Decrypts any encrypted RTSP URLs on the edge |

Keep `.env` and `camera_config.json` out of version control and readable only by
the service account. The service definitions carry **no** secrets — they only
set the working directory so the agent loads these files itself.
