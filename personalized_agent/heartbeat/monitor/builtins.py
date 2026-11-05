"""THE BUILT-IN ACTIVATORS, written once into the Monitor (activators.seed).

- the two that acted on the screen at once before the Monitor - a meeting
  invitation, a time being agreed (heartbeat/core/triggers.py's rules, their
  patterns as they were) - the notification step on that window;
- SPAM AND SCAM RISK on pages and in mail (the user, 2026-11-03: "default的有
  spam risk detection ... 要用keyword based detection"): a high score said
  at once, a middling one judged at the next heartbeat (step_monitor);
- the moments to recommend (PR #6's): the day begun, back from an absence,
  new work, a project come back to after days, the work scattered, a rest;
- a break after a long stretch, and the evening report after a long day (by
  the hours worked and the time - not by windows being closed).

Those three - spam and the two calendar ones - are PROTECTED from the start:
the model may change none of them until the user lifts it. The model may
edit or delete the rest; it is told to delete only when it must.
"""

from __future__ import annotations

from typing import Any, Dict, List

from ..core.triggers import RULES

_RULE = {rule.name: rule for rule in RULES}

#: THE RULES OF QUIET THERE ARE FROM THE START (the user, 2026-11-04): nothing the
#: Monitor says of its own accord over slides being shown or in a meeting.
QUIET = (("presenting", "While presenting", {"signal": "presenting", "op": "==", "value": True}),
         ("meeting", "In a meeting", {"signal": "in_meeting", "op": "==", "value": True}))


def _calendar(name: str, label: str) -> Dict[str, Any]:
    rule = _RULE[name]
    parts = []
    if rule.title is not None:
        parts.append({"pattern": rule.title.pattern, "in": "title"})
    if rule.text is not None:
        parts.append({"pattern": rule.text.pattern, "in": "text"})
    return {"builtin": name, "protected": True, "name": label, "group": "calendar",
            "purpose": rule.why,
            "when": parts[0] if len(parts) == 1 else {"any": parts},
            "then": [{"do": "ask_model", "handler": "calendar"}],
            "cooldown_minutes": 10, "most_per_day": 50}


#: SPAM AND SCAM, in every display language: the words and their weights. A
#: word that is also ordinary ("urgent", "перевод" - a translation too) weighs
#: little; it takes several together to reach a score.
SPAM_WORDS: Dict[str, float] = {
    # English
    "verify your account": 3, "account suspended": 3, "your account has been locked": 3,
    "unusual sign-in activity": 2, "confirm your identity": 2, "password expired": 3,
    "you have won": 4, "claim your prize": 4, "lottery": 3, "inheritance": 2,
    "gift card": 3, "wire transfer": 2, "bitcoin": 1, "act now": 1, "urgent": 1,
    "click here to unlock": 3, "limited time offer": 1, "final notice": 2,
    "refund pending": 2, "tax refund": 2, "your package could not be delivered": 3,
    # Chinese
    "账户异常": 3, "账号异常": 3, "立即验证": 3, "冻结": 2, "中奖": 4, "领取奖品": 4,
    "礼品卡": 3, "转账": 2, "安全账户": 3, "刷单": 4, "退款": 1, "紧急": 1,
    "包裹无法投递": 3, "点击链接": 2,
    # Japanese
    "アカウントが停止": 3, "アカウントがロック": 3, "本人確認": 2, "当選": 4,
    "ギフトカード": 3, "至急": 1, "振込": 2, "パスワードの有効期限": 3,
    # Korean
    "계정이 정지": 3, "계정이 잠겼": 3, "본인 인증": 2, "당첨": 4, "기프트카드": 3,
    "긴급": 1, "송금": 2, "비밀번호가 만료": 3,
    # German
    "konto gesperrt": 3, "bestätigen sie ihr konto": 3, "sie haben gewonnen": 4,
    "geschenkkarte": 3, "überweisung": 2, "dringend": 1, "passwort abgelaufen": 3,
    # French
    "compte suspendu": 3, "vérifiez votre compte": 3, "vous avez gagné": 4,
    "carte cadeau": 3, "virement": 2, "mot de passe expiré": 3,
    # Spanish
    "cuenta suspendida": 3, "verifique su cuenta": 3, "has ganado": 4,
    "tarjeta regalo": 3, "transferencia bancaria": 2, "urgente": 1,
    "contraseña caducada": 3,
    # Italian
    "account sospeso": 3, "verifica il tuo account": 3, "hai vinto": 4,
    "carta regalo": 3, "bonifico": 2, "password scaduta": 3,
    # Russian
    "аккаунт заблокирован": 3, "подтвердите аккаунт": 3, "вы выиграли": 4,
    "подарочная карта": 3, "перевод средств": 2, "срочно": 1, "пароль истёк": 3,
}

#: "A break" is the time at the screen, work or not (the user, 2026-11-04); the
#: user's own work has one of its own, sooner. Said so in words of those
#: already kept (`reword_the_break`).
SCREEN_PURPOSE = "A long time at the screen, work or not: a break is suggested."
SCREEN_MESSAGE = "You have been at the screen {since_break_minutes} minutes without a break."
OLD_BREAK = (("A long stretch without a break: one is suggested.", SCREEN_PURPOSE),
             ("You have worked {since_break_minutes} minutes without a break.", SCREEN_MESSAGE))

SPAM_BRIEF = ("Code saw words of a scam here and has warned the user; that warning stands, "
              "whatever you find. Read what was on the screen and give your own reading beside "
              "it: what looks wrong here, or what looks in order, and what to do. Send only "
              "if you have something to add.")
