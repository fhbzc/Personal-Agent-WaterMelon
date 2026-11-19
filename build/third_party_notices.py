"""THE THIRD-PARTY LIBRARIES' LICENSES, SHIPPED WITH EVERY BUILD (the user, 2026-11-19: "加一份许可清单，或者确认打包方式符合
LGPL" - svglib, LGPL, had been added to the requirements).

Written when a build is made, from what is installed: every library the requirements name and every library those
need in turn - its name, version, license, home page and the full text of each license file it carries - into
THIRD_PARTY_NOTICES.txt beside the program.

THE LGPL LIBRARIES STAY REPLACEABLE: a library under the LGPL that is pure Python (LGPL_SOURCE) is not packed into the
program's archive but shipped as its own source files in a folder of its own name beside it (build/common.py
lgpl_source_arguments), so anyone may put their own version of it there; the notices say where.
"""

from __future__ import annotations

import importlib.metadata as metadata
import re
from pathlib import Path
from typing import Dict, List, Optional, Set

#: Pure-Python LGPL libraries shipped as their own source, replaceable: the package and its distribution.
LGPL_SOURCE = {"svglib": "svglib", "pystray": "pystray"}
#: What each of them imports, which the build is told of (not analysed: it is not packed) - by platform, "" for all.
LGPL_IMPORTS = {"svglib": {"": ["lxml.etree", "tinycss2", "cssselect2", "webencodings", "reportlab.graphics.renderPDF",
                                "reportlab.graphics.shapes", "reportlab.lib.colors", "PIL.Image"]},
                "pystray": {"": ["six", "PIL.Image", "PIL.ImageDraw"],
                            "macos": ["AppKit", "Foundation", "Quartz", "objc"],
                            "linux": ["Xlib", "Xlib.display", "gi"]}}
#: A license file's name.
LICENSE_FILE = re.compile(r"(^|/)(licen[cs]e|copying|notice)[^/]*$", re.IGNORECASE)
NOTICES_NAME = "THIRD_PARTY_NOTICES.txt"


def _name_of(line: str) -> Optional[str]:
    """A requirement line's distribution name, when it applies here (its environment marker, if any, holds)."""
    line = line.split("#", 1)[0].strip()
    if not line or line.startswith("-"):
        return None
    requirement, _, marker = line.partition(";")
    if marker.strip():
        try:
            from packaging.markers import Marker
            if not Marker(marker.strip()).evaluate():
                return None
        except Exception:
            pass
    found = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", requirement)
    return found.group(1) if found else None


def bundled(requirements: Path) -> List[metadata.Distribution]:
    """The installed distributions a build carries: those the requirements name, and theirs in turn (no extras)."""
    wanted = [name for name in (_name_of(line) for line in requirements.read_text(encoding="utf-8").splitlines()) if name]
    seen: Set[str] = set()
    out: List[metadata.Distribution] = []
    while wanted:
        name = wanted.pop(0)
        key = re.sub(r"[-_.]+", "-", name).lower()
        if key in seen:
            continue
        seen.add(key)
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            continue
        out.append(dist)
        for requirement in dist.requires or []:
            if "extra ==" in requirement:
                continue
            inner = _name_of(requirement)
            if inner:
                wanted.append(inner)
    return sorted(out, key=lambda d: d.metadata["Name"].lower())


def _license_of(dist: metadata.Distribution) -> str:
    meta = dist.metadata
    said = meta.get("License-Expression") or (meta.get("License") or "").splitlines()[0:1]
    said = said if isinstance(said, str) else (said[0] if said else "")
    classifiers = [c.split("::")[-1].strip() for c in meta.get_all("Classifier") or [] if c.startswith("License ::")]
    return "; ".join(x for x in [said.strip()] + classifiers if x) or "see its license file"


def _texts_of(dist: metadata.Distribution) -> List[tuple]:
    texts = []
    for one in dist.files or []:
        if LICENSE_FILE.search(str(one).replace("\\", "/")) and not str(one).endswith((".pyc", ".py")):
            try:
                texts.append((str(one), Path(dist.locate_file(one)).read_text(encoding="utf-8", errors="replace")))
            except OSError:
                continue
    return texts


def write_notices(requirements: Path, out: Path) -> Dict[str, str]:
    """THIRD_PARTY_NOTICES.txt written at `out`: each library's name, version, license and license texts. {name: license}."""
    dists = bundled(requirements)
    parts = ["THIRD-PARTY SOFTWARE NOTICES",
             "",
             "This program includes the third-party libraries below, each under its own license, whose texts follow.",
             ""]
    lgpl = [name for name, dist_name in LGPL_SOURCE.items() if any(d.metadata["Name"].lower() == dist_name for d in dists)]
    if lgpl:
        parts += ["LIBRARIES UNDER THE GNU LESSER GENERAL PUBLIC LICENSE",
                  "",
                  *(f"- {name}: shipped as its own source files, unmodified, in the folder \"{name}\" beside the program "
                    f"(in its \"_internal\" folder on Windows and Linux, in \"Contents/Resources\" on macOS). You may replace "
                    f"that folder with your own version of {name}; the program uses whatever is there." for name in lgpl),
                  ""]
    if any(d.metadata["Name"].lower() == "av" for d in dists):
        parts += ["- FFmpeg (in PyAV): its libraries, under the LGPL, are shipped as separate shared libraries "
                  "(avcodec, avformat, avutil and the rest) beside the program and may be replaced. Their source: "
                  "https://ffmpeg.org.", ""]
    summary: Dict[str, str] = {}
    for dist in dists:
        meta = dist.metadata
        license_said = _license_of(dist)
        summary[meta["Name"]] = license_said
        parts += ["=" * 79, f"{meta['Name']} {dist.version}", f"License: {license_said}"]
        home = meta.get("Home-page") or next((u.split(",", 1)[-1].strip() for u in meta.get_all("Project-URL") or []), "")
        if home:
            parts.append(f"Home: {home}")
        for path, text in _texts_of(dist):
            parts += ["", f"--- {path} ---", text.strip()]
        parts.append("")
    text = "\n".join(parts).replace("\r\n", "\n") + "\n"
    # No control characters (a license's form feeds made line breaks): a plain text file anyone can read.
    text = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", lambda m: "\n" if m.group(0) in "\x0b\x0c" else "", text)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    return summary


__all__ = ["LGPL_SOURCE", "LGPL_IMPORTS", "NOTICES_NAME", "bundled", "write_notices"]
