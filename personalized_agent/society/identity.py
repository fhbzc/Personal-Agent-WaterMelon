"""THE AGENT'S IDENTITY - its key pair (the user, 2026-11-01: "每个agent都会有一个
agent id, 是一个unique的，存在用户本机上 (放在data里，卸载也不会被delete, 和用户data
一起可以被复用, 可以被package (package的时候会询问是否打包key))").

* TWO KEYS, made on this computer the first time the agent starts without
  them: Ed25519, to sign what the agent says; X25519, to open what is sealed
  for it - its public half signed by the first, so no one can put another in.
* ITS PUBLIC ID is the Ed25519 public key's hash, short, for people:
  `pa-XXXX-XXXX-XXXX-XXXX` (agent_society_server_end/society/ids.py makes it
  the same way). The private keys are never shown, never sent.
* KEPT IN THE DATA FOLDER (credentials/), not the system keychain: it stays
  when the program is uninstalled, and moves with the user's data. Encrypted
  there with a key file beside it that only this user may read.
* A BACKUP (`export`): the public id alone, or - when the user says so - the
  private keys too, sealed with a PASSPHRASE of theirs (scrypt, then
  AES-GCM): without it the file opens for no one. `restore` takes it back.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import stat
from pathlib import Path
from typing import Any, Dict, Optional

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
FILE, KEY_FILE = "society_identity.json", "society_identity.secret"
BACKUP_KIND = "personal-agent-identity"
#: A passphrase at least this long.
LEAST_PASSPHRASE = 8
SCRYPT = {"n": 2 ** 15, "r": 8, "p": 1}


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


def normal_id(text: Any) -> Optional[str]:
    raw = "".join(str(text or "").split()).replace("-", "").upper()
    if raw.startswith("PA"):
        raw = raw[2:]
    raw = raw.replace("O", "0").replace("I", "1").replace("L", "1")
    if len(raw) != 16 or any(ch not in CROCKFORD for ch in raw):
        return None
    return "pa-" + "-".join(raw[at:at + 4] for at in range(0, 16, 4))


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


# -- a backup, and taking it back -------------------------------------------------------------
def export(identity: Identity, passphrase: Optional[str] = None) -> Dict[str, Any]:
    """A backup of the identity: its public id alone - or, with a passphrase,
    its private keys too, sealed with it."""
    said: Dict[str, Any] = dict(identity.public(), kind=BACKUP_KIND, v=1)
    if passphrase is None:
        said["private"] = False
        return said
    if len(passphrase) < LEAST_PASSPHRASE:
        raise ValueError(f"a passphrase is at least {LEAST_PASSPHRASE} characters")
    salt, nonce = secrets.token_bytes(16), secrets.token_bytes(12)
    key = Scrypt(salt=salt, length=32, **SCRYPT).derive(passphrase.encode("utf-8"))
    box = AESGCM(key).encrypt(nonce, json.dumps(identity._private()).encode("utf-8"),
                              f"{BACKUP_KIND}-v1:{identity.id}".encode("utf-8"))
    said.update(private=True, sealed={"kdf": "scrypt", **SCRYPT, "salt": b64(salt),
                                      "nonce": b64(nonce), "box": b64(box)})
    return said


def restore(backup: Dict[str, Any], passphrase: str) -> Identity:
    """The identity a backup holds - only with its passphrase."""
    if not isinstance(backup, dict) or backup.get("kind") != BACKUP_KIND:
        raise ValueError("this is not a backup of an agent's key")
    if not backup.get("private"):
        raise ValueError("this backup holds the agent's id alone, not its key")
    sealed = backup.get("sealed") or {}
    try:
        key = Scrypt(salt=unb64(sealed["salt"]), length=32, n=int(sealed["n"]),
                     r=int(sealed["r"]), p=int(sealed["p"])).derive(passphrase.encode("utf-8"))
        keys = json.loads(AESGCM(key).decrypt(unb64(sealed["nonce"]), unb64(sealed["box"]),
                                              f"{BACKUP_KIND}-v1:{backup['id']}"
                                              .encode("utf-8")))
    except Exception:
        raise ValueError("the passphrase does not open this backup") from None
    identity = Identity._from_private(keys)
    if identity.id != backup.get("id"):
        raise ValueError("this backup's key is not its id's")
    return identity


__all__ = ["Identity", "load", "load_or_make", "save", "export", "restore", "short_id",
           "normal_id", "b64", "unb64"]
