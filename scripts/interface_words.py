"""The interface's words, and the tables they are shown from in each language.

    python scripts/interface_words.py              # what is not in the tables yet
    python scripts/interface_words.py --merge F    # put translations in

THE TABLES (personalized_agent/api/web/lang/<code>.json, one per display
language): English on the left, the language's own words on the right,
every one of the interface's texts in each - a word the language writes as
English is there too, the same on both sides. Places that a number, a name
or another phrase fills are numbered: "{0} states read". The page shows
them (api/web/i18n.js) and so does the desktop (common/interface_words.py).
Nothing is translated on the fly.

WHAT THE INTERFACE CAN SHOW is read off its sources: the string and template
literals of the page's scripts - split at HTML tags, each ${...} a numbered
place - the text and the labelling attributes of its pages, and what the
server sends to be shown (the settings' names and explanations, their
choices). tests/test_interface_languages.py fails while any of it is not in
every table.

AFTER A CHANGE TO A PAGE: run this, translate what it lists into every
language (as a model would: the whole phrase, places kept, in the words the
table already uses), and merge it - a JSON file {"<code>": {"<English>":
"<translation>"}} with every language in it.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "personalized_agent" / "api" / "web"
TABLES = WEB / "lang"
LANGUAGES = ("zh", "es", "ja", "ko", "ru", "fr", "de", "it")
SCRIPTS = ["app.js", "debugger.js", "chat.js", "chat-debugger.js", "chat-navigation.js",
           "listener-debugger.js"]
PAGES = ["index.html", "chat.html"]
REGEX_BEFORE = set("(,=:[!&|?{};+-*%<>~^")
ATTRS = ("title", "placeholder", "aria-label", "label", "alt")
# What the server sends that is not the interface's words: names of
# products and models, and anything of the user's.
DATA = re.compile(r"[A-Za-z]:\\|^https?://\S*$|[\w.+-]+@[\w-]+\.\w+|claude-[a-z]+-\d|"
                  r"^\S*\d{1,2}:\d\d\S*$|^\d{4}-\d\d-\d\d|^v\d|\(UTC|^\d+ px$")
BRANDS = {"Anthropic", "OpenRouter", "OpenAI", "Google Gemini", "DeepSeek", "Moonshot (Kimi)",
          "Alibaba Qwen (DashScope)", "Zhipu GLM", "xAI (Grok)", "Mistral", "Groq", "SiliconFlow",
          "Together AI", "Watermelon", "Claude Code", "Codex", "Brave"}


def literals(src):
    """(kind, pieces, line): pieces is a list of str or None (an ${} slot)."""
    i, n, line = 0, len(src), 1
    prev = ""            # last significant character
    prev_word = ""
    found = []

    def template(i, line):
        pieces, buf = [], []
        start_line = line
        while i < n:
            c = src[i]
            if c == "\\":
                buf.append(src[i:i + 2]); i += 2; continue
            if c == "`":
                pieces.append("".join(buf))
                return i + 1, line, pieces, start_line
            if c == "$" and i + 1 < n and src[i + 1] == "{":
                pieces.append("".join(buf)); buf = []
                pieces.append(None)
                i, line = code(i + 2, line, closing="}")
                continue
            if c == "\n":
                line += 1
            buf.append(c); i += 1
        return i, line, pieces, start_line

    def code(i, line, closing=None):
        nonlocal prev, prev_word
        depth = 0
        while i < n:
            c = src[i]
            if c == "\n":
                line += 1; i += 1; continue
            if c in " \t\r":
                i += 1; continue
            if src.startswith("//", i):
                j = src.find("\n", i); i = n if j < 0 else j; continue
            if src.startswith("/*", i):
                j = src.find("*/", i + 2); line += src[i:j].count("\n"); i = j + 2; continue
            if c in "'\"":
                j, buf = i + 1, []
                while j < n and src[j] != c:
                    if src[j] == "\\":
                        buf.append(src[j:j + 2]); j += 2; continue
                    if src[j] == "\n":
                        break
                    buf.append(src[j]); j += 1
                found.append(("str", ["".join(buf)], line, i, j + 1))
                i = j + 1; prev = c; prev_word = ""; continue
            if c == "`":
                at = i
                i, line2, pieces, sl = template(i + 1, line)
                found.append(("tpl", pieces, sl, at, i)); line = line2
                prev = "`"; prev_word = ""; continue
            if c == "/" and (prev in REGEX_BEFORE or prev == "" or prev_word in ("return", "typeof", "case")):
                j, cls = i + 1, False
                while j < n:
                    if src[j] == "\\":
                        j += 2; continue
                    if src[j] == "[":
                        cls = True
                    elif src[j] == "]":
                        cls = False
                    elif src[j] == "/" and not cls:
                        break
                    elif src[j] == "\n":
                        break
                    j += 1
                j += 1
                while j < n and src[j].isalpha():
                    j += 1
                i = j; prev = "x"; prev_word = ""; continue
            if closing:
                if c == "{":
                    depth += 1
                elif c == "}":
                    if depth == 0:
                        return i + 1, line
                    depth -= 1
            m = re.match(r"[A-Za-z_$][\w$]*", src[i:i + 60])
            if m:
                prev_word = m.group(0); prev = "a"; i += len(prev_word); continue
            if c.isdigit():
                prev = "0"; prev_word = ""; i += 1; continue
            prev = c; prev_word = ""; i += 1
        return i, line

    code(0, 1)
    # "a" + "b" + `c${x}`: one text at run time. Literals nested in a
    # template's ${} come after it in the list, so only neighbours in the
    # source with nothing but + between them are joined.
    found.sort(key=lambda f: f[3])
    merged = []
    for f in found:
        if merged and re.fullmatch(r"\s*\+\s*", src[merged[-1][4]:f[3]]):
            last = merged[-1]
            merged[-1] = (last[0], last[1] + f[1], last[2], last[3], f[4])
        else:
            merged.append(f)
    return [(k, p, l) for k, p, l, _a, _b in merged]


def unescape_js(s):
    return re.sub(r"\\(u\{[0-9a-fA-F]+\}|u[0-9a-fA-F]{4}|x[0-9a-fA-F]{2}|.)",
                  lambda m: (chr(int(m.group(1)[2:-1], 16)) if m.group(1).startswith("u{")
                             else chr(int(m.group(1)[1:], 16)) if m.group(1)[0] in "ux" and len(m.group(1)) > 1
                             else {"n": "\n", "t": "\t"}.get(m.group(1), m.group(1))), s)


def texts_of(pieces):
    """Join the pieces with numbered slots, then split at tags: the text runs
    between tags, and the labelling attributes inside them."""
    slot, joined = 0, []
    for p in pieces:
        if p is None:
            joined.append("\x00%d\x00" % slot); slot += 1
        else:
            joined.append(p)
    s = unescape_js("".join(joined))
    out = []
    if "<" in s and re.search(r"<[a-zA-Z/!]", s):
        for tag in re.finditer(r"<[a-zA-Z][^<>]*>", s):
            for a in ATTRS:
                for m in re.finditer(r'\s%s="([^"]*)"' % a, tag.group(0)):
                    out.append(m.group(1))
        runs = re.split(r"<[^<>]*>|<[a-zA-Z/][^<>]*$|^[^<>]*>", s)
        out.extend(runs)
    else:
        out.append(s)
    result = []
    for t in out:
        t = html.unescape(t)
        t = re.sub(r"\s+", " ", t).strip()
        # renumber the slots that survived, in order
        k = [0]
        def renum(m):
            v = "{%d}" % k[0]; k[0] += 1; return v
        t = re.sub(r"\x00\d+\x00", renum, t)
        result.append(t)
    return result


def looks_like_ui(t):
    bare = re.sub(r"\{\d+\}", "", t).strip()
    if not re.search(r"[A-Za-z]{2}", bare):
        return False
    if re.fullmatch(r"[a-z0-9_\-./:#\[\]=*]+", bare):                 # identifiers, selectors, paths
        return False
    # Code, not prose: a semicolon with no space after it, braces, arrows -
    # an explanation's "; " and a label's "folder:" are prose.
    if re.search(r"^(https?:|/api/|#|\.[\w-]|--)|[{}]|;\S|=>|\$\(|\(\)|\bvar\(|px\b|rgba?\(|"
                 r"^[a-z-]+:\S", bare):
        return False
    if re.search(r'="|^\[|^&|^\?|^input\[|^[a-z]+[A-Z][\w-]*$|^[a-z]+-[a-z-]+=', t):  # selectors, markup
        return False
    if bare in ("If-None-Match", "ETag", "Content-Type", "ArrowLeft", "ArrowRight", "Escape",
                "Home", "End", "Enter", "Tab", "DOMContentLoaded", "ModelRequest"):
        return False
    if re.fullmatch(r"(?:[a-z0-9]+(?:-[a-z0-9]+)+\s*)+", bare):  # class names: a-b c-d
        return False
    if re.fullmatch(r"[A-Z0-9_]+", bare):                          # CONSTANTS
        return False
    if re.fullmatch(r"[a-z]+[A-Z]\w*", bare):                      # camelCase
        return False
    return True




def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def source_texts() -> Dict[str, str]:
    """Every text the page's sources can show: {text: where it is}."""
    seen: Dict[str, str] = {}

    def add(text: str, where: str) -> None:
        text = _clean(text)
        if text and looks_like_ui(text) and text not in BRANDS and not DATA.search(text):
            seen.setdefault(text, where)

    for name in SCRIPTS:
        source = (WEB / name).read_text(encoding="utf-8")
        for _kind, pieces, line in literals(source):
            for text in texts_of(pieces):
                add(text, f"{name}:{line}")
    for name in PAGES:
        source = (WEB / name).read_text(encoding="utf-8")
        body = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>|<!--[\s\S]*?-->", "", source)
        for tag in re.finditer(r"<[a-zA-Z][^<>]*>", body):
            for attribute in ATTRS:
                for found in re.finditer(r'\s%s="([^"]*)"' % attribute, tag.group(0)):
                    add(html.unescape(found.group(1)), name)
        for run in re.split(r"<[^<>]*>", body):
            add(html.unescape(run), name)
    return seen


