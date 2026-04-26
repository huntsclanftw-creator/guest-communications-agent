"""
agent.py — Core AI drafting + Discord posting logic
Serenity Cabins / Far Longing Getaways Guest Communications Agent

Imported by webhook_server.py. Can also be run standalone for testing:
    python agent.py
"""

import os
import glob
import logging
import requests
from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY  = os.getenv("ANTHROPIC_API_KEY")
DISCORD_BOT_TOKEN  = os.getenv("DISCORD_BOT_TOKEN")
DISCORD_CHANNEL_ID = os.getenv("DISCORD_CHANNEL_ID")
KNOWLEDGE_BASE_PATH = os.getenv("KNOWLEDGE_BASE_PATH")

DISCORD_API = "https://discord.com/api/v10"

log = logging.getLogger(__name__)

client = Anthropic(api_key=ANTHROPIC_API_KEY)

# ── Keywords that should skip drafting and escalate to James ─────────────────

ESCALATION_KEYWORDS = [
    "refund", "charge", "dispute", "damage", "damaged", "broken", "broke",
    "accident", "injury", "injured", "hurt", "police", "emergency", "fire",
    "flood", "mold", "mould", "unsafe", "lawyer", "legal", "sue", "lawsuit",
    "airbnb resolution", "vrbo resolution", "terrible", "disgusting", "awful",
    "horrible", "unacceptable", "scam", "fraud", "report you", "1 star",
    "one star", "bad review", "negative review",
]


def should_flag(message_text: str) -> bool:
    """Return True if the message should be escalated to James instead of auto-drafted."""
    lower = message_text.lower()
    return any(kw in lower for kw in ESCALATION_KEYWORDS)


# ── Knowledge base loader ─────────────────────────────────────────────────────

# Order matters — agent_instructions first so Claude reads its rules before facts
KB_FILE_ORDER = [
    "agent_instructions.md",
    "brand_fact_sheet.md",
    "property_fact_sheets.md",
    "message_templates.md",
    "common_guest_faqs.md",
    "guest_screening.md",
    "addons_and_upsells.md",
    "local_area_guides.md",
    "heart_framework.md",       # complaint / de-escalation guide
]


def load_knowledge_base(kb_path: str) -> str:
    """
    Read all knowledge base .md files and concatenate them into a single
    system prompt string. Files are loaded in the preferred order defined
    above; any extra .md files in the folder are appended at the end.
    """
    if not kb_path or not os.path.isdir(kb_path):
        log.warning("KNOWLEDGE_BASE_PATH not found or not set: %s", kb_path)
        return _fallback_system_prompt()

    sections = []
    loaded = set()

    # Load in preferred order first
    for filename in KB_FILE_ORDER:
        filepath = os.path.join(kb_path, filename)
        if os.path.isfile(filepath):
            content = _read_file(filepath)
            if content:
                sections.append(f"# === {filename} ===\n\n{content}")
                loaded.add(filename)
                log.info("Loaded KB file: %s (%d chars)", filename, len(content))

    # Catch any other .md files not in the preferred list
    for filepath in sorted(glob.glob(os.path.join(kb_path, "*.md"))):
        filename = os.path.basename(filepath)
        if filename not in loaded:
            content = _read_file(filepath)
            if content:
                sections.append(f"# === {filename} ===\n\n{content}")
                log.info("Loaded extra KB file: %s", filename)

    return "\n\n---\n\n".join(sections)


def _read_file(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception as e:
        log.error("Could not read %s: %s", path, e)
        return ""


def _fallback_system_prompt() -> str:
    return (
        "You are a guest communications agent for Serenity Cabins / Far Longing Getaways. "
        "You help draft warm, professional responses to guest messages in James Hinote's voice. "
        "Keep responses concise, friendly, and genuinely helpful. "
        "If you are unsure about a policy or detail, say so rather than guessing."
    )


# ── Claude drafting ───────────────────────────────────────────────────────────

def draft_response(
    system_prompt: str,
    guest_name: str,
    property_name: str,
    message_text: str,
    conversation_history: str = "",
    checkin: str = "unknown",
    checkout: str = "unknown",
) -> str:
    """
    Call Claude to draft a reply to the guest message.
    Returns the draft as a plain string.
    """

    context_block = f"""
You are drafting a response on behalf of James (Serenity Cabins / Far Longing Getaways).

RESERVATION CONTEXT:
- Guest name: {guest_name}
- Property: {property_name}
- Check-in: {checkin}
- Check-out: {checkout}
"""

    if conversation_history:
        context_block += f"\nRECENT CONVERSATION:\n{conversation_history}\n"

    context_block += f"\nNEW GUEST MESSAGE:\n{message_text}"

    instructions = """
Draft a reply to this guest message. Follow these rules exactly:

1. Match James's voice — warm, direct, never stiff or corporate. Use contractions.
2. Address the guest by first name.
3. Answer the specific question asked. Do not over-explain.
4. If a policy applies, state it naturally — do not quote it robotically.
5. If you need information you do not have (e.g., current pool temperature, specific door code),
   write [CONFIRM: what needs to be verified] as a placeholder so the officer can fill it in.
6. Do not include sign-offs like "Best regards" or "Sincerely" — just end naturally.
7. Sign off as "James" unless the property is Clover House Georgetown, in which case sign as "Mandy".
8. Keep it short. One to three sentences is usually right.
9. Do NOT add filler phrases like "Great question!" or "I hope you're having a wonderful day."
10. Output ONLY the draft message text — no preamble, no explanation, no commentary.
"""

    response = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=512,
        system=system_prompt + "\n\n" + instructions,
        messages=[
            {"role": "user", "content": context_block}
        ]
    )

    return response.content[0].text.strip()


