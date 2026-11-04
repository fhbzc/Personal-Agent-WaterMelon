"""A DOCUMENT'S PAGES AS PICTURES, for a model that can see them (the user, 2026-11-04:
a slide's arrows and layout never reached it - only its words). A model takes
words, pictures and PDFs, not Office files, so a file is drawn here first:

  * a PDF as it is, with pypdfium2;
  * PowerPoint and Word files made a PDF by Office itself where it is there
    (Windows, in the background, the user agreed; never by AppleScript on a
    Mac), or else by LibreOffice;
  * neither there, a deck's layout as words (`slide_layout`): what is where and
    which arrow joins what, read from the file itself.

A deck is all its pages; of a PDF or a Word file only the pages with a picture
or a chart - the words of the others are given as words, at far less cost.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional
from xml.etree import ElementTree

log = logging.getLogger(__name__)

#: The most pages drawn of one file, and how long Office is waited for.
MOST_PAGES = 20
OFFICE_SECONDS = 120
#: A page drawn with this many lines and shapes or more is taken for a chart.
CHART_PATHS = 30
LONGEST_SIDE = 1568
DECKS = (".pptx", ".ppt")
WRITINGS = (".docx", ".doc")
LIBREOFFICE = (r"C:\Program Files\LibreOffice\program\soffice.exe",
               "/Applications/LibreOffice.app/Contents/MacOS/soffice")

#: Office, run hidden. PowerPoint keeps one process for all its files, so one
#: the user has open is left open; Word gets a process of its own.
#:
#: NO MACRO RUNS: a file opened by a program, unlike one opened by hand, has its
#: macros allowed with no question asked, and these are files anyone may have
#: sent the user. AutomationSecurity 3 forces them off before the file is opened
#: - read back, and nothing is opened if it did not take. PowerPoint's is put
#: back: the process may be the user's own.
POWERPOINT = r"""
$ErrorActionPreference = 'Stop'
$running = [bool](Get-Process POWERPNT -ErrorAction SilentlyContinue)
$app = New-Object -ComObject PowerPoint.Application
$app.DisplayAlerts = 1
$was = $app.AutomationSecurity
$app.AutomationSecurity = 3
try {
  if ($app.AutomationSecurity -ne 3) { throw 'macros could not be turned off' }
  $doc = $app.Presentations.Open($env:PA_FROM, -1, 0, 0)
  try { $doc.SaveAs($env:PA_TO, 32) } finally { $doc.Close() }
} finally { $app.AutomationSecurity = $was }
if (-not $running -and $app.Presentations.Count -eq 0) { $app.Quit() }
"""
WORD = r"""
$ErrorActionPreference = 'Stop'
$app = New-Object -ComObject Word.Application
$app.Visible = $false
$app.DisplayAlerts = 0
try {
  $app.AutomationSecurity = 3
  if ($app.AutomationSecurity -ne 3) { throw 'macros could not be turned off' }
  $doc = $app.Documents.Open($env:PA_FROM, $false, $true, $false)
  try { $doc.ExportAsFixedFormat($env:PA_TO, 17) } finally { $doc.Close(0) }
} finally { $app.Quit() }
"""


#: One Office job at a time: PowerPoint is one process, however many ask.
_office = threading.Lock()


def draws(path: Any) -> bool:
    """Whether this kind of file has pages to draw."""
    return Path(str(path)).suffix.lower() in (".pdf", *DECKS, *WRITINGS)


def kept_pages(folder: Any) -> Dict[str, Any]:
    """What was drawn before: {"pages", "of", "by"} - or an empty one."""
    try:
        said = json.loads((Path(folder) / "pages.json").read_text(encoding="utf-8"))
        return {**said, "pages": [str(Path(folder) / one) for one in said["pages"]]}
    except (OSError, ValueError, KeyError):
        return {"pages": [], "of": 0, "by": ""}


def draw_pages(path: Any, folder: Any, stop: Optional[threading.Event] = None) -> Dict[str, Any]:
    """The file's pages drawn into `folder`, once: "pages" their paths, "of"
    how many it has, "by" what read it ("" - nothing could). `stop` set, it
    goes no further - Office, once at it, is left to finish, never killed:
    the process may be the user's own PowerPoint, their files open in it."""
    path, folder = Path(str(path)), Path(folder)
    kept = kept_pages(folder)
    if kept["by"]:
        return kept
    nothing = {"pages": [], "of": 0, "by": ""}
    stopped = stop.is_set if stop is not None else (lambda: False)
    folder.mkdir(parents=True, exist_ok=True)
    suffix = path.suffix.lower()
    pdf, by = path, "pdf"
    if suffix != ".pdf":
        pdf = folder / "document.pdf"
        with _office:
            by = "" if stopped() else _office_pdf(path, pdf)
        if not by or stopped():
            return nothing
    try:
        names, count = _draw(pdf, folder, suffix in DECKS, stopped)
        if stopped():
            return nothing
    except Exception:
        log.info("%s could not be drawn", path.name, exc_info=True)
        return {"pages": [], "of": 0, "by": ""}
    finally:
        if pdf != path:
            pdf.unlink(missing_ok=True)
    (folder / "pages.json").write_text(json.dumps({"pages": names, "of": count, "by": by}),
                                       encoding="utf-8")
    return kept_pages(folder)


def _draw(pdf: Path, folder: Path, every: bool, stopped: Any = lambda: False) -> tuple:
    import pypdfium2 as pdfium
    document = pdfium.PdfDocument(str(pdf))
    names: List[str] = []
    try:
        count = len(document)
        for n in range(count):
            if len(names) >= MOST_PAGES or stopped():
                break
            page = document[n]
            if every or _has_a_figure(page):
                width, height = page.get_size()
                image = page.render(scale=LONGEST_SIDE / max(width, height, 1)).to_pil()
                name = f"page-{n + 1:03d}.jpg"
                image.convert("RGB").save(folder / name, "JPEG", quality=85)
                names.append(name)
            page.close()
    finally:
        document.close()
    return names, count