def _strings(value: Any, out: List[str]) -> None:
    if isinstance(value, str):
        out.append(value)
    elif isinstance(value, dict):
        for one in value.values():
            _strings(one, out)
    elif isinstance(value, list):
        for one in value:
            _strings(one, out)


def server_texts(settings: Dict[str, Any]) -> Dict[str, str]:
    """What GET /api/settings sends to be shown: each setting's name,
    explanation and section, and the names of its choices."""
    said: List[str] = []
    schema = settings.get("schema") or []
    for field in schema if isinstance(schema, list) else schema.values():
        for key in ("label", "description", "section", "help", "unit", "placeholder"):
            if isinstance(field.get(key), str):
                said.append(field[key])
    _strings(settings.get("choice_labels"), said)
    _strings(settings.get("choice_notes"), said)
    out: Dict[str, str] = {}
    for text in said:
        text = _clean(text)
        if text and looks_like_ui(text) and text not in BRANDS and not DATA.search(text):
            out.setdefault(text, "server")
    return out


def tables() -> Dict[str, Dict[str, str]]:
    return {code: json.loads((TABLES / f"{code}.json").read_text(encoding="utf-8"))
            for code in LANGUAGES}


def missing(texts: Iterable[str]) -> List[str]:
    """The texts some table has no entry for."""
    have = tables()
    return sorted({t for t in texts if any(t not in table for table in have.values())},
                  key=str.lower)


