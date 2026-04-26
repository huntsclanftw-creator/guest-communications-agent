"""
run_agent.py — Scheduled polling agent
Serenity Cabins / Far Longing Getaways Guest Communications Agent

Run this on a schedule (every 2 minutes) via Windows Task Scheduler or cron.
No webhook server or public URL required.

Windows Task Scheduler command:
    python "C:\Users\Hunter (Ares)\Documents\Guest Communications Agent\run_agent.py"

Cron (Mac/Linux):
    */2 * * * * python /path/to/run_agent.py >> /path/to/agent.log 2>&1

Test manually:
    python run_agent.py
    python run_agent.py --dry-run      (drafts but does NOT post to Discord)
    python run_agent.py --limit 1      (only processes 1 message, good for testing)
"""

import os
import sys
import json
import logging
import argparse
import requests
from datetime import datetime, timezone
from dotenv import load_dotenv
from agent import draft_response, post_to_discord, load_knowledge_base, should_flag

# ── Config ────────────────────────────────────────────────────────────────────
load_dotenv()

HOSTFULLY_API_KEY   = os.getenv("HOSTFULLY_API_KEY")
KNOWLEDGE_BASE_PATH = os.getenv("KNOWLEDGE_BASE_PATH")
HOSTFULLY_BASE_URL  = "https://api.hostfully.com/v2"

# Path to a small JSON file that tracks which leads we have already processed.
# This prevents re-drafting the same message on every run.
PROCESSED_FILE = os.path.join(os.path.dirname(__file__), ".processed_leads.json")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ]
)
log = logging.getLogger(__name__)


# ── Processed leads tracker ───────────────────────────────────────────────────

