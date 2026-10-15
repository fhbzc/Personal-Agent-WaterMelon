"""Passwords and codes never kept as they read (Settings > Privacy).

What the listener records - the text on the screen, a window's title, a
page's address, what was said with an assistant - and the words read off a
picture can hold a password, a one-time code, a key. They are cut out here,
BEFORE anything is kept, by keywords and by the shapes such things have: no
model decides, so no password is ever put before a model to ask whether it
is one. What is cut out is replaced by "[hidden]"; the label stays
("Password: [hidden]"), so it can still be told that a sign-in happened.

THE ACCOUNT IS KEPT - a user name, an e-mail address, "user" in
user:password@: what was done is no use to read back without whose account
it was done in.
"""

from __future__ import annotations

import re
from typing import Any

HIDDEN = "[hidden]"

#: What a secret is called - in every language the screens can be shown in
#: (common/languages.py): English, Chinese (simplified and traditional),
#: Spanish, Japanese, Korean, Russian, French, German and Italian. Not what
#: an account is called: that is kept.
_SECRET_WORDS = (
    r"passwords?|passwd|pwd|pass\s?phrase|pass\s?code|pin(?:\s?code)?|"
    r"one[-\s]?time\s(?:code|password)|otp|verification\scode|security\scode|"
    r"auth(?:entication)?\scode|2fa\scode|sign[-\s]?in\scode|login\scode|"
    r"recovery\scodes?|backup\scodes?|api[\s_-]?keys?|secret(?:[\s_-]?key)?|"
    r"access[\s_-]?keys?|private[\s_-]?keys?|access[\s_-]?tokens?|"
    r"refresh[\s_-]?tokens?|auth[\s_-]?tokens?|tokens?|client[\s_-]?secret|"
    r"密码|密碼|口令|密钥|密鑰|秘钥|验证码|驗證碼|校验码|動態碼|动态码|安全码|安全碼|"
    r"授权码|授權碼|"
    r"contraseñas?|clave(?:\sde\sacceso)?|"
    r"código(?:\sde\s(?:verificación|seguridad|acceso))?|"
    r"パスワード|暗証番号|認証コード|確認コード|セキュリティコード|"
    r"ワンタイムパスワード|秘密鍵|APIキー|"
    r"비밀번호|암호|인증\s?번호|인증\s?코드|확인\s?코드|보안\s?코드|"
    r"일회용\s?비밀번호|비밀\s?키|"
    r"парол[ья]|пин-?код|код\s(?:подтверждения|доступа|безопасности)|одноразов\w+\sкод|"
    r"секретн\w+\sключ|"
    r"mots?\sde\spasse|code\s(?:de\s(?:vérification|sécurité|confirmation|accès)|pin)|"
    r"clé\s(?:secrète|privée|d'api)|"
    r"passw(?:ö|oe)rt(?:er)?|kennwort|geheimzahl|bestätigungscode|sicherheitscode|"
    r"zugangscode|einmalpasswort|geheimer\sschlüssel|privater\sschlüssel|"
    r"codice(?:\sdi\s(?:verifica|sicurezza|accesso))?|parola\sd'ordine|"
    r"chiave\s(?:segreta|privata|api)"
)
_LABEL = rf"(?:{_SECRET_WORDS})"

#: "Password: x", "password = x", "your code is 123456", "验证码为 123456",
#: "tu código es 123456", "認証コードは123456です", "인증번호는 123456입니다" -
#: the value alone, not the ending that follows it.
_STOP = r"\s\"'”’,;，；。、)）"
_LABELLED = re.compile(
    rf"(?i)(?<![A-Za-z0-9_])({_LABEL})"
    r"(\s*(?:[:：=]|\bis\b|\bwas\b|\bes\b|\best\b|\bist\b|\bè\b|—|为|是|為|は|는|은)\s*)"
    rf"([\"'“‘]?)([^{_STOP}]{{1,200}}?)(?=$|[{_STOP}]|です|でした|입니다|이에요|예요)")

#: A label alone on its line - a sign-in form read off the screen - and its
#: value on the next.
_LABEL_LINE = re.compile(rf"(?i)^\s*{_LABEL}\s*[:：*]?\s*$")
_ONE_WORD = re.compile(r"^\s*\S{1,200}\s*$")

#: user:password@ in an address - the password, not the user.
_URL_USER = re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*://)([^/\s:@]+):([^/\s@]+)@")
#: A secret in an address's query: ?token=..., &password=...
_URL_PARAM = re.compile(
    r"(?i)([?&#;][^=&#;\s]*?(?:password|passwd|pwd|token|secret|api[_-]?key|access[_-]?key|"
    r"auth|otp|session|sig|signature|code)[^=&#;\s]*=)([^&#;\s]+)")

#: What keys and tokens look like, whatever they are called.
_SHAPES = [re.compile(one) for one in (
    r"sk-ant-[A-Za-z0-9_\-]{8,}",
    r"\bsk-[A-Za-z0-9_\-]{20,}",
    r"\bgh[pousr]_[A-Za-z0-9]{20,}",
    r"\bgithub_pat_[A-Za-z0-9_]{20,}",
    r"\bAKIA[0-9A-Z]{16}\b",
    r"\bxox[abprs]-[A-Za-z0-9\-]{10,}",
    r"\bAIza[0-9A-Za-z_\-]{35}\b",
    r"\beyJ[A-Za-z0-9_\-]{8,}\.eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}",
    r"(?i)(?<=\bbearer\s)[A-Za-z0-9._~+/\-]{16,}=*",
)]
_PRIVATE_KEY = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)")


def _text(text: str) -> str:
    if not text:
        return text
    out = _PRIVATE_KEY.sub(HIDDEN, text)
    for shape in _SHAPES:
        out = shape.sub(HIDDEN, out)
    out = _URL_USER.sub(lambda m: f"{m.group(1)}{m.group(2)}:{HIDDEN}@", out)
    # Not what is cut out already - here, or a sign-in ticket ("[removed]").
    out = _URL_PARAM.sub(lambda m: m.group(0) if m.group(2).startswith(("[", "%5B"))
                         else f"{m.group(1)}{HIDDEN}", out)
    out = _LABELLED.sub(
        lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}{HIDDEN}"
        if not m.group(4).startswith(HIDDEN) else m.group(0), out)
    if "\n" in out:
        lines = out.split("\n")
        for at in range(len(lines) - 1):
            if _LABEL_LINE.match(lines[at]) and _ONE_WORD.match(lines[at + 1]) \
                    and not _LABEL_LINE.match(lines[at + 1]) \
                    and lines[at + 1].strip() != HIDDEN:
                lines[at + 1] = HIDDEN
        out = "\n".join(lines)
    return out


def hide_credentials(value: Any) -> Any:
    """``value`` with every password and code in it hidden - walking
    dictionaries and lists, their values only. What holds none comes back as
    the same object."""
    if isinstance(value, str):
        hidden = _text(value)
        return value if hidden == value else hidden
    if isinstance(value, dict):
        changed = {key: hide_credentials(item) for key, item in value.items()}
        return value if all(changed[k] is value[k] for k in value) else changed
    if isinstance(value, (list, tuple)):
        changed = [hide_credentials(item) for item in value]
        if all(a is b for a, b in zip(changed, value)):
            return value
        return type(value)(changed) if isinstance(value, tuple) else changed
    return value


__all__ = ["HIDDEN", "hide_credentials"]
