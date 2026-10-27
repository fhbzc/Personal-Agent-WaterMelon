"""A window's words, read on this Mac by Apple Vision - in memory, bounded.

APPLE VISION, ACCURATE (the user, 2026-10-26: Vision, not Tesseract, for
WeChat as for every picture - built into macOS, nothing to install, and as
a rule more accurate on screen text, Chinese most of all). The request is
Vision's accurate level (`VNRequestTextRecognitionLevelAccurate`, 0), with
language correction, in Simplified Chinese and English. The image travels
in memory only - never written to disk - and the reading has a deadline.

`read_layout` keeps each line Vision found with its box and confidence:
what the WeChat layout (`wechat_layout.parse_wechat_frame`) sorts into
bubbles and sides. Vision's boxes are a line each, measured from the
picture's bottom left as fractions of it; they are given back in pixels
from the top left, as the layout reads them.
"""
from __future__ import annotations

import io
import re
import threading
from typing import Any, Dict, List


class LocalWindowOcr:
    #: Seconds a reading may take before it is given up.
    TIMEOUT = 8
    #: What Vision is asked to read: Simplified Chinese and English.
    RECOGNITION_LANGUAGES = ('zh-Hans', 'en-US')
    #: Vision's accurate level - its own default, said outright.
    ACCURATE = 0
    #: The most lines kept from one reading.
    MOST_LINES = 5000

    def __init__(self) -> None:
        self.method = ''
        self.last_error = ''

    @staticmethod
    def _vision() -> Any:
        try:
            import Vision                                       # noqa: F401
            return Vision
        except ImportError:
            return None

    def available(self) -> bool:
        return self._vision() is not None

    def languages(self) -> List[str]:
        return list(self.RECOGNITION_LANGUAGES) if self.available() else []

    def _observations(self, png: bytes) -> list:
        """Vision's text observations of the picture, within the deadline."""
        self.method = ''
        Vision = self._vision()
        if Vision is None:
            self.last_error = 'Apple Vision is not available (pyobjc-framework-Vision)'
            raise RuntimeError(self.last_error)
        said: Dict[str, Any] = {}

        def recognise() -> None:
            try:
                from Foundation import NSData
                data = NSData.dataWithBytes_length_(png, len(png))
                request = Vision.VNRecognizeTextRequest.alloc().init()
                request.setRecognitionLevel_(self.ACCURATE)
                request.setUsesLanguageCorrection_(True)
                request.setRecognitionLanguages_(list(self.RECOGNITION_LANGUAGES))
                handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(data, None)
                ok, error = handler.performRequests_error_([request], None)
                if not ok:
                    said['error'] = f'Apple Vision could not read the window: {error}'
                    return
                said['results'] = list(request.results() or [])
            except Exception as exc:                  # pragma: no cover - macOS only
                said['error'] = f'Apple Vision failed: {exc}'

        worker = threading.Thread(target=recognise, name='window-vision-ocr', daemon=True)
        worker.start()
        worker.join(self.TIMEOUT)
        if worker.is_alive():
            self.last_error = f'Apple Vision OCR timed out after {self.TIMEOUT} seconds'
            raise RuntimeError(self.last_error)
        if said.get('error'):
            self.last_error = str(said['error'])[:500]
            raise RuntimeError(self.last_error)
        self.method = 'macos_vision_ocr'
        self.last_error = ''
        return said.get('results') or []

    @staticmethod
    def _best(observation: Any) -> Any:
        candidates = observation.topCandidates_(1)
        return candidates[0] if candidates else None

    def read(self, png: bytes) -> str:
        lines = []
        for observation in self._observations(png)[:self.MOST_LINES]:
            best = self._best(observation)
            text = str(best.string()).strip() if best is not None else ''
            if text:
                lines.append(text)
        return '\n'.join(lines).strip()

    def read_layout(self, png: bytes) -> dict:
        """One reading, keeping each line's box (pixels, from the top left)
        and confidence (0-100) - the picture itself is not kept."""
        from PIL import Image
        width, height = Image.open(io.BytesIO(png)).size
        words, lines = [], []
        for number, observation in enumerate(self._observations(png)[:self.MOST_LINES]):
            best = self._best(observation)
            text = str(best.string()).strip() if best is not None else ''
            if not text:
                continue
            box = observation.boundingBox()
            left = box.origin.x * width
            top = (1.0 - box.origin.y - box.size.height) * height
            wide, tall = box.size.width * width, box.size.height * height
            if wide <= 0 or tall <= 0:
                continue
            confidence = max(0.0, min(1.0, float(best.confidence()))) * 100
            words.append({'text': text[:2000],
                          'bbox': [max(0, int(round(left))), max(0, int(round(top))),
                                   max(1, int(round(wide))), max(1, int(round(tall)))],
                          'confidence': round(confidence, 1), 'line': (number,)})
            lines.append(text)
        text = '\n'.join(re.sub(r'(?<=[㐀-鿿])\s+(?=[㐀-鿿，。！？、：；])', '', one)
                         for one in lines)
        return {'text': text[:16000], 'words': words, 'width': width, 'height': height}

    def status(self) -> dict:
        return {'engine': 'apple_vision', 'method': self.method,
                'apple_vision_enabled': True, 'level': 'accurate',
                'last_error': self.last_error, 'available': self.available(),
                'languages': self.languages(),
                'recognition_languages': list(self.RECOGNITION_LANGUAGES),
                'timeout_seconds': self.TIMEOUT}
