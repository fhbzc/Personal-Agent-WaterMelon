"""macOS foreground text: Accessibility first, Apple Vision OCR second (19.2).

No database access, scrolling, background windows, or retained screenshots.
Native imports stay lazy so other platforms can import and test the reader.
"""
from __future__ import annotations

import time
import hashlib
import threading
import copy
from collections import OrderedDict
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Any, Callable

from .window_text import WindowText, is_wechat_application


@dataclass
class FrontWindow:
    application: str
    title: str
    pid: int
    handle: int
    bounds: tuple
    element: Any = None

    @property
    def identity(self):
        return self.pid, self.handle, self.title, self.bounds


class CaptureFocusChanged(RuntimeError):
    """The identified window was no longer in front while capturing it."""


class MacWindowTextReader:
    def __init__(self, backend=None):
        self.backend = backend
        self.last_error = ""
        self.last_method = ""
        self.ax_readings = 0
        self.ocr_readings = 0
        # Metadata only. A successful read of another app must not hide a
        # failed chat read; clear its failure only when that app recovers.
        self._application_reads = OrderedDict()
        self._status_lock = threading.RLock()

    def _attempt(self, front):
        with self._status_lock:
            item = self._application_reads.setdefault(front.application, {
                'application': front.application, 'attempts': 0, 'successes': 0,
                'failures': 0, 'consecutive_failures': 0, 'status': 'reading',
                'last_success_at': None, 'last_failure_at': None, 'last_error': '',
                'method': '', 'reason': ''})
            item['attempts'] += 1
            item['last_attempt_at'] = datetime.now(timezone.utc).isoformat()
            item['last_result'] = 'reading'
            self._application_reads.move_to_end(front.application)
            while len(self._application_reads) > 40:
                self._application_reads.popitem(last=False)

    def _result(self, front, status, *, method='', error='', reason=''):
        with self._status_lock:
            item = self._application_reads[front.application]
            if status == 'skipped' and item['status'] in ('failed', 'degraded'):
                item['last_result'] = 'skipped'
                return
            item.update(status=status, last_result=status, method=method, reason=reason)
            if method.endswith('_ocr'):
                ocr_status = getattr(self.backend, 'ocr_status', None)
                if callable(ocr_status):
                    item['ocr'] = ocr_status()
                layout = getattr(self.backend, 'ocr_wechat_capture', {})
                if is_wechat_application(front.application) and layout:
                    item['wechat_layout'] = {'status':layout.get('layout_status'),
                        'reason':layout.get('layout_reason'),
                        'parser_version':layout.get('parser_version'),
                        'diagnostics':layout.get('diagnostics',{}),
                        'messages':len(layout.get('messages') or []),
                        'unknown_senders':sum(m.get('sender_role') == 'unknown' for m in layout.get('messages') or [])}
            if status in ('failed', 'degraded'):
                item['failures'] += 1
                item['consecutive_failures'] += 1
                item['last_failure_at'] = datetime.now(timezone.utc).isoformat()
                item['last_error'] = error[:500]
            elif status == 'ok':
                item['successes'] += 1
                item['consecutive_failures'] = 0
                item['last_success_at'] = datetime.now(timezone.utc).isoformat()
                item['last_error'] = ''

    def available(self):
        try:
            if self.backend is None:
                self.backend = NativeReader()
            return True
        except (ImportError, OSError) as exc:
            self.last_error = "Install macOS window-reading dependencies: " + str(exc)
            return False

    def read(self, allowed: Callable[[FrontWindow], bool]):
        started = time.monotonic()
        self.last_method = ""
        self.last_error = ""
        if not self.available():
            return None
        attempted = None
        try:
            front = self.backend.front()
            if front is None or not allowed(front):
                return None
            self._attempt(front)
            attempted = front
            if getattr(self.backend,'supports_wechat_layout',False):
                self.backend.ocr_wechat_capture = {}
            text, elements, secure, complete = self.backend.accessible_text(front)
            method = "macos_accessibility"
            # A title and a few toolbar labels are not the conversation.
            # OCR is also useful where an app exposes its shell but not its
            # message bubbles. A short AX result is retained if OCR is empty.
            if (is_wechat_application(front.application) or len(text.strip()) < 40) and not secure and complete:
                current = self.backend.front()
                if current is None or current.identity != front.identity or not allowed(current):
                    self._result(front, 'skipped', reason='focus_or_privacy_changed')
                    return None
                try:
                    recognised = self.backend.ocr(front)
                except CaptureFocusChanged:
                    self._result(front, 'skipped', reason='focus_changed_before_capture')
                    return None
                except Exception as exc:
                    self.last_error = str(exc)[:250]
                    recognised = ""
                if recognised.strip():
                    text = recognised
                    method = getattr(self.backend, 'ocr_method', 'macos_vision_ocr')
            # An immutable image verified in front belongs to that original
            # window even if OCR finishes after the user switches away.
            # AX walks and unverified backends still need the focus recheck.
            snapshot = (method.endswith('_ocr') and
                        getattr(self.backend, 'ocr_snapshot_identity', None) == front.identity)
            current = front if snapshot else self.backend.front()
            if current is None or current.identity != front.identity or not allowed(current):
                self._result(front, 'skipped', reason='focus_or_privacy_changed')
                return None
            if not text.strip():
                if not self.last_error:
                    self.last_error = ("Secure field present; OCR skipped." if secure else
                                       "This window exposed no readable text.")
                self._result(front, 'blocked' if secure or not complete else 'failed',
                             error=self.last_error,
                             reason='secure_field' if secure else 'incomplete_scan' if not complete
                             else 'ocr_failed' if self.last_error.startswith('Local ') else 'no_readable_text')
                return None
            self.last_method = method
            if is_wechat_application(front.application) and getattr(self.backend,'supports_wechat_layout',False) and not getattr(self.backend,'ocr_wechat_capture',{}):
                self.backend.ocr_wechat_capture = {'schema_version':'1.0','source':'wechat_screen_ocr',
                    'layout_status':'unrecognized','layout_reason':'ocr_failed_or_unavailable',
                    'peer_name':None,'conversation_type':'unknown','messages':[],
                    'unresolved_text':text[:16000],'captured_at':datetime.now(timezone.utc).isoformat()}
            if method.endswith('_ocr'):
                self.ocr_readings += 1
            else:
                self.ax_readings += 1
            self._result(front, 'degraded' if self.last_error else 'ok', method=method,
                         error=self.last_error, reason='ocr_failed' if self.last_error else '')
            return WindowText(application=front.application, title=front.title,
                              pid=front.pid, handle=front.handle, text=text[:16000],
                              how=method, elements=elements,
                              milliseconds=(time.monotonic() - started) * 1000,
                              capture_scope='visible_window' if method.endswith('_ocr') else '',
                              wechat_capture=copy.deepcopy(getattr(self.backend, 'ocr_wechat_capture', {}))
                              if is_wechat_application(front.application) else {})
        except Exception as exc:
            self.last_error = str(exc)[:250]
            if attempted is not None:
                self._result(attempted, 'failed', error=self.last_error, reason='read_error')
            return None

    def status(self):
        with self._status_lock:
            applications = [dict(item) for item in self._application_reads.values()]
        return {"method": self.last_method, "accessibility_readings": self.ax_readings,
                "ocr_readings": self.ocr_readings, "last_error": self.last_error,
                'applications': applications,
                'ocr_image_cache_hits': getattr(self.backend, 'ocr_image_cache_hits', 0),
                'ocr_executions': getattr(self.backend, 'ocr_executions', 0)}



