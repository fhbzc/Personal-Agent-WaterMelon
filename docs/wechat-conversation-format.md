# Local WeChat observations (schema 2.0)

Capture and organization use Apple Vision (accurate), Pillow, Numpy and fixed
rules, on the computer. No language model is called. A WeChat window in front
is looked at every 3 seconds and read when its picture changed, every 5 seconds
after 30 seconds unchanged, every 3 again at typing
(`listener/providers/fast_reading.py`); a reading is given up after 8 seconds.
Only the window in front is captured. The privacy checks, the excluded
applications and contacts, secure fields and password scrubbing apply before
anything is kept. Pictures stay in memory.

## Data layers

- Observation: a bounded, privacy-filtered reading, parser status, geometry and
  evidence. Unchanged readings update observation counts and last-seen time.
  A failed reading has no conversation or segment. It cannot invent a contact.
- Segment: a continuous observation of one readable chat. A chat-title change,
  conflicting direct-chat avatar, foreground visit change or gap over 90 seconds
  starts a new segment. A segment ID is independent of the conversation ID.
- Conversation: an anonymous local grouping. Reusing an earlier grouping needs
  a unique header plus avatar match (when a direct chat is established), or a
  unique same-header match with at least two distinct substantive overlapping
  messages (12 normalized characters combined, one at least 5 characters).
  Names or short replies alone cannot join separate visits. Ambiguity creates
  another grouping rather than silently joining people.
- Message: a stable local ID for a visible text bubble. It carries self / other /
  unknown, observed bounds, OCR confidence, source capture links, observation
  times and revision history. IDs are not WeChat native message IDs.

New conversations do not create person profiles. `peer.person_id` and
`connection_id` are null for new records. A header is only UI matching evidence;
no contact identification or Connection integration is required. Old records,
including old provisional person IDs and failed captures, are retained. Old
empty conversation candidates are hidden from the main list and remain in the
DB. Existing conversation aliases remain resolvable.

## Alignment and revisions

Ordered exact matching reuses IDs and preserves separately visible repeated
short messages. Revisited frames can reuse a richer prior observed window
with two distinct anchors. A repeated observation does not publish a new raw
event or wake downstream consumers.

Small OCR corrections need the same segment, substantially overlapping bubble
bounds, unchanged adjacent message anchors, high text similarity and improved
OCR confidence. Unknown sender attribution can be resolved with the same bounds
and adjacent anchors (or the same verified frame). An isolated similar sentence
at the same position is insufficient. Insufficient evidence creates a separate
candidate; it never overwrites an unrelated message.

A correction updates the canonical message and increments `revision`; immutable
`revisions` retain before/after evidence and the reason. Raw events distinguish
`wechat_messages` (new) from `wechat_message_updates` (revisions), using the same
message ID. A future consumer should upsert by message ID + revision, not append
every raw screen text as a new statement. Capture and organization perform no
emotion inference. Downstream consumers must apply their own quality checks.

## Read-only inspection

Authenticated routes, in debugger builds only:

- `GET /api/debugger/listener/wechat/conversations?limit=30` lists nonempty conversations,
  role counts, segment counts and recent observation diagnostics.
- `GET /api/debugger/listener/wechat/conversation?id=conv_...&limit=200` returns structured
  JSON with `conversation`, `segments`, `messages`, `captures` and `pagination`.
- Follow `pagination.next_before` with `before=` to load older messages.

Debugger → Listener shows anonymous conversation numbers, self/other counts,
segments and failures separately from thread health. It can inspect/download the
loaded JSON. Its conversation list is all-time; event filters do not apply.
Downloads contain loaded pages, not a guaranteed complete conversation archive.
Each response bounds evidence to 50 captures, 20 capture links and 20 recent
revisions per message, and 100 recent segments. Revision count and evidence
truncation flags remain explicit; the database retains the older evidence.

## Evidence limits

Roles describe the visible bubble container, inferred from layout, fill color
and optional avatar evidence. They do not prove authorship or delivery. Quotes,
forwarded cards, images, voice/video and group-member identity are not reliably
separated. They must not be interpreted as verified self-authored emotional
statements. Unclear roles stay unknown. A future emotion consumer must use these
provenance/uncertainty fields, not rely on the raw transcript alone.

Sidebar, toolbar and composer text are excluded from message extraction. If
input boundaries are missing, unassigned OCR text is discarded so an unsent
draft cannot become chat evidence. Captures are explicitly partial visible
text, not full chat history. Send times are unknown (`message_time: null`).
`first_seen_at` / `last_seen_at` are observation times. Message array order is
first discovery, not proven send order; capture message positions give the
visible order in each observation, including scrolling into older history.

Account IDs identify this local installation profile, not a verified signed-in
WeChat account. Account switching is not reliably detected. Names can change,
OCR can differ, and indistinguishable repeated screens cannot establish native
message identity. These conditions may leave separate anonymous groupings.

Schema migration only adds tables; no historical text is reinterpreted without
new evidence. Raw events, normalized rows and revisions share a transaction.
The existing raw-activity retention window applies to the new tables too; zero
keeps records indefinitely. The screen character budget still bounds a capture,
and truncation is explicit. No screenshots are added to this structured store.


## Stable cross-segment IDs and simplified-Chinese OCR (2026-10-22)

Recognition explicitly uses `chi_sim+eng`, even when other language files are
installed. Diagnostics distinguish installed `languages` from active
`recognition_languages`. Traditional Chinese data can stay installed without
participating in recognition.

A previously observed capture owns one message ID per position. Returning to
that capture reuses its position mapping rather than aligning it again against
message insertion order. A separate content fingerprint ignores OCR confidence
and folds Unicode variants (and, only where OpenCC happens to be installed - it
is no dependency - simplified and traditional characters); original
OCR text remains unchanged. A unique header plus a complete matching frame
with at least two distinct bounded bubbles can reconnect a segment without
identifying the contact. A lone short reply remains insufficient.

The database enforces unique `(capture_id, position)` associations. Migration
repairs historical collisions in one transaction, retaining every original
message, capture, observation and raw-event ID and payload. Identical duplicate
messages supported by the same capture position become aliases of the original
position owner. Messages proven to coexist as separate bubbles are never
aliased. Uncertain extra associations are archived for review rather than
merged. The JSON exposes `message_aliases`, `association_conflicts`, and each
message's `association_status`; message lists and role counts exclude alias
rows. Consumers can map old raw-event message IDs through these aliases.

Returning to an indistinguishable screen is still observation evidence, not
proof of a native WeChat message ID or of delivery. The existing unknown-time,
quote, media and completeness limitations still apply. No language model is
called for normalization, association, or historical repair.
