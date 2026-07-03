"""Settings Manager — live, DB-backed configuration store.

Replaces static config.py values for all tunable parameters.
The pipeline reads from SettingsManager on each frame so changes take
effect immediately without a server restart.

Usage:
    settings = SettingsManager(db)
    threshold = settings.get("detection", "person_confidence_threshold")
    settings.set("detection", "grace_period_seconds", 120)
    all_detection = settings.section("detection")
"""

import json
import logging
import threading
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# ── Default values (fallback when DB has no entry) ───────────────────
# These mirror the current config.py values so the system works on a
# fresh DB with zero settings rows.

DEFAULTS: Dict[str, Dict[str, Any]] = {
    "detection": {
        "person_confidence_threshold": 0.60,
        "grace_period_seconds": 180,
        "stable_frames_required": 4,
        "optical_flow_threshold": 2.0,
        "static_worker_timeout_seconds": 180,
        "kalman_predict_frames": 3,
    },
    "light": {
        "enabled": True,
        "alert_on_red": True,
        "saturation_min": 30,
        "brightness_min": 40,
        "vivid_saturation": 60,
        "analysis_size": 160,
        "min_color_pixels": 15,
        "stable_frames": 3,
        "hue_green_min": 25,
        "hue_green_max": 95,
        "hue_amber_min": 10,
        "hue_amber_max": 25,
        "hue_red_low_min": 0,
        "hue_red_low_max": 10,
        "hue_red_high_min": 160,
        "hue_red_high_max": 180,
    },
    "shifts": {
        "default_shift_hours": 8,
        "shifts": [],          # list of {name, start_hhmm, end_hhmm}
        "holidays": [],        # list of "YYYY-MM-DD" strings
    },
    "notifications": {
        "email_enabled": False,
        "smtp_host": "",
        "smtp_port": 587,
        "smtp_username": "",
        "smtp_password": "",
        "alert_recipients": [],   # list of email strings
        "notify_on": ["machine_red_light", "static_worker", "camera_offline"],
    },
    "branding": {
        "company_name": "Cologic",
        "logo_url": "https://i.ibb.co/q3NXdhnH/Cologic-LOGO-1.png",
        "primary_color": "#6366F1",
    },
    "retention": {
        "retention_days": 90,
        "archive_enabled": True,
        "archive_time": "02:00",
    },
}


class SettingsManager:
    """Thread-safe, DB-backed settings store with in-memory cache."""

    def __init__(self, db=None):
        self._db = db
        self._lock = threading.Lock()
        self._cache: Dict[str, Dict[str, Any]] = {}
        if db:
            self._load_all()

    def _load_all(self):
        """Load all settings from DB into the in-memory cache."""
        try:
            rows = self._db.fetch_all("SELECT section, key, value FROM app_settings")
            with self._lock:
                self._cache = {}
                for row in rows:
                    s, k, v = row["section"], row["key"], row["value"]
                    if s not in self._cache:
                        self._cache[s] = {}
                    try:
                        self._cache[s][k] = json.loads(v)
                    except (json.JSONDecodeError, ValueError):
                        self._cache[s][k] = v
        except Exception as e:
            logger.warning("Failed to load settings from DB (using defaults): %s", e)

    def get(self, section: str, key: str, default: Any = None) -> Any:
        """Get a single setting value.

        Returns: DB value → section default → provided default → None
        """
        with self._lock:
            if section in self._cache and key in self._cache[section]:
                return self._cache[section][key]
        # Fall back to DEFAULTS
        section_defaults = DEFAULTS.get(section, {})
        if key in section_defaults:
            return section_defaults[key]
        return default

    def section(self, section: str) -> Dict[str, Any]:
        """Return the full settings dict for a section, merged with defaults."""
        defaults = dict(DEFAULTS.get(section, {}))
        with self._lock:
            overrides = dict(self._cache.get(section, {}))
        defaults.update(overrides)
        return defaults

    def set(self, section: str, key: str, value: Any) -> None:
        """Persist a single setting value to DB and update the cache."""
        json_value = json.dumps(value)
        if self._db:
            try:
                self._db.execute(
                    """INSERT INTO app_settings (section, key, value, updated_at)
                       VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                       ON CONFLICT(section, key) DO UPDATE SET value=excluded.value,
                       updated_at=excluded.updated_at""",
                    (section, key, json_value),
                )
            except Exception as e:
                logger.error("Failed to save setting %s/%s: %s", section, key, e)
                raise
        with self._lock:
            if section not in self._cache:
                self._cache[section] = {}
            self._cache[section][key] = value

    def set_section(self, section: str, values: Dict[str, Any]) -> None:
        """Persist all keys of a section dict at once."""
        for key, value in values.items():
            self.set(section, key, value)

    def reload(self):
        """Reload all settings from DB (call after external DB changes)."""
        if self._db:
            self._load_all()


# Module-level singleton (set at startup via init_settings)
_settings: Optional[SettingsManager] = None


def init_settings(db) -> SettingsManager:
    """Initialize the global SettingsManager with the DB instance."""
    global _settings
    _settings = SettingsManager(db)
    return _settings


def get_settings() -> SettingsManager:
    """Return the global SettingsManager, or a default-only one if not initialized."""
    if _settings is None:
        return SettingsManager(None)
    return _settings
