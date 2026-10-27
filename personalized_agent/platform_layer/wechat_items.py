"""WeChat's messages one by one, from what its window says of itself (Windows).

WeChat 4 on Windows gives UI Automation its message list: one item a message,
named with the message's exact text, placed by its height on the screen - but
every item spans the whole list, so it does not say whose it is. The window's
picture says that: the bubble in the item's band of the picture lies on the
right for the user's own message, green, and on the left for the other
person's (measured on the user's machine, 2026-10-26: Weixin 4 on Qt 5.15, the
message list and the conversation list each a List of ListItems, no
descendants). So the words come from Automation - exact, no recognition - and
only the side from the picture.

The result is the same frame `wechat_layout.parse_wechat_frame` makes from a
Mac's recognized picture, so `data_manage/storage/wechat_store.py` keeps
each message once however it was read. Only the list's items are read:
whatever is being typed in the box below it is never a message.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, List, Optional, Sequence

from .wechat_layout import _avatar, _background

#: Labels at the top of the window that are not a conversation's name.
NOT_A_NAME = ("weixin", "wechat", "微信", "...", "…")
#: Pages of WeChat that are not a conversation.
NOT_A_CHAT = ("discover", "moments", "contacts", "search", "发现", "朋友圈", "通讯录", "搜索")


def _inside(box: Sequence[float], size) -> List[int]:
    x, y, w, h = box
    width, height = size
    left, top = max(0, int(x)), max(0, int(y))
    right, bottom = min(width, int(x + w)), min(height, int(y + h))
    return [left, top, max(0, right - left), max(0, bottom - top)]


def _scaled(box: Sequence[float], scale: float) -> List[float]:
    return [v * scale for v in box]


def _fill(picture: Any, region: Sequence[int], background) -> Any:
    """Where the chat region holds a bubble's flat fill: green, or a light
    grey that is not the background (dark mode: lighter than it). The same
    test as `wechat_layout._bubble_regions`, done at once for the region."""
    import numpy as np
    left, top, right, bottom = region
    rgb = np.asarray(picture, dtype=np.int16)[top:bottom, left:right]
    neutral = rgb.max(axis=2) - rgb.min(axis=2) <= 8
    green = (rgb[:, :, 1] > rgb[:, :, 0] + 20) & (rgb[:, :, 1] > rgb[:, :, 2] + 20)
    distance = np.max(np.abs(rgb - np.array(background)), axis=2)
    light = rgb.mean(axis=2) > 145 if sum(background) > 360 else rgb.mean(axis=2) > 42
    return rgb, ((neutral & light) | green) & (distance >= 6)


def _block(mask: Any, a: int, b: int) -> Optional[tuple]:
    """In rows a to b of a mask: the rows something fills, then the widest
    block of columns mostly filled there - and the rows that block fills.
    (first, last, top, bottom), relative to the mask; None for nothing."""
    import numpy as np
    band = mask[a:b]
    if band.shape[0] < 6:
        return None
    # The rows the bubble is in, not the whole band: an item's box can be
    # far taller than its bubble - the last one reaches the list's bottom
    # (measured live, 2026-10-26: a long message's columns diluted by that).
    filled = np.where(band.sum(axis=1) >= 18)[0]
    if filled.size < 6:
        return None
    a, b = a + int(filled[0]), a + int(filled[-1]) + 1
    band = mask[a:b]
    columns = np.where(band.sum(axis=0) >= max(4, .3 * band.shape[0]))[0]
    if columns.size < 18:
        return None
    # The bubble is the widest block of filled columns: the avatar beside
    # it, or a scrollbar at the list's edge, is a block of its own.
    blocks = np.split(columns, np.where(np.diff(columns) > 3)[0] + 1)
    # A row lit across the list - WeChat's under the pointer - is no bubble.
    blocks = [one for one in blocks if one.size < .85 * mask.shape[1]]
    if not blocks:
        return None
    widest = max(blocks, key=len)
    if widest.size < 18:
        return None
    first, last = int(widest[0]), int(widest[-1]) + 1
    rows = np.where(band[:, first:last].sum(axis=1) >= max(4, .3 * (last - first)))[0]
    if rows.size < 6:
        return None
    return first, last, a + int(rows[0]), a + int(rows[-1]) + 1


def _bubble_in(fill: Any, region: Sequence[int], low: int, high: int) -> Optional[tuple]:
    """The bubble in one message's band, with its colour. Its band is known
    from Automation, so no search for where bubbles are is needed.

    GREEN FIRST: only the user's own bubbles are green, and a green block
    in the band is theirs whatever else the band holds - measured live
    (2026-10-26): the row under the pointer, lit grey across the list, was
    taken for the bubble and the green one beside it missed."""
    import numpy as np
    rgb, mask = fill
    left, top = region[0], region[1]
    a, b = max(0, low - top), max(0, high - top)
    if b - a < 6:
        return None
    window = rgb[a:b]
    green = np.zeros_like(mask)
    green[a:b] = ((window[:, :, 1] > window[:, :, 0] + 20)
                  & (window[:, :, 1] > window[:, :, 2] + 20))
    found = _block(green, a, b) or _block(mask, a, b)
    if found is None:
        return None
    first, last, upper, lower = found
    inside = mask[upper:lower, first:last] | green[upper:lower, first:last]
    colours = rgb[upper:lower, first:last][inside]
    colour = tuple(int(v) for v in np.median(colours, axis=0))
    return (first + left, upper + top, last + left, lower + top, colour)


def _edge_avatar(picture: Any, fill: Any, region: Sequence[int], low: int, high: int,
                 side: str, extent: int, background) -> Optional[str]:
    """The avatar at one edge of a message's band: the first block of
    pixels unlike the background from that edge inward, if it is square -
    so a photograph sent beside it is not taken for part of it."""
    import numpy as np
    rgb, _mask = fill
    left, top = region[0], region[1]
    a, b = max(0, low - top), max(0, high - top)
    band = rgb[a:b]
    if band.shape[0] < 12:
        return None
    edge = band[:, :extent] if side == "left" else band[:, -extent:]
    unlike = np.max(np.abs(edge - np.array(background)), axis=2) > 24
    columns = unlike.sum(axis=0) >= 12
    order = range(columns.size) if side == "left" else range(columns.size - 1, -1, -1)
    run = []
    for x in order:
        if columns[x]:
            run.append(x)
        elif run and len(run) >= 12:
            break
        elif run:
            run = []
    if len(run) < 12:
        return None
    first, last = min(run), max(run) + 1
    rows = np.where(unlike[:, first:last].sum(axis=1) >= max(6, .4 * (last - first)))[0]
    if rows.size < 12:
        return None
    if not .7 <= (last - first) / max(1, rows[-1] + 1 - rows[0]) <= 1.4:
        return None
    x0 = first + left if side == "left" else region[2] - extent + first
    return _avatar(picture, [x0, a + top + int(rows[0]), last - first, int(rows[-1]) + 1 - int(rows[0])])


def parse_wechat_items(image: Any, chat: Sequence[float], items: List[Dict[str, Any]],
                       headers: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The frame of the conversation in front.

    `image` is the window's picture; `chat` the message list's box, and each
    of `items` (the list's items: `text`, `box`) and `headers` (the labels
    above the list: `text`, `box`) in that picture's pixels, x, y, width,
    height.
    """
    from PIL import Image
    picture = image.convert("RGB")
    width, height = picture.size
    scale = min(1.0, 1100 / width, 950 / height)
    if scale < 1:
        picture = picture.resize((round(width * scale), round(height * scale)),
                                 Image.Resampling.BILINEAR)
    w, h = picture.size
    result: Dict[str, Any] = {
        "schema_version": "2.0", "source": "wechat_accessibility",
        "parser_version": "wechat_accessibility_layout_v1",
        "frame_hash": hashlib.blake2b(
            ("\n".join(str(item.get("text") or "") for item in items)).encode("utf-8")
            + picture.tobytes()[::97], digest_size=16).hexdigest(),
        "layout_status": "unrecognized", "layout_reason": "message_list_not_found",
        "peer_name": None, "peer_avatar": None, "self_avatar": None,
        "conversation_type": "unknown", "messages": [],
        "capture_scope": "visible_text_only", "completeness_status": "partial",
        "unresolved_text": "", "diagnostics": {"list_items": len(items), "bubble_candidates": 0}}
    left, top, list_width, list_height = _inside(_scaled(chat, scale), (w, h))
    right, bottom = left + list_width, top + list_height
    if list_width < 160 or list_height < 60:
        return result
    names = []
    for header in headers:
        x, y, hw, hh = _scaled(header.get("box") or (0, 0, 0, 0), scale)
        said = str(header.get("text") or "").strip()
        if (said and said.casefold() not in NOT_A_NAME and left - 2 <= x + hw / 2 <= right
                and y + hh <= top + 2):
            names.append((y, x, said))
    if not names:
        result["layout_reason"] = "chat_header_not_readable"
        return result
    name = sorted(names)[0][2][:200]
    result["peer_name"] = name
    if name.casefold() in NOT_A_CHAT:
        result.update(layout_status="not_chat", layout_reason="non_chat_page")
        return result
    result["conversation_type"] = "group" if re.search(r"[（(]\s*\d+\s*[)）]\s*$", name) else "unknown"
    result.update(layout_status="recognized", layout_reason="")
    result["regions"] = {"chat": [left / w, top / h, list_width / w, list_height / h]}
    region = (left + 3, top + 3, right - 3, bottom - 3)
    background = _background(picture, region)
    fill = _fill(picture, region, background)
    extent = min(90, int(list_width * .14))
    unresolved = []
    for item in sorted(items, key=lambda one: (one.get("box") or (0, 0))[1]):
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        x, y, iw, ih = _scaled(item.get("box") or (0, 0, 0, 0), scale)
        low, high = max(top, y), min(bottom, y + ih)
        if high - low < 6:
            continue                     # scrolled out of the list's sight
        inside = _bubble_in(fill, region, int(low), int(high))
        other_avatar = _edge_avatar(picture, fill, region, int(low), int(high), "left", extent, background)
        self_avatar = _edge_avatar(picture, fill, region, int(low), int(high), "right", extent, background)
        role, basis, box = "unknown", "unresolved_layout", [left, low, right, high]
        if inside:
            result["diagnostics"]["bubble_candidates"] += 1
            a, b, c, d, color = inside
            box = [a, b, c, d]
            near_left = a - left < .2 * list_width
            near_right = right - c < .2 * list_width
            green = color[1] > color[0] + 20 and color[1] > color[2] + 20
            if near_right and (green or self_avatar):
                role, basis = "self", "right_bubble"
            elif near_left and not green and (not near_right or other_avatar):
                role, basis = "other", "left_anchored_flat_bubble"
        elif self_avatar and not other_avatar:
            # A picture, a file, a sticker: no flat bubble, but the avatar.
            role, basis = "self", "right_avatar"
        elif other_avatar and not self_avatar:
            role, basis = "other", "left_avatar"
        else:
            # A time, or a notice across the middle: not anyone's message.
            unresolved.append(text)
            continue
        if role == "other" and other_avatar:
            result["peer_avatar"] = result["peer_avatar"] or other_avatar
        if role == "self" and self_avatar:
            result["self_avatar"] = result["self_avatar"] or self_avatar
        a, b, c, d = box
        result["messages"].append({
            "text": text[:16000], "sender_role": role, "sender_basis": basis,
            "attribution_status": "inferred_from_ui" if role != "unknown" else "unresolved",
            "content_status": "visible_chat_bubble", "message_time": None,
            "bbox": [round(a / w, 5), round(b / h, 5), round((c - a) / w, 5), round((d - b) / h, 5)],
            # The words are WeChat's own, not recognized from a picture.
            "ocr_confidence": 100.0, "text_source": "accessibility"})
    result["unresolved_text"] = "\n".join(unresolved)[:16000]
    if not result["messages"]:
        result.update(layout_status="unrecognized", layout_reason="no_readable_chat_bubbles")
    result["diagnostics"]["assigned_messages"] = len(result["messages"])
    result["diagnostics"]["unknown_senders"] = sum(
        m["sender_role"] == "unknown" for m in result["messages"])
    return result

