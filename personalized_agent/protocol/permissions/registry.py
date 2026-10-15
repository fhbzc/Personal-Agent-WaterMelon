"""Application registration and permissions (sections 63, 65).

An application that speaks the Personalized Agent protocol registers an
identifier, a public key and the permissions the user grants it.  Permissions
belong to the authorization model of the side that grants them - here, the
agent granting local API access to an external integration.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

PERMISSIONS = ("read", "write", "execute", "ingest_events", "schedule_tasks")

KV_KEY = "protocol.registered_applications"


class ApplicationRegistry:
    def __init__(self, db: Any) -> None:
        self.db = db
        self._lock = threading.RLock()

    # -- persistence --------------------------------------------------------
    def _load(self) -> Dict[str, Dict[str, Any]]:
        row = self.db.query_one("SELECT value FROM kv WHERE key = ?", (KV_KEY,))
        if not row:
            return {}
        try:
            return json.loads(row["value"])
        except ValueError:
            return {}

    def _save(self, data: Dict[str, Dict[str, Any]]) -> None:
        self.db.execute(
            "INSERT INTO kv (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (KV_KEY, json.dumps(data)),
        )

    # -- api ----------------------------------------------------------------
    def register(
        self,
        application_id: str,
        public_key: str,
        permissions: List[str],
        display_name: str = "",
    ) -> Dict[str, Any]:
        unknown = [p for p in permissions if p not in PERMISSIONS]
        if unknown:
            raise ValueError(f"unknown permissions: {unknown}")
        with self._lock:
            data = self._load()
            data[application_id] = {
                "application_id": application_id,
                "display_name": display_name or application_id,
                "public_key": public_key,
                "permissions": sorted(set(permissions)),
                "registered_at": datetime.now(timezone.utc).isoformat(),
                "revoked": False,
            }
            self._save(data)
            return data[application_id]

    def get(self, application_id: str) -> Optional[Dict[str, Any]]:
        entry = self._load().get(application_id)
        if entry and entry.get("revoked"):
            return None
        return entry

    def list(self) -> List[Dict[str, Any]]:
        return sorted(self._load().values(), key=lambda e: e["application_id"])

    def revoke(self, application_id: str) -> bool:
        """Section 65 - key revocation."""
        with self._lock:
            data = self._load()
            if application_id not in data:
                return False
            data[application_id]["revoked"] = True
            data[application_id]["revoked_at"] = datetime.now(timezone.utc).isoformat()
            self._save(data)
            return True

    def set_permissions(self, application_id: str, permissions: List[str]) -> Dict[str, Any]:
        """Section 65 - permission revocation is a permission update."""
        unknown = [p for p in permissions if p not in PERMISSIONS]
        if unknown:
            raise ValueError(f"unknown permissions: {unknown}")
        with self._lock:
            data = self._load()
            if application_id not in data:
                raise KeyError(application_id)
            data[application_id]["permissions"] = sorted(set(permissions))
            self._save(data)
            return data[application_id]

    def has_permission(self, application_id: str, permission: str) -> bool:
        entry = self.get(application_id)
        return bool(entry and permission in entry.get("permissions", []))
