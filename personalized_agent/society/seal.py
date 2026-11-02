"""A MESSAGE FOR ONE AGENT ALONE (the user, 2026-11-01: "发给某个public id的信息要用
公钥加密，这样只有有私钥的人才能解开").

SEALED, on the sender's computer: a fresh X25519 key for this message alone,
met with the recipient's sealing key, gives a key no one else can make
(HKDF); the words are locked with it (ChaCha20-Poly1305), bound to who sends
and to whom. SIGNED by the sender's own key - so the recipient knows who it
is from, and that not a letter of it changed. The server carries the
envelope and can read none of it.

OPENED on the recipient's computer: the signature checked with the sender's
public key - its id that key's hash - then the words unlocked with the
recipient's own sealing key.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict

from cryptography.exceptions import InvalidSignature, InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from .identity import Identity, _raw, b64, short_id, unb64

#: What one message may hold, once opened.
MOST_OPENED = 48 * 1024


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def _key(shared: bytes, eph: bytes, recipient: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
                info=b"pa-seal-v1" + eph + recipient).derive(shared)


def seal(me: Identity, to_id: str, to_enc_key: bytes, payload: Dict[str, Any]) -> Dict[str, Any]:
    eph = X25519PrivateKey.generate()
    eph_public = _raw(eph.public_key())
    key = _key(eph.exchange(X25519PublicKey.from_public_bytes(to_enc_key)), eph_public,
               to_enc_key)
    nonce = os.urandom(12)
    bound = canonical({"v": 1, "from": me.id, "to": to_id})
    envelope = {"v": 1, "from": me.id, "to": to_id, "eph": b64(eph_public), "nonce": b64(nonce),
                "box": b64(ChaCha20Poly1305(key).encrypt(nonce, canonical(payload), bound))}
    envelope["sig"] = b64(me.sign(b"pa-envelope-v1\n" + canonical(envelope)))
    return envelope


class Unreadable(ValueError):
    """A message that is not what it says it is - left unread."""


def open_sealed(me: Identity, envelope: Dict[str, Any], sender_public_key: bytes) -> Dict[str, Any]:
    if not isinstance(envelope, dict) or envelope.get("v") != 1:
        raise Unreadable("not a sealed message")
    if envelope.get("to") != me.id:
        raise Unreadable("not sealed for this agent")
    if short_id(sender_public_key) != envelope.get("from"):
        raise Unreadable("the sender's key is not its id's")
    signed = {key: value for key, value in envelope.items() if key != "sig"}
    try:
        Ed25519PublicKey.from_public_bytes(sender_public_key).verify(
            unb64(envelope.get("sig")), b"pa-envelope-v1\n" + canonical(signed))
    except (InvalidSignature, ValueError):
        raise Unreadable("its signature does not match its sender") from None
    try:
        eph = unb64(envelope["eph"])
        key = _key(me.sealing.exchange(X25519PublicKey.from_public_bytes(eph)), eph, me.enc_key)
        opened = ChaCha20Poly1305(key).decrypt(
            unb64(envelope["nonce"]), unb64(envelope["box"]),
            canonical({"v": 1, "from": envelope["from"], "to": me.id}))
    except (InvalidTag, ValueError, KeyError):
        raise Unreadable("it could not be opened") from None
    if len(opened) > MOST_OPENED:
        raise Unreadable("too large")
    said = json.loads(opened.decode("utf-8"))
    if not isinstance(said, dict):
        raise Unreadable("not a message")
    return said


__all__ = ["seal", "open_sealed", "Unreadable", "canonical"]
