"""A VIDEO, read for a model that cannot watch one.

None of the models the agent runs on takes a video as it is - the Claude API,
Claude Code, Codex and the OpenAI-compatible ones are given text and
pictures - so a video is read here, on this computer, into what they can
take:

  * FRAMES, evenly over the part asked for, each a picture saved as a file -
    with the words read in it (runtime/documents: the computer's own OCR);
  * WHAT IS SAID, with the time it was said: the speech recognised by
    faster-whisper, on the processor. Its model is fetched once, the first
    time a video's speech is read, and kept.

All of it is written INTO THE RUN'S OWN FOLDER - the only place a run may
write - under ``videos/<name>-<sha>/``: the frames, ``transcript.txt`` and
``reading.json``. A video read again is read from there.

A frame whose words hold a password or a code is not kept and not given to
a model (as a picture is not, runtime/documents); its words are, with it
hidden, and so is what is said.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

log = logging.getLogger(__name__)

VIDEOS = (".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".wmv", ".flv",
          ".mpg", ".mpeg", ".3gp", ".ts")
#: Frames taken unless asked, and the most - over the part asked for.
FRAMES = 12
MOST_FRAMES = 40
#: Of those, the most handed to a model as pictures in one answer.
PICTURES_GIVEN = 8
#: A frame's longest side, as kept.
FRAME_SIDE = 1280
#: The speech model: small enough for a processor, good in many languages.
SPEECH_MODEL = "small"
#: The most of a video whose speech is read in one call, in seconds: an hour
#: takes the processor many minutes. Further on is read with `start`.
MOST_SPEECH_SECONDS = 30 * 60
VERSION = 1

#: THE SPEECH MODEL IS LET GO once nothing has used it for this long - as
#: the embedding model is (heartbeat/states/embedding.py): held, it is
#: several hundred megabytes of the agent's memory for as long as it runs.
LINGER_SECONDS = 120.0

_model_lock = threading.Lock()
_model: Any = None
_holders = 0
_last_used = 0.0
_timer: Optional[threading.Timer] = None


def is_video(path: Any) -> bool:
    return Path(str(path)).suffix.lower() in VIDEOS


def _clock(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    return (f"{hours}:{rest // 60:02d}:{rest % 60:02d}" if hours
            else f"{rest // 60:02d}:{rest % 60:02d}")


def duration_of(path: Path) -> float:
    import av
    with av.open(str(path)) as container:
        if container.duration:
            return container.duration / 1_000_000
        stream = container.streams.video[0] if container.streams.video else None
        if stream is not None and stream.duration and stream.time_base:
            return float(stream.duration * stream.time_base)
    return 0.0


def frames_at(path: Path, moments: List[float], into: Path) -> List[Dict[str, Any]]:
    """The frame at (or just after) each moment, saved into `into`."""
    import av
    out: List[Dict[str, Any]] = []
    with av.open(str(path)) as container:
        if not container.streams.video:
            return out
        stream = container.streams.video[0]
        base = stream.time_base
        for at, moment in enumerate(moments, 1):
            try:
                container.seek(int(moment / base), stream=stream, any_frame=False,
                               backward=True)
                picture = None
                for frame in container.decode(stream):
                    if frame.time is None or frame.time >= moment - 0.05:
                        picture = frame.to_image()
                        break
                if picture is None:
                    continue
            except Exception:
                log.debug("no frame at %.1fs of %s", moment, path.name, exc_info=True)
                continue
            picture.thumbnail((FRAME_SIDE, FRAME_SIDE))
            target = into / f"frame_{at:03d}_{_clock(moment).replace(':', '-')}.jpg"
            picture.convert("RGB").save(target, format="JPEG", quality=85)
            out.append({"at": _clock(moment), "seconds": round(moment, 1),
                        "file": str(target)})
    return out


def _threads() -> int:
    """HALF THE PROCESSOR, at most: it runs in the agent's own process, beside
    the page, the heartbeats and the work itself."""
    return max(1, (os.cpu_count() or 2) // 2)


def _speech_model() -> Any:
    """The model, loaded when first wanted, and held while it is used."""
    global _model, _holders
    with _model_lock:
        if _model is None:
            from faster_whisper import WhisperModel
            _model = WhisperModel(SPEECH_MODEL, device="cpu", compute_type="int8",
                                  cpu_threads=_threads())
        _holders += 1
        return _model


def _done_with_model() -> None:
    """One use over: let go of it once nothing has used it for LINGER_SECONDS."""
    global _holders, _last_used, _timer
    with _model_lock:
        _holders = max(0, _holders - 1)
        _last_used = time.monotonic()
        if _timer is not None:
            _timer.cancel()
        _timer = threading.Timer(LINGER_SECONDS, _release_if_idle)
        _timer.daemon = True
        _timer.start()


def _release_if_idle() -> None:
    global _model, _timer
    with _model_lock:
        if _model is None or _holders or time.monotonic() - _last_used < LINGER_SECONDS - 0.05:
            return
        _model = None
        _timer = None
    import gc
    gc.collect()
    log.info("the speech model was let go: unused for %.0fs", LINGER_SECONDS)


def model_loaded() -> bool:
    return _model is not None


#: The sound the speech model hears: one channel, 16,000 samples a second.
RATE = 16000


def sound_of(path: Path, start: float, end: float) -> Any:
    """The sound from `start` to `end`, as the speech model takes it.

    Decoded here rather than by faster-whisper, which opens the file with an
    argument newer versions of PyAV no longer take."""
    import av
    import numpy as np
    parts: List[Any] = []
    with av.open(str(path)) as container:
        if not container.streams.audio:
            return np.zeros(0, dtype=np.float32)
        stream = container.streams.audio[0]
        if start:
            container.seek(int(start * 1_000_000), any_frame=False, backward=True)
        resampler = av.AudioResampler(format="s16", layout="mono", rate=RATE)
        first: Optional[float] = None
        for frame in container.decode(stream):
            if first is None:
                first = float(frame.time or 0.0)
            if frame.time is not None and frame.time > end:
                break
            for out in resampler.resample(frame):
                parts.append(out.to_ndarray().reshape(-1))
        for out in resampler.resample(None):
            parts.append(out.to_ndarray().reshape(-1))
    if not parts:
        return np.zeros(0, dtype=np.float32)
    sound = np.concatenate(parts).astype(np.float32) / 32768.0
    # From where decoding began (a seek lands a little before) to the end.
    skip = max(0, int(round((start - (first or 0.0)) * RATE)))
    return sound[skip:skip + int(max(0.0, end - start) * RATE)]


def speech_of(path: Path, start: float, end: float,
              stopped: Callable[[], bool] = lambda: False) -> Dict[str, Any]:
    """What is said between `start` and `end`, each line with its time."""
    sound = sound_of(path, start, end)
    if not len(sound):
        return {"language": "", "lines": []}
    model = _speech_model()
    try:
        segments, info = model.transcribe(sound, vad_filter=True)
        lines: List[str] = []
        # The segments are worked out as they are read: the model is in use
        # until the last of them.
        for segment in segments:
            if stopped():
                break
            lines.append(f"[{_clock(start + segment.start)}] {segment.text.strip()}")
    finally:
        _done_with_model()
    return {"language": getattr(info, "language", "") or "", "lines": lines}


def read_video(path: Any, folder: Path, start: float = 0.0, end: Optional[float] = None,
               frames: int = FRAMES, speech: bool = True, hiding: bool = True,
               stopped: Callable[[], bool] = lambda: False) -> Dict[str, Any]:
    """Frames and speech of `path` from `start` to `end` (seconds), written
    under `folder` (the run's own); what a model is given of it."""
    from . import documents
    path = Path(str(path))
    sha = documents.sha256_of(path)
    total = duration_of(path)
    end = min(float(end), total) if end is not None and total else (end or total)
    start = max(0.0, min(float(start or 0), end or 0.0))
    frames = max(1, min(int(frames or FRAMES), MOST_FRAMES))
    home = folder / "videos" / f"{path.stem[:40]}-{sha[:10]}"
    home.mkdir(parents=True, exist_ok=True)
    key = f"{start:.1f}-{end:.1f}-{frames}-{int(bool(speech))}"
    kept_at = home / "reading.json"
    try:
        kept = json.loads(kept_at.read_text(encoding="utf-8"))
        if kept.get("version") == VERSION and key in kept.get("parts", {}):
            return dict(kept["parts"][key], from_copy=True)
    except (OSError, ValueError):
        kept = {}
    span = max(0.0, end - start)
    moments = [start + span * (n + 0.5) / frames for n in range(frames)] if span else [0.0]
    taken = frames_at(path, moments, home)
    from ..common.credentials_guard import hide_credentials
    for one in taken:
        words = documents.ocr_file(one["file"])
        if hiding and words and hide_credentials(words) != words:
            # The words with it hidden; the picture, which shows it, gone.
            one["words"] = hide_credentials(words)
            one["picture_held_back"] = "it shows a password or a code"
            Path(one["file"]).unlink(missing_ok=True)
            one["file"] = ""
        else:
            one["words"] = words
    said: Dict[str, Any] = {"path": str(path), "kind": "video", "sha256": sha,
                            "duration": _clock(total), "duration_seconds": round(total, 1),
                            "from": _clock(start), "to": _clock(end),
                            "folder": str(home), "frames": taken}
    if speech:
        until = min(end, start + MOST_SPEECH_SECONDS)
        try:
            heard = speech_of(path, start, until, stopped)
            text = "\n".join(heard["lines"])
            said["speech_language"] = heard["language"]
            said["transcript"] = hide_credentials(text) if hiding else text
            if until < end:
                said["speech_cut_at"] = _clock(until)
            (home / "transcript.txt").write_text(said["transcript"], encoding="utf-8")
        except ImportError:
            said["speech"] = "not read: speech recognition is not installed with this copy"
        except Exception as exc:
            log.info("the speech of %s could not be read", path.name, exc_info=True)
            said["speech"] = f"not read: {exc}"[:300]
    kept.setdefault("parts", {})[key] = said
    kept["version"] = VERSION
    kept_at.write_text(json.dumps(kept, ensure_ascii=False, indent=1), encoding="utf-8")
    return dict(said, from_copy=False)


def for_model(said: Dict[str, Any], most: int = 40000) -> Dict[str, Any]:
    """The reading as a tool's answer: the text, and some frames as pictures
    (under "_pictures", models/vision.py) - every one kept as a file in the
    folder, to open by its path."""
    from . import documents
    lines = [f"VIDEO {Path(said['path']).name}, {said['duration']} long; "
             f"from {said['from']} to {said['to']}. Kept in {said['folder']}."]
    for one in said.get("frames") or []:
        where = Path(one["file"]).name if one.get("file") else "(not kept: "\
            + one.get("picture_held_back", "") + ")"
        words = " ".join(str(one.get("words") or "").split())[:600]
        lines.append(f"FRAME at {one['at']} - {where}" + (f": {words}" if words else ""))
    if said.get("transcript") is not None:
        lines.append(f"WHAT IS SAID ({said.get('speech_language') or 'language unknown'}):")
        lines.append(said["transcript"] or "(nothing is said)")
        if said.get("speech_cut_at"):
            lines.append(f"(speech read up to {said['speech_cut_at']}: read on with start)")
    elif said.get("speech"):
        lines.append(f"Speech {said['speech']}")
    text = "\n".join(lines)
    out = {key: said[key] for key in ("path", "kind", "duration", "from", "to", "folder",
                                      "from_copy") if key in said}
    out.update({"text": text[:most], "cut": len(text) > most})
    shown = [one for one in said.get("frames") or [] if one.get("file")]
    if len(shown) > PICTURES_GIVEN:
        step = len(shown) / PICTURES_GIVEN
        shown = [shown[int(n * step)] for n in range(PICTURES_GIVEN)]
    pictures = [documents.picture_for_model(one["file"]) for one in shown]
    pictures = [one for one in pictures if one is not None]
    if pictures:
        out["_pictures"] = pictures
    return out


__all__ = ["VIDEOS", "is_video", "read_video", "for_model", "duration_of"]