_WARNING = {"do": "notify", "min_score": 8.0, "title": "This looks like a scam",
            "message": "What is on this page or in this email has the marks of a scam "
                       "or phishing. Do not click its links or give it any details "
                       "until you are sure who sent it."}
#: WHAT IT DID BEFORE, each as it was written to the Monitor's own table.
OLD_SPAM_THENS = (
    [_WARNING, {"do": "queue", "min_score": 4.0}],
    [dict(_WARNING, reason="Words of a scam on the screen: {matched}", and_then={
        "do": "ask_model", "handler": "judge", "brief": (
            "Code saw words of a scam here and has already warned the user. Read what "
            "was on the screen: is it really one? Send only to say it was a false "
            "alarm, or what exactly is wrong here and what to do.")}),
     {"do": "queue", "min_score": 4.0}])

SPAM = {"builtin": "spam_risk", "protected": True, "name": "Spam and scam risk",
        "group": "risk",
        "purpose": "Words of a scam or phishing on a page or in an email: a high score is "
                   "said at once, a middling one judged by the next heartbeat.",
        "when": {"keywords": {"words": SPAM_WORDS, "where": ["page", "email"]}},
        "then": [{"min_score": 8, "do": "notify", "title": "This looks like a scam",
                  "message": "What is on this page or in this email has the marks of a scam "
                             "or phishing. Do not click its links or give it any details "
                             "until you are sure who sent it.",
                  "reason": "Words of a scam on the screen: {matched}",
                  "and_then": {"do": "ask_model", "handler": "judge", "brief": SPAM_BRIEF}},
                 {"min_score": 4, "do": "queue"}],
        "cooldown_minutes": 30, "most_per_day": 20}


def _recommend(builtin: str, name: str, purpose: str, when: Dict[str, Any], brief: str,
               cooldown: float, most: int) -> Dict[str, Any]:
    return {"builtin": builtin, "name": name, "group": "recommend", "purpose": purpose,
            "when": when, "then": [{"do": "ask_model", "handler": "recommend", "brief": brief}],
            "cooldown_minutes": cooldown, "most_per_day": most}


def builtins() -> List[Dict[str, Any]]:
    return [
        _calendar("invitation", "A meeting invitation"),
        _calendar("time_agreed", "A time being agreed"),
        SPAM,
        _recommend("day_began", "The day begins",
                   "The first work of the day: new papers and news on what the user works on.",
                   {"event": "day_began"},
                   "The day has just begun: what is new on the web about what they work on.",
                   600, 1),
        _recommend("back", "Back after an absence",
                   "Back at the computer after a while: where they left off.",
                   {"all": [{"event": "back"},
                            {"signal": "back_after_minutes", "op": ">=", "value": 30}]},
                   "They are back after an absence: where they left off, and what to pick up.",
                   60, 4),
        _recommend("new_work", "New work begins",
                   "A piece of work never worked on before: what bears on it.",
                   {"event": "new_work"},
                   "They have begun new work: what would help them start it.", 60, 4),
        _recommend("project_return", "Back to a project after days",
                   "A project opened again after days: what it is and where it stood.",
                   {"event": "project_return"},
                   "They are back to this project after days: what it is, where it stood, what "
                   "is new about it.", 120, 3),
        _recommend("scattered", "The work turns scattered",
                   "Many switches in a stretch of work: an idea, or something related.",
                   {"all": [{"event": "scattered"},
                            {"signal": "since_break_minutes", "op": ">=", "value": 30}]},
                   "Their work has turned scattered: one idea or one related thing that could "
                   "help - or nothing.", 90, 2),
        _recommend("leisure", "A rest",
                   "Turned to something to watch after working: one light thing, never work.",
                   {"all": [{"event": "leisure"},
                            {"signal": "worked_today_minutes", "op": ">=", "value": 60}]},
                   "They are resting: one light thing they would enjoy - never their work.",
                   180, 2),
        {"builtin": "break", "name": "A break", "group": "rest",
         "purpose": SCREEN_PURPOSE,
         "when": {"all": [{"signal": "present", "op": "==", "value": True},
                          {"signal": "since_break_minutes", "op": ">=", "value": 120}]},
         "then": [{"do": "notify", "choices": "break", "title": "Time for a break?",
                   "message": SCREEN_MESSAGE}],
         "cooldown_minutes": 45, "most_per_day": 4},
        {"builtin": "work_break", "name": "A long stretch of work", "group": "rest",
         "purpose": "A long stretch of your own work: a pause is suggested.",
         "when": {"all": [{"signal": "present", "op": "==", "value": True},
                          {"signal": "work_minutes", "op": ">=", "value": 90}]},
         "then": [{"do": "notify", "choices": "break", "title": "Time for a break?",
                   "message": "You have been working for {work_minutes} minutes."}],
         "cooldown_minutes": 45, "most_per_day": 4},
        {"builtin": "evening", "name": "The evening report", "group": "rest",
         "purpose": "After a long day, in the evening: a report of the day, if wanted.",
         "when": {"all": [{"signal": "present", "op": "==", "value": True},
                          {"signal": "hour", "op": ">=", "value": 18},
                          {"signal": "worked_today_minutes", "op": ">=", "value": 240}]},
         "then": [{"do": "ask_model", "handler": "evening"}],
         "cooldown_minutes": 60, "most_per_day": 3},
    ]


__all__ = ["builtins", "SPAM_WORDS"]
