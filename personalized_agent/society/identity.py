"""THE AGENT'S IDENTITY - its key pair (the user, 2026-11-01: "每个agent都会有一个
agent id, 是一个unique的，存在用户本机上 (放在data里，卸载也不会被delete, 和用户data
一起可以被复用, 可以被package (package的时候会询问是否打包key))").

* TWO KEYS, made on this computer the first time the agent starts without
  them: Ed25519, to sign what the agent says; X25519, to open what is sealed
  for it - its public half signed by the first, so no one can put another in.
* THE KEY'S OWN ID is the Ed25519 public key's hash, short:
  `pa-XXXX-XXXX-XXXX-XXXX` - the agent's id under the site's old v1 API.
  Since v2 (2026-11-10) the agent acts for the USER'S ACCOUNT, made on the
  website (`pa-XXXX-XXXX` since 2026-11-12): a key of it is GIVEN to this
  agent, which the site knows by its AGENT ID - its public key's
  fingerprint (`key_code`, the pairing's request code, `XXXX-XXXX-XXXX`).
  The private keys are never shown, never sent.
* KEPT IN THE DATA FOLDER (credentials/), not the system keychain: it stays
  when the program is uninstalled, and moves with the user's data. Encrypted
  there with a key file beside it that only this user may read.
* NO BACKUP OF IT (the user, 2026-11-13: "去掉，全部用新的key形式"): the site gives this
  agent a key of the user's account; lost - a new computer, the data gone - the agent
  makes a new identity and is paired again, the old key taken back on the website.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Any, Dict, Optional

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
FILE, KEY_FILE = "society_identity.json", "society_identity.secret"


def b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def unb64(text: Any) -> bytes:
    return base64.b64decode(str(text or ""), validate=True)


def short_id(public_key: bytes) -> str:
    """The public id of an Ed25519 public key - as the server makes it."""
    digest = hashlib.sha256(b"pa-agent-id\x00" + public_key).digest()[:10]
    number = int.from_bytes(digest, "big")
    chars = []
    for _ in range(16):
        chars.append(CROCKFORD[number & 31])
        number >>= 5
    text = "".join(reversed(chars))
    return "pa-" + "-".join(text[at:at + 4] for at in range(0, 16, 4))


def key_code(public_key: bytes) -> str:
    """A key's fingerprint as the server names it (v2's X-Key, and the
    pairing's request code): the low 60 bits of the first 8 bytes of
    sha256(b"pa-key\\0" + the raw 32-byte PUBLIC key), big-endian, in
    Crockford's base32, `XXXX-XXXX-XXXX`."""
    number = int.from_bytes(hashlib.sha256(b"pa-key\x00" + public_key).digest()[:8], "big")
    chars = "".join(CROCKFORD[(number >> (5 * n)) & 31] for n in range(12))[::-1]
    return "-".join(chars[at:at + 4] for at in range(0, 12, 4))


def normal_id(text: Any) -> Optional[str]:
    """An account id as the site writes it - in any case, with or without its dashes. SHORT now
    (the site's API, 2026-11-12): `pa-XXXX-XXXX`; one made before ids were short,
    `pa-XXXX-XXXX-XXXX-XXXX`, is still found by the site under that id, and kept as it is."""
    raw = "".join(str(text or "").split()).replace("-", "").upper()
    if raw.startswith("PA"):
        raw = raw[2:]
    raw = raw.replace("O", "0").replace("I", "1").replace("L", "1")
    if len(raw) not in (8, 16) or any(ch not in CROCKFORD for ch in raw):
        return None
    return "pa-" + "-".join(raw[at:at + 4] for at in range(0, len(raw), 4))


def _raw(key: Any) -> bytes:
    if isinstance(key, (Ed25519PrivateKey, X25519PrivateKey)):
        return key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                 serialization.NoEncryption())
    return key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


class Identity:
    """The agent's two keys - and what of them may be said."""

    def __init__(self, signing: Ed25519PrivateKey, sealing: X25519PrivateKey) -> None:
        self.signing, self.sealing = signing, sealing
        self.public_key = _raw(signing.public_key())
        self.enc_key = _raw(sealing.public_key())
        self.id = short_id(self.public_key)

    @classmethod
    def new(cls) -> "Identity":
        return cls(Ed25519PrivateKey.generate(), X25519PrivateKey.generate())

    def sign(self, data: bytes) -> bytes:
        return self.signing.sign(data)

    def enc_key_sig(self) -> bytes:
        return self.sign(b"pa-enc-key-v1\n" + self.enc_key)

    def _private(self) -> Dict[str, str]:
        return {"sign": b64(_raw(self.signing)), "seal": b64(_raw(self.sealing))}

    @classmethod
    def _from_private(cls, said: Dict[str, Any]) -> "Identity":
        return cls(Ed25519PrivateKey.from_private_bytes(unb64(said["sign"])),
                   X25519PrivateKey.from_private_bytes(unb64(said["seal"])))

    def public(self) -> Dict[str, str]:
        return {"id": self.id, "public_key": b64(self.public_key), "enc_key": b64(self.enc_key)}


# -- kept in the data folder ----------------------------------------------------------------
def _restrict(path: Path) -> None:
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:                                   # pragma: no cover - the system's own
        pass


def _fernet(folder: Path) -> Fernet:
    path = folder / KEY_FILE
    if not path.exists():
        path.write_bytes(Fernet.generate_key())
        _restrict(path)
    return Fernet(path.read_bytes().strip())


def save(folder: Path, identity: Identity) -> None:
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    sealed = _fernet(folder).encrypt(json.dumps(identity._private()).encode("utf-8"))
    path = folder / FILE
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"v": 1, "id": identity.id,
                                     "keys": sealed.decode("ascii")}), encoding="utf-8")
    _restrict(temporary)
    os.replace(temporary, path)


def load(folder: Path) -> Optional[Identity]:
    path = Path(folder) / FILE
    if not path.exists():
        return None
    said = json.loads(path.read_text(encoding="utf-8"))
    try:
        keys = json.loads(_fernet(Path(folder)).decrypt(said["keys"].encode("ascii")))
    except InvalidToken:
        raise ValueError("the agent's key could not be opened: its key file is not the one "
                         "it was kept with") from None
    identity = Identity._from_private(keys)
    if identity.id != said.get("id"):
        raise ValueError("the agent's key file does not match its id")
    return identity


def load_or_make(folder: Path) -> Identity:
    """The agent's identity - made, and kept, the first time it is asked for
    (the user: "第一次启动检查是否已经有密钥，没有就生成")."""
    found = load(folder)
    if found is not None:
        return found
    made = Identity.new()
    save(folder, made)
    return made


__all__ = ["Identity", "load", "load_or_make", "save", "short_id",
           "normal_id", "key_code", "b64", "unb64"]
