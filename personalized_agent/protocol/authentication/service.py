"""Challenge-response authentication (sections 64, 65).

Formal definition of the handshake used by external integrations connecting to
the local agent API:

1. the application calls ``begin(application_id)``;
2. the agent returns a single use 32 byte challenge with a 60 second lifetime;
3. the application signs the challenge bytes with its registered Ed25519 key;
4. ``complete(...)`` verifies the signature against the registered public key,
   consumes the nonce (replay prevention), and establishes a session token with
   a bounded lifetime.

The private key never travels: possession is proven, not transmitted.
"""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from ..identity.keys import b64, unb64, verify_signature

CHALLENGE_TTL_SECONDS = 60
SESSION_TTL_SECONDS = 3600


@dataclass
class Session:
    token: str
    application_id: str
    permissions: List[str]
    expires_at: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "token": self.token,
            "application_id": self.application_id,
            "permissions": self.permissions,
            "expires_in": max(0, int(self.expires_at - time.time())),
        }


class AuthenticationError(Exception):
    pass


class AuthenticationService:
    def __init__(self, registry: Any, session_ttl: int = SESSION_TTL_SECONDS) -> None:
        self.registry = registry
        self.session_ttl = session_ttl
        self._lock = threading.RLock()
        self._challenges: Dict[str, Dict[str, Any]] = {}
        self._sessions: Dict[str, Session] = {}

    # -- handshake ----------------------------------------------------------
    def begin(self, application_id: str) -> Dict[str, Any]:
        entry = self.registry.get(application_id)
        if entry is None:
            raise AuthenticationError("application is not registered or has been revoked")
        nonce = secrets.token_bytes(32)
        challenge = b64(nonce)
        with self._lock:
            self._expire()
            self._challenges[challenge] = {
                "application_id": application_id,
                "expires_at": time.time() + CHALLENGE_TTL_SECONDS,
            }
        return {
            "challenge": challenge,
            "algorithm": "Ed25519",
            "expires_in": CHALLENGE_TTL_SECONDS,
        }

    def complete(self, application_id: str, challenge: str, signature: str) -> Dict[str, Any]:
        with self._lock:
            self._expire()
            pending = self._challenges.pop(challenge, None)  # single use
        if pending is None:
            raise AuthenticationError("unknown, expired or already used challenge")
        if pending["application_id"] != application_id:
            raise AuthenticationError("challenge was issued to a different application")
        entry = self.registry.get(application_id)
        if entry is None:
            raise AuthenticationError("application is not registered or has been revoked")
        if not verify_signature(entry["public_key"], unb64(challenge), signature):
            raise AuthenticationError("signature verification failed")
        session = Session(
            token=secrets.token_urlsafe(32),
            application_id=application_id,
            permissions=list(entry.get("permissions", [])),
            expires_at=time.time() + self.session_ttl,
        )
        with self._lock:
            self._sessions[session.token] = session
        return session.to_dict()

    # -- sessions -----------------------------------------------------------
    def authenticate(self, token: Optional[str], permission: Optional[str] = None) -> Session:
        if not token:
            raise AuthenticationError("missing session token")
        with self._lock:
            self._expire()
            session = self._sessions.get(token)
        if session is None:
            raise AuthenticationError("invalid or expired session")
        # A revocation takes effect immediately, even mid session.
        entry = self.registry.get(session.application_id)
        if entry is None:
            self.revoke_session(token)
            raise AuthenticationError("application registration was revoked")
        session.permissions = list(entry.get("permissions", []))
        if permission and permission not in session.permissions:
            raise AuthenticationError(f"permission '{permission}' has not been granted")
        return session

    def revoke_session(self, token: str) -> None:
        with self._lock:
            self._sessions.pop(token, None)

    def revoke_application_sessions(self, application_id: str) -> int:
        with self._lock:
            tokens = [t for t, s in self._sessions.items() if s.application_id == application_id]
            for token in tokens:
                self._sessions.pop(token, None)
        return len(tokens)

    def _expire(self) -> None:
        now = time.time()
        for challenge, data in list(self._challenges.items()):
            if data["expires_at"] < now:
                self._challenges.pop(challenge, None)
        for token, session in list(self._sessions.items()):
            if session.expires_at < now:
                self._sessions.pop(token, None)

    def describe(self) -> Dict[str, Any]:
        with self._lock:
            self._expire()
            return {
                "pending_challenges": len(self._challenges),
                "active_sessions": len(self._sessions),
                "challenge_ttl_seconds": CHALLENGE_TTL_SECONDS,
                "session_ttl_seconds": self.session_ttl,
                "algorithm": "Ed25519",
                "replay_prevention": "single use nonce with expiry",
            }
