"""
register_webhook.py — One-time Hostfully webhook registration
Serenity Cabins / Far Longing Getaways

Run this ONCE after you have a public HTTPS URL for your webhook server.
Your webhook server must already be running and publicly reachable before
Hostfully will accept the registration.

Usage:
    python register_webhook.py --url https://your-ngrok-url.ngrok.io/webhook
    python register_webhook.py --url https://your-server.com/webhook

To list existing webhooks:
    python register_webhook.py --list

To delete a webhook:
    python register_webhook.py --delete <webhook_uid>
"""

import os
import sys
import json
import argparse
import requests
from dotenv import load_dotenv

load_dotenv()

HOSTFULLY_API_KEY = os.getenv("HOSTFULLY_API_KEY")
HOSTFULLY_BASE_URL = "https://api.hostfully.com/v2"

# ── Your Hostfully Agency UID ─────────────────────────────────────────────────
# Find this in your Hostfully dashboard → Settings → Agency Info
# It is the objectUid for NEW_INBOX_MESSAGE (agency-level event)
AGENCY_UID = "REPLACE_WITH_YOUR_AGENCY_UID"


def headers():
    return {
        "X-HOSTFULLY-APIKEY": HOSTFULLY_API_KEY,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def register_webhook(callback_url: str):
    """
    Register a NEW_INBOX_MESSAGE webhook with Hostfully.
    This tells Hostfully to POST to your server every time a guest sends a message.
    """
    payload = {
        "webhookType": "POST_JSON",          # We want JSON, not form-encoded
        "eventType": "NEW_INBOX_MESSAGE",    # Fire on every new guest inbox message
        "callbackUrl": callback_url,
        "objectUid": AGENCY_UID,             # Agency-level: catches all properties
    }

    print(f"\nRegistering webhook…")
    print(f"  Event type:   NEW_INBOX_MESSAGE")
    print(f"  Callback URL: {callback_url}")
    print(f"  Agency UID:   {AGENCY_UID}")
    print(f"  Payload type: POST_JSON\n")

    # NOTE: Verify the exact registration endpoint in your Hostfully partner docs.
    # Common patterns are /v2/webhooks or /v3/webhooks.
    url = f"{HOSTFULLY_BASE_URL}/webhooks"
    resp = requests.post(url, headers=headers(), json=payload, timeout=15)

    if resp.status_code in (200, 201):
        data = resp.json()
        print("✅ Webhook registered successfully!")
        print(json.dumps(data, indent=2))
        return data
    else:
        print(f"❌ Registration failed: {resp.status_code}")
        print(resp.text)
        sys.exit(1)


def list_webhooks():
    """List all webhooks registered for your agency."""
    url = f"{HOSTFULLY_BASE_URL}/webhooks"
    params = {"agencyUid": AGENCY_UID}
    resp = requests.get(url, headers=headers(), params=params, timeout=10)

    if resp.status_code == 200:
        data = resp.json()
        webhooks = data if isinstance(data, list) else data.get("webhooks", [])
        if not webhooks:
            print("No webhooks registered yet.")
        else:
            print(f"\n{len(webhooks)} webhook(s) registered:\n")
            for wh in webhooks:
                print(f"  UID:       {wh.get('uid', 'N/A')}")
                print(f"  Event:     {wh.get('eventType', 'N/A')}")
                print(f"  URL:       {wh.get('callbackUrl', 'N/A')}")
                print(f"  Type:      {wh.get('webhookType', 'N/A')}")
                print()
    else:
        print(f"Failed to list webhooks: {resp.status_code}")
        print(resp.text)


def delete_webhook(webhook_uid: str):
    """Delete a specific webhook by UID."""
    url = f"{HOSTFULLY_BASE_URL}/webhooks/{webhook_uid}"
    resp = requests.delete(url, headers=headers(), timeout=10)

    if resp.status_code in (200, 204):
        print(f"✅ Webhook {webhook_uid} deleted.")
    else:
        print(f"❌ Delete failed: {resp.status_code}")
        print(resp.text)


def verify_server_reachable(callback_url: str):
    """Hit the /health endpoint to make sure the server is up before registering."""
    health_url = callback_url.replace("/webhook", "/health")
    print(f"Checking server health at {health_url}…")
    try:
        resp = requests.get(health_url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            print(f"✅ Server is running — KB size: {data.get('kb_chars', '?'):,} chars\n")
        else:
            print(f"⚠️  Server returned {resp.status_code} — double-check it's running\n")
    except Exception as e:
        print(f"⚠️  Could not reach {health_url}: {e}")
        answer = input("Continue with registration anyway? (y/n): ")
        if answer.lower() != "y":
            sys.exit(0)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Hostfully webhook manager")
    parser.add_argument("--url",    help="Public HTTPS callback URL (e.g. https://abc.ngrok.io/webhook)")
    parser.add_argument("--list",   action="store_true", help="List existing webhooks")
    parser.add_argument("--delete", metavar="WEBHOOK_UID", help="Delete a webhook by UID")
    args = parser.parse_args()

    if not HOSTFULLY_API_KEY:
        print("❌ HOSTFULLY_API_KEY not set in .env")
        sys.exit(1)

    if AGENCY_UID == "REPLACE_WITH_YOUR_AGENCY_UID":
        print("❌ You must set AGENCY_UID in register_webhook.py before running.")
        print("   Find your Agency UID in Hostfully → Settings → Agency Info")
        sys.exit(1)

    if args.list:
        list_webhooks()
    elif args.delete:
        delete_webhook(args.delete)
    elif args.url:
        if not args.url.startswith("https://"):
            print("❌ Hostfully requires an HTTPS URL. Plain HTTP will be rejected.")
            sys.exit(1)
        verify_server_reachable(args.url)
        register_webhook(args.url)
    else:
        parser.print_help()