def load_processed() -> dict:
    """Load the set of already-processed lead+message IDs."""
    if os.path.isfile(PROCESSED_FILE):
        try:
            with open(PROCESSED_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_processed(processed: dict):
    """Persist the processed lead tracker to disk."""
    with open(PROCESSED_FILE, "w") as f:
        json.dump(processed, f, indent=2)


def already_processed(processed: dict, lead_uid: str, message_id: str) -> bool:
    return processed.get(lead_uid) == message_id


def mark_processed(processed: dict, lead_uid: str, message_id: str):
    processed[lead_uid] = message_id


# ── Hostfully API helpers ─────────────────────────────────────────────────────

def hostfully_headers():
    return {
        "X-HOSTFULLY-APIKEY": HOSTFULLY_API_KEY,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def get_active_leads() -> list:
    """
    Fetch leads (bookings/inquiries) that may have new unread messages.
    Filters for leads with status NEW or with recent activity.

    NOTE: Adjust the query params to match what the Hostfully API supports.
    Common filters: status=NEW, status=INQUIRY, or simply fetch all recent leads
    and filter by message timestamp client-side.
    """
    url = f"{HOSTFULLY_BASE_URL}/leads"
    params = {
        "limit": 50,
        "offset": 0,
        # Filter for leads with new/unread messages — adjust if Hostfully uses different params
        "status": "NEW,INQUIRY,BOOKED",
    }
    resp = requests.get(url, headers=hostfully_headers(), params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    if isinstance(data, list):
        return data
    return data.get("leads", data.get("results", []))


def get_messages_for_lead(lead_uid: str) -> list:
    """
    Fetch the message thread for a specific lead.
    Returns a list of message objects sorted by createdAt ascending.
    """
    url = f"{HOSTFULLY_BASE_URL}/leads/{lead_uid}/messages"
    resp = requests.get(url, headers=hostfully_headers(), timeout=10)
    resp.raise_for_status()
    data = resp.json()

    messages = data if isinstance(data, list) else data.get("messages", [])
    return sorted(messages, key=lambda m: m.get("createdAt", ""))


def get_lead_detail(lead_uid: str) -> dict:
    url = f"{HOSTFULLY_BASE_URL}/leads/{lead_uid}"
    resp = requests.get(url, headers=hostfully_headers(), timeout=10)
    resp.raise_for_status()
    return resp.json()


def extract_latest_guest_message(messages: list) -> dict | None:
    """
    Find the most recent message sent by the guest (not the host).
    Returns the full message object, or None if no guest messages found.
    """
    guest_messages = [
        m for m in messages
        if m.get("senderType", "").upper() in ("GUEST", "TRAVELER")
        or m.get("direction", "").upper() == "INBOUND"
    ]
    if not guest_messages:
        return None
    return guest_messages[-1]  # Already sorted ascending, so last = newest


def build_conversation_history(messages: list) -> str:
    """Format the last 6 messages as a short transcript for Claude context."""
    recent = messages[-6:]
    lines = []
    for m in recent:
        role = m.get("senderType", m.get("direction", "")).upper()
        sender = "Guest" if role in ("GUEST", "TRAVELER", "INBOUND") else "Host"
        text = m.get("text") or m.get("message") or "(no text)"
        lines.append(f"{sender}: {text}")
    return "\n".join(lines)


# ── Main polling loop ─────────────────────────────────────────────────────────

def run(dry_run: bool = False, limit: int = 0):
    log.info("── Guest Communications Agent — polling run at %s ──", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    # Validate credentials
    if not HOSTFULLY_API_KEY:
        log.error("HOSTFULLY_API_KEY not set in .env — aborting")
        sys.exit(1)

    # Load knowledge base
    log.info("Loading knowledge base from: %s", KNOWLEDGE_BASE_PATH)
    system_prompt = load_knowledge_base(KNOWLEDGE_BASE_PATH)
    if not system_prompt:
        log.error("Knowledge base is empty or path not found — aborting")
        sys.exit(1)
    log.info("Knowledge base loaded: %d characters", len(system_prompt))

    # Load already-processed tracker
    processed = load_processed()

    # Fetch leads
    try:
        leads = get_active_leads()
    except Exception as e:
        log.error("Failed to fetch leads from Hostfully: %s", e)
        sys.exit(1)

    log.info("Found %d leads to check", len(leads))

    drafted = 0
    flagged = 0
    skipped = 0
    errors  = 0

    for lead in leads:
        if limit and (drafted + flagged) >= limit:
            log.info("Limit of %d reached — stopping", limit)
            break

        lead_uid = lead.get("uid") or lead.get("id")
        if not lead_uid:
            continue

        # Fetch message thread
        try:
            messages = get_messages_for_lead(lead_uid)
        except Exception as e:
            log.warning("Could not fetch messages for lead %s: %s", lead_uid, e)
            errors += 1
            continue

        latest_msg = extract_latest_guest_message(messages)
        if not latest_msg:
            skipped += 1
            continue

        message_id = latest_msg.get("uid") or latest_msg.get("id") or latest_msg.get("createdAt", "")

        # Skip if already processed
        if already_processed(processed, lead_uid, message_id):
            skipped += 1
            continue

        message_text = latest_msg.get("text") or latest_msg.get("message") or ""
        if not message_text.strip():
            skipped += 1
            continue

        # Pull guest and property info from the lead object
        guest_name  = lead.get("guestName") or lead.get("travelerName") or "Guest"
        guest_first = guest_name.split()[0] if guest_name else "Guest"
        property_name = lead.get("propertyName") or lead.get("listingName") or "Unknown Property"
        checkin  = lead.get("checkInDate") or lead.get("arrivalDate") or "unknown"
        checkout = lead.get("checkOutDate") or lead.get("departureDate") or "unknown"
        conversation_history = build_conversation_history(messages)

        log.info(
            "Processing lead %s | Guest: %s | Property: %s | Message: %.60s…",
            lead_uid, guest_name, property_name, message_text
        )

        # ── Flag check ────────────────────────────────────────────────────────
        if should_flag(message_text):
            log.info("→ FLAGGED for James")
            if not dry_run:
                try:
                    post_to_discord(
                        draft=None,
                        guest_name=guest_name,
                        property_name=property_name,
                        message_text=message_text,
                        lead_uid=lead_uid,
                        checkin=checkin,
                        checkout=checkout,
                        flagged=True,
                    )
                    mark_processed(processed, lead_uid, message_id)
                    flagged += 1
                except Exception as e:
                    log.error("Discord post failed for flagged message: %s", e)
                    errors += 1
            else:
                log.info("  [DRY RUN] Would post flagged alert to Discord")
                flagged += 1
            continue

        # ── Draft with Claude ─────────────────────────────────────────────────
        try:
            draft = draft_response(
                system_prompt=system_prompt,
                guest_name=guest_first,
                property_name=property_name,
                message_text=message_text,
                conversation_history=conversation_history,
                checkin=checkin,
                checkout=checkout,
            )
            log.info("→ Draft: %s", draft[:100])
        except Exception as e:
            log.error("Claude drafting failed for lead %s: %s", lead_uid, e)
            errors += 1
            continue

        # ── Post to Discord ───────────────────────────────────────────────────
        if not dry_run:
            try:
                post_to_discord(
                    draft=draft,
                    guest_name=guest_name,
                    property_name=property_name,
                    message_text=message_text,
                    lead_uid=lead_uid,
                    checkin=checkin,
                    checkout=checkout,
                    flagged=False,
                )
                mark_processed(processed, lead_uid, message_id)
                drafted += 1
            except Exception as e:
                log.error("Discord post failed for lead %s: %s", lead_uid, e)
                errors += 1
        else:
            log.info("  [DRY RUN] Would post draft to Discord")
            mark_processed(processed, lead_uid, message_id)
            drafted += 1

    # Save processed tracker
    if not dry_run:
        save_processed(processed)

    log.info(
        "── Run complete: %d drafted | %d flagged | %d skipped | %d errors ──",
        drafted, flagged, skipped, errors
    )


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Serenity Cabins guest communications polling agent")
    parser.add_argument("--dry-run", action="store_true", help="Draft but do not post to Discord or mark as processed")
    parser.add_argument("--limit", type=int, default=0, help="Max messages to process in this run (0 = no limit)")
    args = parser.parse_args()

    run(dry_run=args.dry_run, limit=args.limit)
