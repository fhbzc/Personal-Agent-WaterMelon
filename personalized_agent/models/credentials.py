"""Credential Manager (sections 62, 74, 83, 84).

Rules enforced here rather than merely documented:

* secrets never enter the Knowledge Graph, Raw Edits, prompts, or logs;
* the OS keychain is used when available (``keyring``);
* otherwise secrets are encrypted at rest with a key file whose permissions are
  restricted to the current user;
* every stored value is registered with the log redaction filter.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import stat
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from ..common.logging.setup import SECRETS
from ..common.atomic import replace_atomically
from ..common import children

log = logging.getLogger(__name__)

try:  # optional: OS backed secure storage
    import keyring  # type: ignore

    _KEYRING_AVAILABLE = True
except Exception:  # pragma: no cover - optional dependency
    keyring = None
    _KEYRING_AVAILABLE = False

try:
    from cryptography.fernet import Fernet, InvalidToken

    _CRYPTO_AVAILABLE = True
except Exception:  # pragma: no cover - optional dependency
    Fernet = None  # type: ignore
    InvalidToken = Exception  # type: ignore
    _CRYPTO_AVAILABLE = False

SERVICE_NAME = "personalized-agent"
DISABLE_KEYRING_ENV = "PERSONALIZED_AGENT_NO_KEYRING"
#: Left in the credentials folder when the system keychain refused to keep a
#: secret: from then on this installation keeps its secrets in the encrypted
#: file instead, without asking the keychain again.
KEYCHAIN_REFUSED = "keychain-refused"


def keychain_unreliable() -> str:
    """Why this copy of the program should not keep secrets in the system
    keychain, or "" when it may.

    A macOS application signed by nobody - ad hoc, which is all a build
    without a developer's certificate can be - is known to the keychain by
    the exact bytes of this version. The next version is another program
    to it: it asks the user again before handing over what this one kept,
    and a "Deny" leaves the agent without its keys, its identity made
    afresh. So such a build keeps them in its encrypted file instead.
    """
    if sys.platform != "darwin" or not getattr(sys, "frozen", False):
        return ""
    bundle = next((one for one in Path(sys.executable).resolve().parents
                   if one.suffix == ".app"), None)
    if bundle is None:
        return ""
    try:
        done = children.run_short(["codesign", "-dv", str(bundle)],
                                  capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=15)
    except Exception:  # pragma: no cover - codesign is part of macOS
        return ""
    said = f"{done.stderr or ''}{done.stdout or ''}"
    if "Signature=adhoc" in said or "not signed" in said:
        return ("this build is not signed by a developer, so macOS would ask "
                "again for the keychain after every update")
    return ""


def service_name_for(data_dir: Path) -> str:
    """Keychain service name, scoped to the installation.

    The OS keychain is a single global namespace, so two agent instances with
    different data directories - a second install, a test harness, a throwaway
    validation run - must not read or overwrite each other's credentials.  The
    default installation keeps the plain, recognizable service name.
    """
    from ..common.paths import INTERNAL_DIR, default_root

    resolved = Path(data_dir).resolve()
    try:
        # THE STANDARD data folder only - not whichever this run uses: a
        # folder chosen when installing is another agent, and must not read
        # or overwrite the keys of the one in the standard folder.
        default = (default_root() / INTERNAL_DIR).resolve() / "credentials"
    except Exception:  # pragma: no cover - home directory unavailable
        default = None
    if default is not None and resolved == default:
        return SERVICE_NAME
    digest = hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:12]
    return f"{SERVICE_NAME}:{digest}"


class CredentialManager:
    def __init__(self, data_dir: Path, use_keyring: Optional[bool] = None) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._store_path = self.data_dir / "credentials.enc"
        self._key_path = self.data_dir / "credential.key"
        self.service_name = service_name_for(self.data_dir)
        # The environment variable is an absolute opt-out: it must win over a
        # caller that asks for the keychain, because it is what keeps tests and
        # throwaway instances out of the user's real credential store.
        env_disabled = os.environ.get(DISABLE_KEYRING_ENV, "").strip().lower() in (
            "1", "true", "yes", "on"
        )
        if use_keyring is None:
            use_keyring = True
        self._refused_path = self.data_dir / KEYCHAIN_REFUSED
        #: Whether the keychain may be touched at all by this manager.
        self._keychain_allowed = _KEYRING_AVAILABLE and use_keyring and not env_disabled
        #: The keychain refused a secret once, or is not to be relied on:
        #: what it held before is read from it once and moved into the
        #: file, and nothing more is written there.
        self._keychain_refused = self._refused_path.exists()
        if self._keychain_allowed and not self._keychain_refused:
            why = keychain_unreliable()
            if why:
                log.info("secrets are kept in an encrypted file in %s: %s",
                         self.data_dir, why)
                self._keychain_refused = True
                self._remember_refusal(why)
        keyring_usable = self._keychain_allowed and not self._keychain_refused
        self._backend = "keyring" if keyring_usable else self._file_backend()
        self._fernet = self._load_fernet() if self._backend == "encrypted-file" else None
        self._cache: Dict[str, str] = {}
        self._load_all()

    # -- backend ------------------------------------------------------------
    @property
    def backend(self) -> str:
        return self._backend

    @staticmethod
    def _file_backend() -> str:
        return "encrypted-file" if _CRYPTO_AVAILABLE else "plaintext-file"

    def _keychain_said_no(self, error: Exception) -> None:
        """THE KEYCHAIN REFUSED A SECRET, so the encrypted file keeps them.

        macOS refuses an application whose signature does not match it
        (error -67030), and a Linux desktop may have no keyring service
        running. Either way the agent cannot go without its keys - it died
        on start when its identity key could not be stored - so they are
        kept the way they are kept where there is no keychain at all, and
        that is remembered."""
        log.warning("the system keychain would not keep a secret (%s); this "
                    "installation keeps its secrets in an encrypted file in %s "
                    "from now on", error, self.data_dir)
        with self._lock:
            self._backend = self._file_backend()
            self._keychain_refused = True
            if self._backend == "encrypted-file" and self._fernet is None:
                self._fernet = self._load_fernet()
            self._load_all()
        self._remember_refusal(f"{type(error).__name__}: {error}")

    def _remember_refusal(self, why: str) -> None:
        try:
            self._refused_path.write_text(f"{why}\n", encoding="utf-8")
        except OSError:  # pragma: no cover - the folder is the agent's own
            log.debug("could not remember the keychain's refusal", exc_info=True)

    def _restrict(self, path: Path) -> None:
        try:
            if os.name == "nt":
                user = os.environ.get("USERNAME", "")
                if user:
                    children.run_short(
                        ["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:F"],
                        capture_output=True, text=True,
                        encoding="utf-8", errors="replace", timeout=15,
                    )
            else:
                path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        except Exception:  # pragma: no cover - best effort hardening
            log.debug("could not restrict permissions on %s", path, exc_info=True)

    def _load_fernet(self):
        if self._key_path.exists():
            key = self._key_path.read_bytes()
        else:
            key = Fernet.generate_key()
            self._key_path.write_bytes(key)
            self._restrict(self._key_path)
        return Fernet(key)

    # -- persistence --------------------------------------------------------
    def _load_all(self) -> None:
        if self._backend == "keyring":
            return
        if not self._store_path.exists():
            return
        try:
            blob = self._store_path.read_bytes()
            if self._fernet is not None:
                blob = self._fernet.decrypt(blob)
            data = json.loads(blob.decode("utf-8"))
        except (OSError, ValueError, InvalidToken):
            log.warning("credential store unreadable; starting empty")
            return
        with self._lock:
            self._cache = {str(k): str(v) for k, v in data.items()}
        for value in self._cache.values():
            SECRETS.register(value)

    def _flush(self) -> None:
        if self._backend == "keyring":
            return
        with self._lock:
            payload = json.dumps(self._cache).encode("utf-8")
        if self._fernet is not None:
            payload = self._fernet.encrypt(payload)
        else:
            payload = base64.b64encode(payload)
        tmp = self._store_path.with_suffix(".tmp")
        tmp.write_bytes(payload)
        replace_atomically(tmp, self._store_path)
        self._restrict(self._store_path)

    # -- public API ---------------------------------------------------------
    def set(self, key: str, value: str) -> None:
        SECRETS.register(value)
        if self._backend == "keyring":
            try:
                keyring.set_password(self.service_name, key, value)
                return
            except Exception as exc:
                self._keychain_said_no(exc)
        with self._lock:
            self._cache[key] = value
        self._flush()

    def get(self, key: str) -> Optional[str]:
        if self._backend == "keyring":
            try:
                value = keyring.get_password(self.service_name, key)
            except Exception:
                return None
            SECRETS.register(value)
            return value
        with self._lock:
            value = self._cache.get(key)
        if value is None and self._keychain_refused and self._keychain_allowed:
            # Kept in the keychain before: read from there ONCE and moved
            # into the file, so macOS asks about it at most once.
            try:
                value = keyring.get_password(self.service_name, key)
            except Exception:
                value = None
            if value is not None:
                SECRETS.register(value)
                with self._lock:
                    self._cache[key] = value
                self._flush()
        return value

    def delete(self, key: str) -> None:
        value = self.get(key)
        SECRETS.forget(value)
        if self._backend == "keyring":
            try:
                keyring.delete_password(self.service_name, key)
            except Exception:
                pass
            return
        with self._lock:
            self._cache.pop(key, None)
        self._flush()
        if self._keychain_refused and self._keychain_allowed:
            try:
                keyring.delete_password(self.service_name, key)
            except Exception:
                pass

    def has(self, key: str) -> bool:
        return bool(self.get(key))

    def set_json(self, key: str, value: Dict[str, Any]) -> None:
        self.set(key, json.dumps(value))

    def get_json(self, key: str) -> Optional[Dict[str, Any]]:
        raw = self.get(key)
        if not raw:
            return None
        try:
            return json.loads(raw)
        except ValueError:
            return None

    def describe(self) -> Dict[str, Any]:
        """Never returns secret material - only whether one exists."""
        if self._backend == "keyring":
            keys: list[str] = []
        else:
            with self._lock:
                keys = sorted(self._cache.keys())
        return {"backend": self._backend, "service": self.service_name,
                "stored_keys": keys}
