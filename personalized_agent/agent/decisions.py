"""WHAT THE AGENT DID, IN SMALL GREY WORDS; WHAT IT ASKS, SAID ON ITS FOLD (the user, 2026-11-09:
"任何agent做的动作都用灰色小字显示（比如add calender什么的)。这些显示都是放在主对话框里的，不会被
collapse"; and then "每个问题要用按钮回复的设定取消。more details如果有question to answer, 加一个提示
'questions to answer' 就行").

A question asked in the full agent's folded answer was never read, and a "1" sent before it was asked
was taken as its yes. Now:

- WHAT WAS DONE - put on the calendar, added to the to-do list, remembered, handed over as work - is
  said under the reply in small grey words, one line each, from the calls that changed something.
- AN ANSWER THAT ASKS the user something begins with QUESTIONS (agent/two_speed.py), and its fold's
  line says "Questions to answer", so that it is opened.
- A message that does not plainly answer a question is never its yes (the chat's rules).
"""

from __future__ import annotations

from typing import Any, Dict, List, Sequence

#: What a full answer that asks the user something begins with, taken off before it is shown.
QUESTIONS = "[QUESTIONS]"

#: Said in the chat's own rules.
RULES = """
Asking:
- A question that waits on the user's answer - put this on the calendar? send
  it? which of these? - is asked plainly, and nothing is done about it until
  they answer it.
- A message that does not plainly answer it - "1", "ok" with nothing to tie it
  to it, or one written before you asked - is never a yes."""


# -- what the agent did -----------------------------------------------------------------------

#: The calls that change something, and how each is said. The words of the call that say what
#: it was about, in order: the first there is used.
DONE_AS = {
    "schedule_task": ("Put on your calendar", ("title",)),
    "cancel_scheduled_task": ("Taken off your calendar", ("title", "task_id")),
    "notify_user": ("Notified you", ("title", "message")),
    "todo_add": ("Added to your to-do list", ("text",)),
    "todo_suggest_done": ("Suggested a to-do is done", ("why", "id")),
    "todo_suggest_remove": ("Suggested taking a to-do off", ("why", "id")),
    "assign_work": ("Handed over as work", ("title", "instruction")),
    "make_special_issue": ("Made a special issue", ("title", "requirements")),
    "lesson_add": ("Remembered", ("rule", "user_said", "about")),
    "monitor_add": ("Monitor rule added", ("name", "about", "why")),
    "monitor_edit": ("Monitor rule changed", ("name", "id", "why")),
    "monitor_delete": ("Monitor rule deleted", ("name", "id", "why")),
    "monitor_quiet_add": ("Blocker added", ("name", "why")),
    "monitor_quiet_remove": ("Blocker removed", ("id", "why")),
    "write_file": ("Wrote a file", ("path",)),
    "society_send": ("Sent on Agent Society", ("text", "id")),
    "society_write_draft": ("Drafted on Agent Society", ("text", "id")),
}
ABOUT_CHARS = 110


def said_actions(calls: Sequence[Dict[str, Any]], words: Any = None) -> List[str]:
    """One line for each call that changed something and was not refused, as it is shown -
    its words in the user's language by `words` (common/interface_words.shown)."""
    said = words if callable(words) else (lambda text: text)
    out: List[str] = []
    for call in calls or []:
        name = str(call.get("tool") or "")
        if name not in DONE_AS or call.get("ok") is False:
            continue
        verb, keys = DONE_AS[name]
        verb = said(verb)
        args = call.get("arguments") if isinstance(call.get("arguments"), dict) else {}
        about = next((" ".join(str(args[key]).split()) for key in keys
                      if str(args.get(key) or "").strip()), "")
        if len(about) > ABOUT_CHARS:
            about = about[:ABOUT_CHARS - 1].rstrip() + "…"
        if name == "schedule_task":
            when = " ".join(str(args.get("at_local") or args.get("when") or "").split())
            quiet = str(args.get("notify") or "") != "notify" or args.get("event") == "explore"
            about += (f", {when}" if when else "") + " ({0})".format(
                said("no notification" if quiet else "with a notification"))
        line = f"{verb}: {about}" if about else verb
        if line not in out:
            out.append(line)
    return out


__all__ = ["QUESTIONS", "RULES", "DONE_AS", "said_actions"]
