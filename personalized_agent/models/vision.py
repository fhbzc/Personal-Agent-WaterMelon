"""WHICH MODELS CAN SEE A PICTURE - and a tool's answer with pictures in it,
for one that can and one that cannot.

A tool that read a picture (runtime/documents.py) answers with its words
read here - always - and the picture itself under "_pictures". The answer is
made into content blocks (`tool_content`): its text, then each picture as an
image block. A provider for a model that sees pictures sends them; one for a
model that does not leaves them out - the words are already in the text
(`without_pictures`).

Whether a model sees pictures is known from its name, and the user can say
otherwise for any model (the setting `model_sees_images`: name -> yes/no).
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

#: Names of models that see pictures.
_SEES = re.compile(
    r"(?i)(claude|gpt-4o|gpt-4\.1|gpt-4-turbo|gpt-4-vision|gpt-5|chatgpt-4o|\bo1\b|\bo3|\bo4|"
    r"codex|gemini|vision|\bvl\b|-vl|vl-|qwen.*vl|qvq|llava|pixtral|glm-4v|glm-4\.\dv|"
    r"minicpm-v|internvl|deepseek-vl|grok-.*vision|grok-4|llama-3\.2-.*vision|llama-4|"
    r"moondream|bakllava|gemma-3|mistral-small-3\.[12]|kimi-.*vl|doubao.*vision)")
#: ... and names that, though they match, do not.
_BLIND = re.compile(r"(?i)(gpt-4o-audio|gpt-4o-.*transcribe|embedding|tts|whisper)")


def sees_images(provider: str, model: str, config: Any = None) -> bool:
    """Whether a model can be given a picture."""
    said = {}
    if config is not None:
        said = config.get("model_sees_images", {}) or {}
        if isinstance(said, str):
            try:
                said = json.loads(said)
            except ValueError:
                said = {}
    for key in (f"{provider}/{model}", str(model)):
        if key in said:
            return bool(said[key])
    if provider in ("anthropic", "claude_code", "codex"):
        return True
    if provider == "offline":
        return False
    name = str(model or "")
    return bool(_SEES.search(name)) and not _BLIND.search(name)


def tool_content(said: Any) -> Any:
    """A tool's answer as a tool_result's content: plain text - or, when it
    holds pictures, its text and then each picture as an image block."""
    if isinstance(said, dict) and said.get("_pictures"):
        pictures = said["_pictures"]
        text = json.dumps({key: value for key, value in said.items() if key != "_pictures"},
                          ensure_ascii=False, default=str)[:40000]
        return [{"type": "text", "text": text}, *[
            {"type": "image", "source": {"type": "base64", "media_type": one["media_type"],
                                         "data": one["data"]}}
            for one in pictures if isinstance(one, dict) and one.get("data")]]
    return json.dumps(said, ensure_ascii=False, default=str)[:40000] \
        if not isinstance(said, str) else said


NOT_SHOWN = ("(A picture is not shown to this model, which reads text only: the words "
             "read in it are above.)")


def without_pictures(content: Any, note: str = NOT_SHOWN) -> Any:
    """Content with every picture taken out - each said where it was, its
    words being in the text already. Into tool results too."""
    if not isinstance(content, list):
        return content
    out: List[Any] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "image":
            out.append({"type": "text", "text": note})
        elif isinstance(block, dict) and block.get("type") == "tool_result" \
                and isinstance(block.get("content"), list):
            out.append(dict(block, content=without_pictures(block["content"], note)))
        else:
            out.append(block)
    return out


def text_of(content: Any) -> str:
    """Content as text alone: its text blocks joined, a picture said where
    it was."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return json.dumps(content, ensure_ascii=False, default=str)
    parts = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text") or ""))
        elif isinstance(block, dict) and block.get("type") == "image":
            parts.append("[a picture]")
        else:
            parts.append(json.dumps(block, ensure_ascii=False, default=str))
    return "\n".join(parts)


def pictures_in(messages: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Every picture in a conversation, in order - in tool results too."""
    found: List[Dict[str, str]] = []

    def walk(content: Any) -> None:
        if not isinstance(content, list):
            return
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "image":
                source = block.get("source") or {}
                if source.get("type") == "base64" and source.get("data"):
                    found.append({"media_type": str(source.get("media_type") or "image/png"),
                                  "data": str(source["data"])})
            elif block.get("type") == "tool_result":
                walk(block.get("content"))

    for message in messages or []:
        walk(message.get("content"))
    return found


__all__ = ["sees_images", "tool_content", "without_pictures", "text_of", "pictures_in",
           "NOT_SHOWN"]
