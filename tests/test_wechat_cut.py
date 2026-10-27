"""WeChat's picture cut to where its words are before it is read (Mac and
Linux): the recognizer reads a fifth of the window, and what it finds is put
back where it was in the window. The window is drawn by the test."""

import io
import unittest

from PIL import Image, ImageDraw

from test_wechat_structure import frame

from personalized_agent.platform_layer.wechat_layout import (parse_wechat_frame,
                                                             prepare_wechat_ocr_cut,
                                                             shift_layout)

#: Stand-ins for words: the name, and one in each of three bubbles.
WORDS = {"小陈": (330, 26, 70, 16), "你好": (375, 133, 50, 16), "好": (670, 213, 20, 16),
         "第一行": (375, 363, 65, 16)}


def with_words():
    png, _ocr = frame()
    picture = Image.open(io.BytesIO(png)).convert("RGB")
    draw = ImageDraw.Draw(picture)
    for x, y, w, h in WORDS.values():
        draw.rectangle((x, y, x + w - 1, y + h - 1), fill=(20, 20, 20))
    out = io.BytesIO()
    picture.save(out, format="PNG")
    return out.getvalue()


def read_blocks(png):
    """A stand-in recognizer: each dark block, in the picture it is given."""
    import numpy as np
    dark = np.asarray(Image.open(io.BytesIO(png)).convert("L")) < 128
    found, seen = [], np.zeros_like(dark)
    for y, x in zip(*np.where(dark)):
        if seen[y, x]:
            continue
        right = x
        while right + 1 < dark.shape[1] and dark[y, right + 1]:
            right += 1
        bottom = y
        while bottom + 1 < dark.shape[0] and dark[bottom + 1, x]:
            bottom += 1
        seen[y:bottom + 1, x:right + 1] = True
        found.append([int(x), int(y), int(right - x + 1), int(bottom - y + 1)])
    return found


class CutToItsWordsTests(unittest.TestCase):

    def test_a_fifth_of_the_window_read_and_every_word_put_back(self):
        png = with_words()
        cut, offset = prepare_wechat_ocr_cut(png)
        width, height = Image.open(io.BytesIO(cut)).size
        self.assertLess(width * height, .25 * 1000 * 700)
        boxes = read_blocks(cut)
        layout = shift_layout({"words": [{"text": "", "bbox": box} for box in boxes]}, offset)
        self.assertEqual(sorted(tuple(w["bbox"]) for w in layout["words"]),
                         sorted(WORDS.values()))

    def test_the_messages_the_same_as_from_the_whole_window(self):
        png = with_words()
        whole = {"words": [{"text": text, "bbox": list(box), "line": (i,), "confidence": 90}
                           for i, (text, box) in enumerate(WORDS.items())]}
        by_box = {box: text for text, box in WORDS.items()}
        cut, offset = prepare_wechat_ocr_cut(png)
        read = shift_layout({"words": [{"text": "", "bbox": box, "line": (i,), "confidence": 90}
                                       for i, box in enumerate(read_blocks(cut))]}, offset)
        for word in read["words"]:
            word["text"] = by_box[tuple(word["bbox"])]
        strip = lambda frame: [(m["text"], m["sender_role"]) for m in frame["messages"]]
        self.assertEqual(strip(parse_wechat_frame(png, read)), strip(parse_wechat_frame(png, whole)))
        self.assertEqual(parse_wechat_frame(png, read)["peer_name"], "小陈")

    def test_a_window_with_no_layout_is_given_whole(self):
        blank = io.BytesIO()
        Image.new("RGB", (800, 600), "white").save(blank, format="PNG")
        cut, offset = prepare_wechat_ocr_cut(blank.getvalue())
        self.assertEqual((cut, offset), (blank.getvalue(), (0, 0)))


if __name__ == "__main__":
    unittest.main()
