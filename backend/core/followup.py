"""Follow-up rules for the recurring check: when an open alert is escalated and when a missing check-in is chased.

Everything here is a fixed rule or fixed wording. Nothing is generated, so a worker who is unwell
never receives advice a model made up.
"""
import os
from datetime import datetime


def _minutes(name: str, default: int) -> int:
    try: return max(1, int(os.getenv(name, default)))
    except (TypeError, ValueError): return default


def config() -> dict:
    return {
        # An alert nobody has resolved is escalated once at each of these ages.
        "escalate_after_minutes": (_minutes("ALERT_ESCALATE_MINUTES", 10), _minutes("ALERT_SECOND_ESCALATE_MINUTES", 30)),
        # After a plan is sent: when workers who have not checked in are reminded, and when the supervisor is told who is missing.
        "remind_after_minutes": _minutes("ACK_REMINDER_MINUTES", 30),
        "summary_after_minutes": _minutes("ACK_SUMMARY_MINUTES", 60),
        "max_buddies": 3,
    }


def minutes_since(iso: str | None, now: datetime) -> float | None:
    try: return (now - datetime.fromisoformat(str(iso))).total_seconds() / 60
    except (TypeError, ValueError): return None


def due_stage(alert: dict, now: datetime) -> int | None:
    """The escalation step an open alert is due for (1, 2, ...), or None. Each step happens once."""
    if alert.get("status") != "OPEN": return None
    age = minutes_since(alert.get("at"), now)
    if age is None: return None
    done = int(alert.get("escalations") or 0)
    thresholds = config()["escalate_after_minutes"]
    reached = sum(1 for threshold in thresholds if age >= threshold)
    return done + 1 if reached > done else None


def pick_buddies(workers: list[dict], acknowledged: set[str], unwell_id: str, limit: int) -> list[dict]:
    """Co-workers to ask for help: reachable on Telegram, not the person who is unwell,
    and those who checked in today first, since they are known to be on site."""
    candidates = [w for w in workers if w.get("chatId") != unwell_id and w.get("channel") not in ("discord", "email")]
    candidates.sort(key=lambda w: w.get("chatId") not in acknowledged)
    return candidates[:limit]


def unacknowledged(delivered: list[str], acknowledged: set[str]) -> list[str]:
    return [recipient for recipient in delivered if recipient not in acknowledged]


FIRST_AID = {
    "en": "HeatShield: your supervisor has been alerted again. Until someone reaches you:\n1. Stop work and move to shade or a cool place.\n2. Sit or lie down and loosen tight clothing.\n3. Sip cool water if you are fully awake.\n4. Cool your skin with wet cloths or a fan.\nIf you feel confused or faint, or you have stopped sweating, ask anyone nearby to call emergency services now (112 in India).",
    "hi": "हीटशील्ड: आपके सुपरवाइज़र को फिर से सूचना दी गई है। जब तक कोई आप तक पहुँचे:\n1. काम रोकें और छाँव या ठंडी जगह पर जाएँ।\n2. बैठें या लेट जाएँ और कसे कपड़े ढीले करें।\n3. अगर आप पूरी तरह होश में हैं तो ठंडा पानी घूँट-घूँट पिएँ।\n4. गीले कपड़े या पंखे से शरीर को ठंडा करें।\nअगर चक्कर आए, बेहोशी लगे या पसीना आना बंद हो जाए, तो पास के किसी व्यक्ति से तुरंत आपातकालीन सेवा को फ़ोन करने को कहें (भारत में 112)।",
    "te": "హీట్‌షీల్డ్: మీ సూపర్‌వైజర్‌కు మళ్లీ సమాచారం పంపబడింది. ఎవరైనా మీ వద్దకు వచ్చే వరకు:\n1. పని ఆపి నీడలోకి లేదా చల్లని చోటుకు వెళ్లండి.\n2. కూర్చోండి లేదా పడుకోండి, బిగుతుగా ఉన్న బట్టలు వదులు చేయండి.\n3. మీరు పూర్తి స్పృహలో ఉంటే చల్లని నీరు కొద్దికొద్దిగా తాగండి.\n4. తడి గుడ్డలతో లేదా ఫ్యాన్‌తో శరీరాన్ని చల్లబరచండి.\nతల తిరిగినా, స్పృహ తప్పేలా ఉన్నా లేదా చెమట ఆగిపోయినా, దగ్గరలో ఉన్నవారిని వెంటనే అత్యవసర సేవలకు ఫోన్ చేయమని అడగండి (భారతదేశంలో 112).",
}

BUDDY = {
    "en": "HeatShield: {name} at {site} reported feeling unwell {minutes} minutes ago and nobody has confirmed they are being helped. If you are nearby, go to them now, move them to shade, give them water, and tell your supervisor.",
    "hi": "हीटशील्ड: {site} पर {name} ने {minutes} मिनट पहले तबीयत ख़राब होने की सूचना दी थी और अभी तक किसी ने मदद की पुष्टि नहीं की है। अगर आप पास हैं तो अभी उनके पास जाएँ, उन्हें छाँव में ले जाएँ, पानी दें और अपने सुपरवाइज़र को बताएँ।",
    "te": "హీట్‌షీల్డ్: {site} వద్ద {name} {minutes} నిమిషాల క్రితం అస్వస్థతగా ఉన్నట్లు తెలిపారు, వారికి సహాయం అందుతోందని ఇంకా ఎవరూ నిర్ధారించలేదు. మీరు దగ్గరలో ఉంటే వెంటనే వారి వద్దకు వెళ్లి, నీడలోకి తీసుకెళ్లి, నీరు ఇచ్చి, మీ సూపర్‌వైజర్‌కు చెప్పండి.",
}

UNNAMED = {"en": "A co-worker", "hi": "एक सहकर्मी", "te": "ఒక సహోద్యోగి"}

REMINDER = {
    "en": "HeatShield reminder: you have not checked in yet. Read today's heat plan for {site}, then tap I'm OK, or Not OK if you need help.",
    "hi": "हीटशील्ड रिमाइंडर: आपने अभी तक चेक-इन नहीं किया है। {site} की आज की गर्मी योजना पढ़ें, फिर I'm OK दबाएँ, या मदद चाहिए तो Not OK दबाएँ।",
    "te": "హీట్‌షీల్డ్ రిమైండర్: మీరు ఇంకా చెక్-ఇన్ చేయలేదు. {site} కోసం నేటి వేడి ప్రణాళిక చదివి, I'm OK నొక్కండి, సహాయం కావాలంటే Not OK నొక్కండి.",
}


def text(table: dict, language: str | None, **values) -> str:
    return table.get(language or "en", table["en"]).format(**values)


def supervisor_escalation_text(site_name: str, minutes: int, stage: int) -> str:
    urgency = "STILL OPEN" if stage > 1 else "not yet resolved"
    return f"HeatShield alert {urgency}: a worker at {site_name} reported feeling unwell {minutes} minutes ago and nobody has marked it handled. Check on them now and mark the alert done on the dashboard."


def supervisor_summary_text(site_name: str, missing: int, total: int, reminded: int) -> str:
    return f"HeatShield check-ins for {site_name}: {missing} of {total} workers who were sent today's plan have not checked in. {reminded} were sent a reminder. Open the dashboard to see who."