class NativeReader:
    supports_wechat_layout = True
    def __init__(self):
        import ApplicationServices as AX
        import Quartz as Q
        from AppKit import NSWorkspace
        self.ax, self.q = AX, Q
        self.workspace = NSWorkspace.sharedWorkspace()
        self._ocr_cache = OrderedDict()
        self.ocr_image_cache_hits = 0
        from .window_ocr import LocalWindowOcr
        self._ocr_engine = LocalWindowOcr()
        self.ocr_method = ''

    def attr(self, element, name):
        error, value = self.ax.AXUIElementCopyAttributeValue(element, name, None)
        return value if error == 0 else None

    def front(self):
        session = self.q.CGSessionCopyCurrentDictionary() or {}
        if session.get("CGSSessionScreenIsLocked", False):
            return None
        if not self.ax.AXIsProcessTrusted():
            raise RuntimeError("Allow Accessibility for the app running Python in System Settings > Privacy & Security, then restart the agent.")
        app = self.workspace.frontmostApplication()
        if app is None:
            return None
        pid = int(app.processIdentifier())
        root = self.ax.AXUIElementCreateApplication(pid)
        self.ax.AXUIElementSetMessagingTimeout(root, 0.2)
        window = self.attr(root, "AXFocusedWindow")
        if window is None:
            return None
        title = str(self.attr(window, "AXTitle") or "")
        bounds = self.rect(window)
        if bounds is None:
            return None
        infos = self.q.CGWindowListCopyWindowInfo(
            self.q.kCGWindowListOptionOnScreenOnly | self.q.kCGWindowListExcludeDesktopElements,
            self.q.kCGNullWindowID) or []
        for info in infos:
            if int(info.get("kCGWindowOwnerPID", -1)) != pid or info.get("kCGWindowLayer", 0) != 0:
                continue
            b = info.get("kCGWindowBounds", {})
            rect = tuple(float(b.get(k, 0)) for k in ("X", "Y", "Width", "Height"))
            if all(abs(a - b) < 3 for a, b in zip(rect, bounds)):
                return FrontWindow(str(app.localizedName() or ""), title, pid,
                                   int(info["kCGWindowNumber"]), rect, window)
        # Without a matching window id we cannot safely choose an OCR image.
        return None

    def rect(self, element):
        position, size = self.attr(element, "AXPosition"), self.attr(element, "AXSize")
        if position is None or size is None:
            return None
        ok_p, point = self.ax.AXValueGetValue(position, self.ax.kAXValueCGPointType, None)
        ok_s, extent = self.ax.AXValueGetValue(size, self.ax.kAXValueCGSizeType, None)
        if not ok_p or not ok_s:
            return None
        return float(point.x), float(point.y), float(extent.width), float(extent.height)

    @staticmethod
    def intersects(a, b):
        return (a[2] > 0 and a[3] > 0 and a[0] < b[0] + b[2]
                and b[0] < a[0] + a[2] and a[1] < b[1] + b[3]
                and b[1] < a[1] + a[3])

    def accessible_text(self, front):
        stack = [(front.element, 0)]
        seen, lines, secure = set(), [], False
        count, chars = 0, 0
        truncated = False
        deadline = time.monotonic() + 2.0
        while stack and count < 1200 and chars < 16000 and time.monotonic() < deadline:
            element, depth = stack.pop()
            count += 1
            role = str(self.attr(element, "AXRole") or "")
            subrole = str(self.attr(element, "AXSubrole") or "")
            if "Secure" in subrole or "Secure" in role:
                secure = True
                continue
            if self.attr(element, "AXHidden"):
                continue
            bounds = self.rect(element)
            if bounds is not None and not self.intersects(bounds, front.bounds):
                continue
            # Read content, not menu/button labels that masquerade as body
            # text and prevent the OCR fallback in custom-drawn chat apps.
            if role in ("AXStaticText", "AXTextField", "AXTextArea") and bounds is not None:
                value = None
                if role == "AXTextArea":
                    visible = self.attr(element, "AXVisibleCharacterRange")
                    if visible is not None:
                        error, value = self.ax.AXUIElementCopyParameterizedAttributeValue(
                            element, "AXStringForRange", visible, None)
                        if error:
                            value = None
                else:
                    value = self.attr(element, "AXValue")
                    if not isinstance(value, str):
                        value = self.attr(element, "AXTitle")
                if isinstance(value, str):
                    for line in value.splitlines():
                        line = line.strip()
                        if line and line not in seen:
                            seen.add(line)
                            lines.append(line)
                            chars += len(line)
            children = self.attr(element, "AXVisibleChildren")
            if children is None:
                children = self.attr(element, "AXChildren")
            if children:
                if depth >= 30:
                    truncated = True
                else:
                    children = list(children)
                    truncated = truncated or len(children) > 1200
                    stack.extend((child, depth + 1) for child in reversed(children[:1200]))
        return "\n".join(lines), count, secure, not stack and not truncated

    def ocr(self, front):
        self.ocr_snapshot_identity = None
        self.ocr_wechat_capture = {}
        if not self.q.CGPreflightScreenCaptureAccess():
            raise RuntimeError("Allow Screen Recording for the app running Python in System Settings > Privacy & Security, then restart the agent.")
        # Capture one identified window, never a desktop rectangle which
        # could contain another application's content after a focus change.
        current = self.front()
        if current is None or current.identity != front.identity:
            raise CaptureFocusChanged()
        picture = self.q.CGWindowListCreateImage(
            self.q.CGRectNull, self.q.kCGWindowListOptionIncludingWindow,
            front.handle, self.q.kCGWindowImageBoundsIgnoreFraming)
        if picture is None:
            raise RuntimeError("macOS could not capture the current window for OCR.")
        current = self.front()
        if current is None or current.identity != front.identity:
            raise CaptureFocusChanged()
        # Mark only a frame bounded by successful focus checks. No later
        # background-window capture or conversation relabeling is allowed.
        self.ocr_snapshot_identity = front.identity
        captured_at = datetime.now(timezone.utc).isoformat()
        # Keep only a fingerprint and recognized text, never the image. A
        # static window can reuse OCR while the listener still checks focus.
        fingerprint = None
        try:
            pixels = self.q.CGDataProviderCopyData(self.q.CGImageGetDataProvider(picture))
            fingerprint = hashlib.blake2b(bytes(pixels), digest_size=16).digest()
        except (AttributeError, TypeError, ValueError):
            pass
        cache = getattr(self, '_ocr_cache', None)
        if cache is None:
            cache = self._ocr_cache = OrderedDict()
        key = (front.identity, fingerprint)
        if fingerprint is not None and key in cache:
            self.ocr_image_cache_hits = getattr(self, 'ocr_image_cache_hits', 0) + 1
            text, self.ocr_method, metadata = cache[key]
            self.ocr_wechat_capture = copy.deepcopy(metadata)
            if self.ocr_wechat_capture:
                self.ocr_wechat_capture['captured_at'] = captured_at
            cache.move_to_end(key)
            return text
        # Serialize the identified window in memory; local OCR has a deadline.
        png = self._encode_png(picture)
        self.ocr_executions = getattr(self, 'ocr_executions', 0) + 1
        if is_wechat_application(front.application):
            from .wechat_layout import prepare_wechat_ocr_cut, shift_layout
            # Only where the words are is read: the name and the bubbles.
            cut, offset = prepare_wechat_ocr_cut(png)
            result = shift_layout(self._ocr_engine.read_layout(cut), offset)
            text = result['text']
            try:
                from .wechat_layout import parse_wechat_frame
                self.ocr_wechat_capture = parse_wechat_frame(png, result)
            except Exception:
                self.ocr_wechat_capture = {'schema_version': '1.0', 'source': 'wechat_screen_ocr',
                    'layout_status': 'unrecognized', 'layout_reason': 'layout_parse_failed',
                    'messages': [], 'unresolved_text': text, 'peer_name': None,
                    'frame_hash': hashlib.blake2b(png, digest_size=16).hexdigest()}
            self.ocr_wechat_capture['captured_at'] = captured_at
        else:
            text = self._ocr_engine.read(png)
        self.ocr_method = self._ocr_engine.method
        if fingerprint is not None and text.strip():
            # A blinking caret alternates between two identical frames.
            # Remember a few fingerprints/texts, so it does not force OCR
            # every second. The images themselves are never retained.
            cache[key] = (text, self.ocr_method, copy.deepcopy(self.ocr_wechat_capture))
            while len(cache) > 4:
                cache.popitem(last=False)
        return text

    def _encode_png(self, picture):
        from Foundation import NSMutableData
        data = NSMutableData.data()
        destination = self.q.CGImageDestinationCreateWithData(data, 'public.png', 1, None)
        if destination is None:
            raise RuntimeError('Local OCR could not encode the current window')
        self.q.CGImageDestinationAddImage(destination, picture, None)
        if not self.q.CGImageDestinationFinalize(destination):
            raise RuntimeError('Local OCR could not encode the current window')
        return bytes(data)

    def ocr_status(self):
        return self._ocr_engine.status()