def write(code: str, table: Dict[str, str]) -> None:
    ordered = dict(sorted(table.items(), key=lambda kv: kv[0].lower()))
    (TABLES / f"{code}.json").write_text(json.dumps(ordered, ensure_ascii=False, indent=0) + "\n",
                                         encoding="utf-8", newline="\n")


def merge(path: Path) -> None:
    """Translations into the tables: {"<code>": {"<English>": "<words>"}}."""
    given = json.loads(path.read_text(encoding="utf-8"))
    keys = set().union(*(set(v) for v in given.values())) if given else set()
    lacking = [code for code in LANGUAGES if set((given.get(code) or {})) != keys]
    if lacking:
        raise SystemExit(f"every language, every text: not so for {', '.join(lacking)}")
    for code in LANGUAGES:
        table = json.loads((TABLES / f"{code}.json").read_text(encoding="utf-8"))
        table.update(given[code])
        write(code, table)
    print(f"merged {len(keys)} texts into {len(LANGUAGES)} tables")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--merge", type=Path, help="a JSON file of translations to put in")
    args = parser.parse_args()
    if args.merge:
        merge(args.merge)
        return 0
    texts = source_texts()
    lacking = missing(texts)
    print(json.dumps({t: texts[t] for t in lacking}, ensure_ascii=False, indent=1))
    print(f"{len(lacking)} of {len(texts)} texts are not in every table "
          "(the server's words are checked by tests/test_interface_languages.py)",
          file=sys.stderr)
    return 1 if lacking else 0


if __name__ == "__main__":
    sys.exit(main())
