# Reading the window in front on a Mac

`platform_layer/macos_window_text.py` reads the window in front;
`platform_layer/window_ocr.py` reads its picture with Apple Vision.

Only the window in front is read - not windows behind, not every display, not
messages as they are sent. The checks that stop a reading before it starts:
the listener and screen text switches, presence, the excluded applications and
windows, secure fields, and an Accessibility walk that did not finish.

Accessibility is asked first. A window that gives fewer than forty characters
through it - WeChat gives only its buttons - is read from its picture instead.
Screen Recording permission is needed for the picture.

Recognition is Apple Vision's accurate mode, Chinese (simplified) and English,
with language correction; the picture stays in memory, and a reading that takes
longer than eight seconds is given up. Nothing is sent anywhere. The packages
are `pyobjc-framework-Vision` and `pyobjc-framework-Quartz`, installed on a Mac
with the requirements. Pictures and documents are read by the same Vision
(`platform_layer/ocr.py`, `MacVisionOcr`).

WeChat 4 gives Accessibility its message list (`chat_message_list`, one item a
message, bubbles marked `chat_bubble_item_view`): its words are taken from
there, and only whose each is from the window's picture - as on Windows
(`wechat_items.py`). Measured 2026-10-27 on a Mac Studio: five messages of five
placed right, about 60 ms. Only where no list is given is the picture read by
Vision, each line's box and confidence kept to tell the messages apart - see
[WeChat observations](wechat-conversation-format.md).

A window that leaves the front before or while its picture is taken is thrown
away. A picture taken while the window was in front may finish being read after
the user moves on, and is kept under that window's name; the privacy checks are
asked again before it is kept.

The last four pictures' fingerprints and readings are remembered, so the same
picture is not read twice.

`screen.text`'s `how` is `macos_accessibility`, `macos_wechat_messages` or
`macos_vision_ocr`.

How often: chats, mail and social media in front are looked at every 3 seconds
and read only when their picture changed (`listener/providers/fast_reading.py`,
on every platform); every other window at "How often to look". New WeChat
messages are stored at once.

The Listener debugger (debugger builds) shows each application's last reading,
when it last failed and why; another application's success does not hide
WeChat's failure. These are kept in memory and gone at a restart.

Not done: a black picture (Screen Recording refused) is reported as "no
readable text" and not told apart; there is no second try at low confidence.
A successful reading does not mean every message was recorded.
