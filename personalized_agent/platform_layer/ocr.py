"""READING THE WORDS IN A PICTURE, ON THIS COMPUTER - each system its own way,
none sending a picture anywhere (runtime/documents.py reads with it).

  - WINDOWS: its own OCR (Windows.Media.Ocr), through PowerShell. A language
    is one of Windows' optional parts; adding one asks for an
    administrator's yes (UAC), once.
  - MACOS: its own Vision framework, through PyObjC - every language built
    in (Japanese and Korean from macOS 13); PyObjC's Vision added to the
    application's own Python if it is not there.
  - LINUX: no OCR of its own. RapidOCR (English and Chinese), added to the
    application's own Python - no administrator; or Tesseract, if the
    system has it, with the languages installed for it.

A picture is read in every language there is of the five the application
speaks, and the reading that is most of its own script kept: Chinese read
by the Chinese engine, English by the English - whichever comes first in
the system's own list.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

#: The languages read: the five the application speaks.
LANGUAGES = (("en", "English"), ("zh", "Chinese"), ("es", "Spanish"), ("ja", "Japanese"),
             ("ko", "Korean"), ("ru", "Russian"), ("fr", "French"), ("de", "German"),
             ("it", "Italian"))
#: How long what is installed is believed before it is asked again.
KNOWN_FOR = 600.0

_CJK = re.compile(r"[一-鿿]")
_KANA = re.compile(r"[぀-ヿ]")
_HANGUL = re.compile(r"[가-힯]")
_LATIN = re.compile(r"[A-Za-zÀ-ÿ]")


def script_score(text: str, code: str) -> float:
    """How much of a reading is in its own language's script - the measure
    one engine's reading is kept over another's by."""
    cjk, kana = len(_CJK.findall(text)), len(_KANA.findall(text))
    hangul, latin = len(_HANGUL.findall(text)), len(_LATIN.findall(text))
    if code == "zh":
        return 2 * cjk + 0.5 * latin - 2 * kana
    if code == "ja":
        return 2 * kana + 1.5 * cjk + 0.5 * latin
    if code == "ko":
        return 2 * hangul + 0.5 * latin
    return latin - 2 * (cjk + kana + hangul)


def best_reading(readings: Dict[str, str], order: List[str]) -> str:
    """The reading kept, of one per language: the most of its own script -
    the first in the system's order when they are as good."""
    if not readings:
        return ""
    ranked = sorted(readings, key=lambda code: (-script_score(readings[code], code),
                                                order.index(code) if code in order else 99))
    return readings[ranked[0]]


def _code_of(tag: str) -> str:
    return str(tag or "").split("-")[0].lower()


class Ocr:
    """One system's OCR."""

    name = "none"

    def __init__(self) -> None:
        self._known: Optional[List[str]] = None
        self._known_at = 0.0
        self.downloading: Dict[str, Any] = {}

    def available(self) -> bool:
        return False

    def installed(self) -> List[str]:
        """The codes of the five languages it reads here, in the system's order."""
        if self._known is None or time.time() - self._known_at > KNOWN_FOR:
            try:
                self._known = self._installed()
            except Exception:
                log.debug("could not see which OCR languages there are", exc_info=True)
                self._known = []
            self._known_at = time.time()
        return list(self._known)

    def _installed(self) -> List[str]:
        return []

    def forget(self) -> None:
        self._known = None

    def read(self, path: str) -> str:
        return ""

    def can_add(self, code: str) -> bool:
        return False

    def add(self, codes: List[str]) -> Dict[str, Any]:
        return {"started": False, "how": "There is no OCR on this computer to add to."}

    def status(self) -> Dict[str, Any]:
        have = self.installed() if self.available() else []
        return {"engine": self.name, "available": self.available(),
                "downloading": dict(self.downloading),
                "languages": [{"code": code, "name": name, "installed": code in have,
                               "can_add": code not in have and self.can_add(code)}
                              for code, name in LANGUAGES]}


