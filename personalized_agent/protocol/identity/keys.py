"""Agent identity (sections 61, 62, 65).

Formally defined so nothing is improvised:

* signing algorithm: Ed25519 (RFC 8032) via ``cryptography``;
* public key format: raw 32 byte key, base64url encoded, plus PEM on request;
* private key storage: PKCS#8 PEM held by the Credential Manager, which uses
  the OS keychain when available;
* the private key is never displayed, logged, put in prompts, Raw Edits, the
  Knowledge Graph, or returned to applications.
"""

from __future__ import annotations

import base64
import hashlib
import logging
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)

PRIVATE_KEY_CREDENTIAL = "protocol.agent_private_key"
SIGNING_ALGORITHM = "Ed25519"

try:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
        Ed25519PublicKey,
    )

    CRYPTO_AVAILABLE = True
except Exception:  # pragma: no cover - optional dependency
    CRYPTO_AVAILABLE = False


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def unb64(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


class CryptographyUnavailable(RuntimeError):
    pass


class AgentIdentity:
    """The agent side of the application protocol."""

    def __init__(self, credentials: Any, kv: Any = None) -> None:
        if not CRYPTO_AVAILABLE:
            raise CryptographyUnavailable(
                "the cryptography package is required for the agent protocol"
            )
        self.credentials = credentials
        self.kv = kv
        self._private: Optional[Ed25519PrivateKey] = None
        self._load_or_create()

    # -- key material -------------------------------------------------------
    def _load_or_create(self) -> None:
        stored = self.credentials.get(PRIVATE_KEY_CREDENTIAL)
        if stored:
            try:
                self._private = serialization.load_pem_private_key(
                    stored.encode("utf-8"), password=None
                )
                return
            except Exception:
                log.warning("stored agent key is unreadable; generating a new identity")
        self.rotate()

    def rotate(self) -> Dict[str, Any]:
        """Section 65 - key rotation."""
        private = Ed25519PrivateKey.generate()
        pem = private.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("utf-8")
        self.credentials.set(PRIVATE_KEY_CREDENTIAL, pem)
        self._private = private
        return self.public_identity()

    @property
    def public_key_bytes(self) -> bytes:
        return self._private.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )

    @property
    def public_key(self) -> str:
        return b64(self.public_key_bytes)

    @property
    def public_key_pem(self) -> str:
        return self._private.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode("utf-8")

    @property
    def agent_id(self) -> str:
        digest = hashlib.sha256(self.public_key_bytes).hexdigest()[:32]
        return f"agent_{digest}"

    # -- proof of possession (section 64) ----------------------------------
    def sign(self, challenge: bytes) -> str:
        return b64(self._private.sign(challenge))

    def sign_challenge(self, challenge_b64: str) -> Dict[str, str]:
        signature = self.sign(unb64(challenge_b64))
        return {"agent_id": self.agent_id, "signature": signature,
                "algorithm": SIGNING_ALGORITHM}

    def public_identity(self) -> Dict[str, Any]:
        """Everything that may be shown to the user or an application."""
        return {
            "agent_id": self.agent_id,
            "public_key": self.public_key,
            "public_key_pem": self.public_key_pem,
            "algorithm": SIGNING_ALGORITHM,
            "private_key": "never disclosed",
        }


def verify_signature(public_key_b64: str, message: bytes, signature_b64: str) -> bool:
    if not CRYPTO_AVAILABLE:
        raise CryptographyUnavailable("the cryptography package is required")
    try:
        public_key = Ed25519PublicKey.from_public_bytes(unb64(public_key_b64))
        public_key.verify(unb64(signature_b64), message)
        return True
    except Exception:
        return False