def _has_a_figure(page: Any) -> bool:
    """A picture on the page, or enough lines and shapes to be a chart."""
    import pypdfium2.raw as raw
    paths = 0
    for one in page.get_objects(max_depth=2):
        if one.type == raw.FPDF_PAGEOBJ_IMAGE:
            return True
        if one.type == raw.FPDF_PAGEOBJ_PATH:
            paths += 1
            if paths >= CHART_PATHS:
                return True
    return False


def _office_pdf(path: Path, pdf: Path) -> str:
    """The file made a PDF; what made it, or ""."""
    if sys.platform.startswith("win"):
        program = POWERPOINT if path.suffix.lower() in DECKS else WORD
        if _run_office(program, path, pdf):
            return "powerpoint" if program is POWERPOINT else "word"
    return "libreoffice" if _libreoffice(path, pdf) else ""


def _run_office(program: str, path: Path, pdf: Path) -> bool:
    try:
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", program],
                       env=dict(os.environ, PA_FROM=str(path.resolve()), PA_TO=str(pdf.resolve())),
                       capture_output=True, timeout=OFFICE_SECONDS, check=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError) as exc:
        log.info("Office could not draw %s: %s", path.name, type(exc).__name__)
        return False
    return pdf.is_file()


def _libreoffice(path: Path, pdf: Path) -> bool:
    program = shutil.which("soffice") or shutil.which("libreoffice") or next(
        (one for one in LIBREOFFICE if Path(one).is_file()), None)
    if not program:
        return False
    try:
        # A profile of its own: the user's LibreOffice is left as it is.
        subprocess.run([program, "--headless", f"-env:UserInstallation={(pdf.parent / 'lo').as_uri()}",
                        "--convert-to", "pdf", "--outdir", str(pdf.parent), str(path)],
                       capture_output=True, timeout=OFFICE_SECONDS, check=True)
        made = pdf.parent / f"{path.stem}.pdf"
        if made.is_file() and made != pdf:
            made.replace(pdf)
    except (OSError, subprocess.SubprocessError) as exc:
        log.info("LibreOffice could not draw %s: %s", path.name, type(exc).__name__)
        return False
    finally:
        shutil.rmtree(pdf.parent / "lo", ignore_errors=True)
    return pdf.is_file()


# -- a deck's layout, in words ---------------------------------------------------------------
NS = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main",
      "p": "http://schemas.openxmlformats.org/presentationml/2006/main"}


def _place(shape: Any, size: tuple) -> str:
    off, ext = shape.find(".//a:xfrm/a:off", NS), shape.find(".//a:xfrm/a:ext", NS)
    if off is None or ext is None:
        return ""
    return (f"at {100 * int(off.get('x')) // size[0]}% across, {100 * int(off.get('y')) // size[1]}% "
            f"down, {100 * int(ext.get('cx')) // size[0]}% wide")


def slide_layout(path: Any) -> str:
    """Where each thing is on each slide, and what each arrow joins - for a
    model when nothing could draw the deck. "" when it is no such file."""
    if Path(str(path)).suffix.lower() != ".pptx":
        return ""
    try:
        with zipfile.ZipFile(str(path)) as deck:
            size = (9144000, 6858000)
            shown = ElementTree.fromstring(deck.read("ppt/presentation.xml")).find("p:sldSz", NS)
            if shown is not None:
                size = (int(shown.get("cx")), int(shown.get("cy")))
            names = sorted((one for one in deck.namelist()
                            if re.fullmatch(r"ppt/slides/slide\d+\.xml", one)),
                           key=lambda one: int(re.search(r"(\d+)\.xml", one).group(1)))
            parts = []
            for n, name in enumerate(names, 1):
                parts.append(_slide_lines(ElementTree.fromstring(deck.read(name)), n, size))
    except (OSError, KeyError, ValueError, zipfile.BadZipFile, ElementTree.ParseError):
        log.info("the deck's layout could not be read", exc_info=True)
        return ""
    return "\n".join(parts)


def _slide_lines(slide: Any, number: int, size: tuple) -> str:
    said: Dict[str, str] = {}
    lines = [f"Slide {number}:"]
    for shape in slide.iter(f"{{{NS['p']}}}sp"):
        words = " ".join(one.text or "" for one in shape.iter(f"{{{NS['a']}}}t")).strip()
        about = shape.find(".//p:cNvPr", NS)
        ident = about.get("id") if about is not None else ""
        said[ident] = words or (about.get("name") if about is not None else "") or "a shape"
        lines.append(f'- "{said[ident]}" {_place(shape, size)}'.rstrip())
    for picture in slide.iter(f"{{{NS['p']}}}pic"):
        lines.append(f"- a picture {_place(picture, size)}".rstrip())
    for line in slide.iter(f"{{{NS['p']}}}cxnSp"):
        start, end = line.find(".//a:stCxn", NS), line.find(".//a:endCxn", NS)
        arrow = line.find(".//a:tailEnd", NS) is not None or line.find(".//a:headEnd", NS) is not None
        kind = "an arrow" if arrow else "a line"
        if start is not None and end is not None:
            lines.append(f'- {kind} from "{said.get(start.get("id"), "?")}" '
                         f'to "{said.get(end.get("id"), "?")}"')
        else:
            lines.append(f"- {kind} {_place(line, size)}".rstrip())
    return "\n".join(lines)


__all__ = ["MOST_PAGES", "draw_pages", "draws", "kept_pages", "slide_layout"]