# -- Windows ---------------------------------------------------------------------------------------
_PREAMBLE = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Globalization.Language, Windows.Globalization, ContentType = WindowsRuntime]
"""

_LIST_SCRIPT = _PREAMBLE + r"""
$null = [Windows.System.UserProfile.GlobalizationPreferences, Windows.System.UserProfile, ContentType = WindowsRuntime]
$order = @([Windows.System.UserProfile.GlobalizationPreferences]::Languages)
foreach ($one in [Windows.Media.Ocr.OcrEngine]::AvailableRecognizerLanguages) { "LANG " + $one.LanguageTag }
foreach ($one in $order) { "PREFER " + $one }
"""

_READ_SCRIPT = _PREAMBLE + r"""
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
  $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
  $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, [Type]$type) {
  $task = $asTask.MakeGenericMethod($type).Invoke($null, @($op))
  $task.Wait(-1) | Out-Null
  $task.Result
}
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics, ContentType = WindowsRuntime]
$file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($env:PA_OCR_PATH)) ([Windows.Storage.StorageFile])
$stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
$decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
$bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
$tags = @($env:PA_OCR_LANGS -split ',' | Where-Object { $_ })
if ($tags.Count -eq 0) {
  $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
  if ($null -eq $engine) { exit 3 }
  $tags = @($engine.RecognizerLanguage.LanguageTag)
}
foreach ($tag in $tags) {
  $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage([Windows.Globalization.Language]::new($tag))
  if ($null -eq $engine) { continue }
  $result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
  "@@LANG " + $tag
  foreach ($line in $result.Lines) { $line.Text }
}
$stream.Dispose()
"""

#: The optional part of Windows for each language's OCR.
_WINDOWS_PARTS = {"en": "en-US", "zh": "zh-CN", "es": "es-ES", "ja": "ja-JP", "ko": "ko-KR",
                  "ru": "ru-RU", "fr": "fr-FR", "de": "de-DE", "it": "it-IT"}


def _powershell(script: str, env: Optional[Dict[str, str]] = None,
                timeout: float = 120) -> subprocess.CompletedProcess:
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        capture_output=True, timeout=timeout, env=dict(os.environ, **(env or {})),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


class WindowsOcr(Ocr):
    name = "Windows OCR"

    def __init__(self) -> None:
        super().__init__()
        self._tags: Dict[str, str] = {}

    def available(self) -> bool:
        return sys.platform == "win32"

    def _installed(self) -> List[str]:
        done = _powershell(_LIST_SCRIPT, timeout=60)
        lines = done.stdout.decode("utf-8", "replace").splitlines()
        tags = [line[5:].strip() for line in lines if line.startswith("LANG ")]
        prefer = [_code_of(line[7:].strip()) for line in lines if line.startswith("PREFER ")]
        wanted = [code for code, _name in LANGUAGES]
        self._tags = {}
        for tag in tags:
            code = _code_of(tag)
            if code in wanted and code not in self._tags:
                self._tags[code] = tag
        return sorted(self._tags, key=lambda code: (prefer.index(code) if code in prefer
                                                    else 50, wanted.index(code)))

    def read(self, path: str) -> str:
        order = self.installed()
        tags = [self._tags[code] for code in order if code in self._tags]
        done = _powershell(_READ_SCRIPT, {"PA_OCR_PATH": path, "PA_OCR_LANGS": ",".join(tags)})
        if done.returncode != 0:
            log.info("the OCR could not read a picture (%s)", done.returncode)
            return ""
        readings: Dict[str, List[str]] = {}
        current = ""
        for line in done.stdout.decode("utf-8", "replace").splitlines():
            if line.startswith("@@LANG "):
                current = _code_of(line[7:].strip())
                readings.setdefault(current, [])
            elif current:
                readings[current].append(line)
        return best_reading({code: "\n".join(lines).strip()
                             for code, lines in readings.items()}, order).strip()

    def can_add(self, code: str) -> bool:
        return code in _WINDOWS_PARTS

    def add(self, codes: List[str]) -> Dict[str, Any]:
        """Windows' optional parts for these languages - in a window of their
        own, after the administrator's yes Windows asks for (UAC)."""
        parts = [f"Language.OCR~~~{_WINDOWS_PARTS[code]}~0.0.1.0" for code in codes
                 if code in _WINDOWS_PARTS]
        if not parts:
            return {"started": False, "how": "Nothing to add."}
        command = "; ".join(f"Add-WindowsCapability -Online -Name '{part}'" for part in parts)
        launch = (f"Start-Process powershell -Verb RunAs -ArgumentList "
                  f"'-NoProfile', '-Command', \"{command}\"")
        try:
            subprocess.Popen(["powershell", "-NoProfile", "-Command", launch],
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError as exc:
            return {"started": False, "how": f"Windows could not be asked: {exc}"}
        self.forget()
        self.downloading = {"codes": codes, "since": time.time(),
                            "note": "Windows asks for an administrator's yes, then adds them."}
        return {"started": True}


# -- macOS ----------------------------------------------------------------------------------------------
_MAC_TAGS = {"en": "en-US", "zh": "zh-Hans", "es": "es-ES", "ja": "ja-JP", "ko": "ko-KR",
             "ru": "ru-RU", "fr": "fr-FR", "de": "de-DE", "it": "it-IT"}


class MacVisionOcr(Ocr):
    name = "Apple Vision"
    package = "pyobjc-framework-Vision"

    def available(self) -> bool:
        try:
            import Vision  # noqa: F401
            return True
        except ImportError:
            return False

    def _request(self):
        import Vision
        request = Vision.VNRecognizeTextRequest.alloc().init()
        request.setRecognitionLevel_(0)               # accurate
        request.setUsesLanguageCorrection_(True)
        return request

    def _installed(self) -> List[str]:
        if not self.available():
            return []
        supported, _error = self._request().supportedRecognitionLanguagesAndReturnError_(None)
        codes = {_code_of(str(tag)) for tag in supported or []}
        return [code for code, _name in LANGUAGES if code in codes]

    def read(self, path: str) -> str:
        import Vision
        from Foundation import NSURL
        request = self._request()
        request.setRecognitionLanguages_([_MAC_TAGS[code] for code in self.installed()])
        handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(
            NSURL.fileURLWithPath_(path), None)
        ok, _error = handler.performRequests_error_([request], None)
        if not ok:
            return ""
        return "\n".join(str(one.topCandidates_(1)[0].string())
                         for one in request.results() or [] if one.topCandidates_(1))

    def can_add(self, code: str) -> bool:
        # Every language is the system's own: what is missing is PyObjC's
        # Vision, which brings them all.
        return not self.available()

    def add(self, codes: List[str]) -> Dict[str, Any]:
        return _pip(self, [self.package])


# -- Linux ------------------------------------------------------------------------------------------------
_TESSERACT = {"en": "eng", "zh": "chi_sim", "es": "spa", "ja": "jpn", "ko": "kor",
              "ru": "rus", "fr": "fra", "de": "deu", "it": "ita"}


class LinuxOcr(Ocr):
    name = "RapidOCR / Tesseract"
    package = "rapidocr-onnxruntime"

    def _rapid(self) -> bool:
        try:
            import rapidocr_onnxruntime  # noqa: F401
            return True
        except ImportError:
            return False

    def _tesseract_languages(self) -> List[str]:
        if not shutil.which("tesseract"):
            return []
        done = subprocess.run(["tesseract", "--list-langs"], capture_output=True, timeout=30)
        have = set(done.stdout.decode("utf-8", "replace").split())
        return [code for code, name in _TESSERACT.items() if name in have]

    def available(self) -> bool:
        return self._rapid() or bool(shutil.which("tesseract"))

    def _installed(self) -> List[str]:
        codes = set(self._tesseract_languages())
        if self._rapid():
            codes |= {"en", "zh"}
        return [code for code, _name in LANGUAGES if code in codes]

    def read(self, path: str) -> str:
        readings: Dict[str, str] = {}
        tesseract = self._tesseract_languages()
        if tesseract:
            done = subprocess.run(
                ["tesseract", path, "-", "-l", "+".join(_TESSERACT[code] for code in tesseract)],
                capture_output=True, timeout=120)
            readings["tesseract"] = done.stdout.decode("utf-8", "replace").strip()
        if self._rapid():
            from rapidocr_onnxruntime import RapidOCR
            result, _elapsed = RapidOCR()(path)
            readings["rapid"] = "\n".join(str(one[1]) for one in result or [])
        if not readings:
            return ""
        # Tesseract's when it has more of the languages; RapidOCR's Chinese
        # and English are the better of the two.
        return max(readings.values(), key=len)

    def can_add(self, code: str) -> bool:
        return code in ("en", "zh") and not self._rapid()

    def add(self, codes: List[str]) -> Dict[str, Any]:
        if any(code in ("en", "zh") for code in codes) and not self._rapid():
            return _pip(self, [self.package])
        missing = " ".join(f"tesseract-ocr-{_TESSERACT[code]}" for code in codes
                           if code in _TESSERACT)
        return {"started": False, "how": (
            "These are the system's to add, with an administrator's yes - for example: "
            f"sudo apt install tesseract-ocr {missing}")}


def _pip(ocr: Ocr, packages: List[str]) -> Dict[str, Any]:
    """Packages added to the application's own Python, in the background."""
    from . import is_frozen
    if is_frozen():
        return {"started": False, "how": "This packaged copy of the application cannot add "
                "it: it comes with the next version."}
    if ocr.downloading.get("running"):
        return {"started": False, "how": "It is being added already."}
    ocr.downloading = {"running": True, "packages": packages, "since": time.time()}

    def run() -> None:
        try:
            done = subprocess.run([sys.executable, "-m", "pip", "install", *packages],
                                  capture_output=True, timeout=1800)
            ocr.downloading = {"running": False, "ok": done.returncode == 0,
                               "error": "" if done.returncode == 0 else
                               done.stderr.decode("utf-8", "replace")[-400:]}
        except Exception as exc:                    # pragma: no cover - said on the page
            ocr.downloading = {"running": False, "ok": False, "error": str(exc)[:400]}
        ocr.forget()

    threading.Thread(target=run, name="ocr-download", daemon=True).start()
    return {"started": True}


_current: Optional[Ocr] = None


def current() -> Ocr:
    """This computer's OCR."""
    global _current
    if _current is None:
        _current = (WindowsOcr() if sys.platform == "win32" else
                    MacVisionOcr() if sys.platform == "darwin" else LinuxOcr())
    return _current


__all__ = ["current", "Ocr", "WindowsOcr", "MacVisionOcr", "LinuxOcr", "LANGUAGES",
           "script_score", "best_reading"]
