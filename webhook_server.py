"""
webhook_server.py — Serenity Cabins Guest Communications Agent
Receives Hostfully webhook callbacks and triggers the Claude drafting pipeline.

Run locally:  python webhook_server.py
Public URL:   expose with ngrok → ngrok http 5055
              then register that URL with register_webhook.py
"""

import os
import json
import logging
import requests
from flask import Flask, request, jsonify
from dotenv import load_dotenv
from agent import draft_response, post_to_discord, load_knowledge_base, should_flag

# ── Config ────────────────────────────────────────────────────────────────────
load_dotenv()

HOSTFULLY_API_KEY  = os.getenv("HOSTFULLY_API_KEY")
KNOWLEDGE_BASE_PATH = os.getenv("KNOWLEDGE_BASE_PATH")

HOSTFULLY_BASE_URL = "https://api.hostfully.com/v2"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger(__name__)

app = Flask(__name__)

# Pre-load the knowledge base once at startup so every request is fast
SYSTEM_PROMPT = load_knowledge_base(KNOWLEDGE_BASE_PATH)
log.info("Knowledge base loaded — %d characters", len(SYSTEM_PROMPT))

# ── Hostfully API helpers ─────────────────────────────────────────────────────

def hostfully_headers():
    return {
        "X-HOSTFULLY-APIKEY": HOSTFULLY_API_KEY,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def get_lead(lead_uid: str) -> dict:
    """Fetch lead (booking/inquiry) details from Hostfully."""
    url = f"{HOSTFULLY_BASE_URL}/leads/{lead_uid}"
    resp = requests.get(url, headers=hostfully_headers(), timeout=10)
    resp.raise_for_status()
    return resp.json()


def get_messages(lead_uid: str) -> list:
    """
    Fetch the inbox message thread for a lead.
    NOTE: Verify this endpoint in your Hostfully dashboard → API docs.
    Hostfully may return messages embedded in the lead object or at a
    separate /leads/{uid}/messages endpoint.
    """
    url = f"{HOSTFULLY_BASE_URL}/leads/{lead_uid}/messages"
    resp = requests.get(url, headers=hostfully_headers(), timeout=10)
    resp.raise_for_status()
    data = resp.json()

    # Hostfully may return {"messages": [...]} or a plain list — handle both
    if isinstance(data, list):
        return data
    return data.get("messages", [])


def get_property(property_uid: str) -> dict:
    """Fetch property details so we can include the property name in the Discord post."""
    url = f"{HOSTFULLY_BASE_URL}/properties/{property_uid}"
    resp = requests.get(url, headers=hostfully_headers(), timeout=10)
    resp.raise_for_status()
    return resp.json()


def extract_latest_guest_message(messages: list) -> str | None:
    """
    Return the text of the most recent message sent BY THE GUEST (not the host).
    Hostfully messages typically have a 'senderType' or 'direction' field.
    Adjust the field name below to match what Hostfully actually returns.
    """
    guest_messages = [
        m for m in messages
        if m.get("senderType", "").upper() in ("GUEST", "TRAVELER")
        or m.get("direction", "").upper() == "INBOUND"
    ]
    if not guest_messages:
        return None
    # Sort by timestamp descending, take the newest
    guest_messages.sort(key=lambda m: m.get("createdAt", ""), reverse=True)
    return guest_messages[0].get("text") or guest_messages[0].get("message") or None


def build_conversation_history(messages: list) -> str:
    """
    Format the last 6 messages as a short transcript for Claude's context.
    """
    recent = sorted(messages, key=lambda m: m.get("createdAt", ""))[-6:]
    lines = []
    for m in recent:
        role = m.get("senderType", m.get("direction", "UNKNOWN")).upper()
        sender = "Guest" if role in ("GUEST", "TRAVELER", "INBOUND") else "Host"
        text = m.get("text") or m.get("message") or "(no text)"
        lines.append(f"{sender}: {text}")
    return "\n".join(lines)


# ── Webhook endpoint ──────────────────────────────────────────────────────────

@app.route("/webhook", methods=["POST"])
def webhook():
    # Parse payload — Hostfully sends POST_JSON so this will be application/json
    try:
        payload = request.get_json(force=True)
    except Exception:
        payload = request.form.to_dict()

    if not payload:
        log.warning("Received empty payload — ignoring")
        return jsonify({"status": "ignored", "reason": "empty payload"}), 200

    event_type  = payload.get("event_type", "")
    agency_uid  = payload.get("agency_uid", "")
    lead_uid    = payload.get("lead_uid")
    property_uid = payload.get("property_uid")

    log.info("Received event: %s | lead: %s | property: %s", event_type, lead_uid, property_uid)

    # ── We only act on new inbox messages ────────────────────────────────────
    if event_type != "NEW_INBOX_MESSAGE":
        return jsonify({"status": "ignored", "reason": f"event_type={event_type}"}), 200

    if not lead_uid:
        log.warning("NEW_INBOX_MESSAGE missing lead_uid — cannot proceed")
        return jsonify({"status": "error", "reason": "missing lead_uid"}), 200

    # ── Fetch data from Hostfully ─────────────────────────────────────────────
    try:
        lead = get_lead(lead_uid)
    except Exception as e:
        log.error("Failed to fetch lead %s: %s", lead_uid, e)
        return jsonify({"status": "error", "reason": str(e)}), 200

    try:
        messages = get_messages(lead_uid)
    except Exception as e:
        log.error("Failed to fetch messages for lead %s: %s", lead_uid, e)
        messages = []

    # Guest name — Hostfully may store as guestFirstName / guestName / travelerName
    guest_first = (
        lead.get("guestFirstName")
        or lead.get("travelerFirstName")
        or lead.get("guestName", "Guest").split()[0]
    )
    guest_name = (
        lead.get("guestName")
        or lead.get("travelerName")
        or lead.get("guestFirstName", "Guest")
    )

    # Property name
    property_name = lead.get("propertyName") or lead.get("listingName") or "Unknown Property"
    if not property_name and property_uid:
        try:
            prop = get_property(property_uid)
            property_name = prop.get("name") or prop.get("title") or "Unknown Property"
        except Exception:
            pass

    # Check-in / check-out for context
    checkin  = lead.get("checkInDate") or lead.get("arrivalDate") or "unknown"
    checkout = lead.get("checkOutDate") or lead.get("departureDate") or "unknown"

    # Get the actual message text
    latest_message = extract_latest_guest_message(messages)
    if not latest_message:
        # Fallback: Hostfully sometimes embeds the message body in the payload itself
        latest_message = payload.get("message") or payload.get("text")

    if not latest_message:
        log.warning("Could not extract message text for lead %s — skipping draft", lead_uid)
        return jsonify({"status": "ignored", "reason": "no message text found"}), 200

    conversation_history = build_conversation_history(messages) if messages else ""

    log.info("Guest: %s | Property: %s | Message: %.80s…", guest_name, property_name, latest_message)

    # ── Flag check — skip drafting and route to #flagged-for-james ───────────
    if should_flag(latest_message):
        log.info("Message flagged for James — routing to escalation channel")
        post_to_discord(
            draft=None,
            guest_name=guest_name,
            property_name=property_name,
            message_text=latest_message,
            lead_uid=lead_uid,
            checkin=checkin,
            checkout=checkout,
            flagged=True,
        )
        return jsonify({"status": "flagged"}), 200

    # ── Draft with Claude ─────────────────────────────────────────────────────
    try:
        draft = draft_response(
            system_prompt=SYSTEM_PROMPT,
            guest_name=guest_first,
            property_name=property_name,
            message_text=latest_message,
            conversation_history=conversation_history,
            checkin=checkin,
            checkout=checkout,
        )
    except Exception as e:
        log.error("Claude drafting failed: %s", e)
        return jsonify({"status": "error", "reason": str(e)}), 200

    # ── Post to Discord ───────────────────────────────────────────────────────
    try:
        post_to_discord(
            draft=draft,
            guest_name=guest_name,
            property_name=property_name,
            message_text=latest_message,
            lead_uid=lead_uid,
            checkin=checkin,
            checkout=checkout,
            flagged=False,
        )
        log.info("Draft posted to Discord for lead %s", lead_uid)
    except Exception as e:
        log.error("Discord post failed: %s", e)

    # Always return 200 — Hostfully will retry on non-2xx
    return jsonify({"status": "ok"}), 200


# ── Health check ──────────────────────────────────────────────────────────────

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "running", "kb_chars": len(SYSTEM_PROMPT)}), 200


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5055, debug=False)