# ── Discord posting ───────────────────────────────────────────────────────────

def post_to_discord(
    draft: str | None,
    guest_name: str,
    property_name: str,
    message_text: str,
    lead_uid: str,
    checkin: str = "unknown",
    checkout: str = "unknown",
    flagged: bool = False,
):
    """
    Post a formatted draft (or escalation alert) to the Discord channel.
    Officers react with:
      ✅  — approve and send as-is
      ✏️  — needs editing before sending
      🚩  — flag for James
    """
    hostfully_link = f"https://platform.hostfully.com/leads/{lead_uid}"

    if flagged:
        content = (
            f"🚨 **FLAGGED FOR JAMES** 🚨\n"
            f"**Property:** {property_name}\n"
            f"**Guest:** {guest_name}  |  Check-in: {checkin}  →  Check-out: {checkout}\n"
            f"**[View in Hostfully]({hostfully_link})**\n\n"
            f"📩 **Guest Message:**\n"
            f"> {_blockquote(message_text)}\n\n"
            f"⚠️ This message requires James's personal attention before any reply is sent."
        )
    else:
        content = (
            f"🏡 **{property_name}**  |  Guest: **{guest_name}**\n"
            f"Check-in: {checkin}  →  Check-out: {checkout}  |  "
            f"[View in Hostfully]({hostfully_link})\n\n"
            f"📩 **Guest Message:**\n"
            f"> {_blockquote(message_text)}\n\n"
            f"💬 **Suggested Reply:**\n"
            f"> {_blockquote(draft)}\n\n"
            f"React: ✅ Send as-is  |  ✏️ Edit first  |  🚩 Flag for James"
        )

    # Discord message limit is 2000 characters — truncate if needed
    if len(content) > 1990:
        content = content[:1987] + "…"

    url = f"{DISCORD_API}/channels/{DISCORD_CHANNEL_ID}/messages"
    headers = {
        "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {"content": content}

    resp = requests.post(url, headers=headers, json=payload, timeout=10)

    if resp.status_code not in (200, 201):
        log.error("Discord post failed: %s %s", resp.status_code, resp.text)
        resp.raise_for_status()
    else:
        message_id = resp.json().get("id")
        log.info("Discord message posted: %s", message_id)

        # Add default reaction prompts so officers see the buttons immediately
        for emoji in ["✅", "✏️", "🚩"]:
            _add_reaction(message_id, emoji)


def _blockquote(text: str) -> str:
    """Format text as a Discord blockquote (each line prefixed with >, no nesting)."""
    if not text:
        return "(no text)"
    return "\n> ".join(text.strip().splitlines())


def _add_reaction(message_id: str, emoji: str):
    """Add a reaction to a Discord message so it appears as a prompt for officers."""
    url = f"{DISCORD_API}/channels/{DISCORD_CHANNEL_ID}/messages/{message_id}/reactions/{requests.utils.quote(emoji)}/@me"
    headers = {"Authorization": f"Bot {DISCORD_BOT_TOKEN}"}
    resp = requests.put(url, headers=headers, timeout=5)
    if resp.status_code not in (200, 204):
        log.warning("Could not add reaction %s: %s", emoji, resp.status_code)


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    print("Loading knowledge base…")
    system_prompt = load_knowledge_base(KNOWLEDGE_BASE_PATH)
    print(f"Loaded {len(system_prompt):,} characters\n")

    # Simulate a guest message
    test_message = "Hey, what time is check-in and is early check-in possible?"
    print(f"Test message: {test_message}\n")

    draft = draft_response(
        system_prompt=system_prompt,
        guest_name="Sarah",
        property_name="Serenity Cabin",
        message_text=test_message,
        checkin="May 3, 2026",
        checkout="May 6, 2026",
    )

    print("Draft response:")
    print(draft)
    print("\n--- Discord would receive the above draft ---")
