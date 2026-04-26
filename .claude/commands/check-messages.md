# /check-messages

Check the Hostfully inbox for new guest messages, draft responses, and post them to Discord for review.

## What to do

1. **Load credentials** from `.env` using python-dotenv. You need: `HOSTFULLY_API_KEY`, `ANTHROPIC_API_KEY`, `DISCORD_BOT_TOKEN`, `DISCORD_CHANNEL_ID`, `KNOWLEDGE_BASE_PATH`.

2. **Load the knowledge base** by reading all `.md` files from `KNOWLEDGE_BASE_PATH` in this order:
   - agent_instructions.md
   - brand_fact_sheet.md
   - property_fact_sheets.md
   - message_templates.md
   - common_guest_faqs.md
   - addons_and_upsells.md
   - guest_screening.md
   - local_area_guides.md
   - heart_framework.md (if it exists)

3. **Poll Hostfully** for new unresponded inbox messages:
   - `GET https://api.hostfully.com/v2/leads` with header `X-HOSTFULLY-APIKEY: {key}`
   - Filter for leads with `status = NEW` or `status = INQUIRY` that have unread messages
   - For each lead, fetch the message thread: `GET https://api.hostfully.com/v2/leads/{uid}/messages`
   - Extract the latest guest message (senderType = GUEST or TRAVELER)

4. **For each new message**, decide: draft or flag?

   **Flag if** the message contains: refund, deposit, damage, broken, injury, lawyer, sue, fraud, terrible, disgusting, report you, 1 star, one star, bad review, or anything not covered by the knowledge base.

   **Draft if** it's a normal guest question about amenities, check-in, checkout, local recommendations, add-ons, or anything clearly covered by the knowledge base.

5. **Draft the response** using your built-in Claude reasoning:
   - Address the guest by first name
   - Use James's voice: warm, direct, 1–3 sentences, no filler phrases
   - Sign as James (or Mandy for Clover House Georgetown)
   - If any info needs confirmation (door code, current pool temp), write `[CONFIRM: what to check]`
   - Reference the exact template from `message_templates.md` if one fits

6. **Post to Discord** using the bot token:
   - `POST https://discord.com/api/v10/channels/{DISCORD_CHANNEL_ID}/messages`
   - Header: `Authorization: Bot {DISCORD_BOT_TOKEN}`
   - Use the format from CLAUDE.md: property, guest name, dates, original message blockquoted, draft blockquoted, reaction instructions
   - Flagged messages go to `#flagged-for-james` — you'll need a second channel ID for that (ask the user if you don't have it)

7. **Report back** with a summary:
   - How many new messages were found
   - How many drafts were posted
   - How many were flagged
   - Any errors or messages that could not be processed

## If no new messages are found

Say: "Inbox is clear — no new unresponded messages in Hostfully."

## If Hostfully API returns an error

Report the error clearly. Do not post anything to Discord. Ask the user if they want to retry or check credentials.

## Notes

- Never send a message directly to a guest. Always post to Discord for review.
- Never print API keys or tokens to the terminal.
- If the knowledge base path does not exist or is empty, stop and tell the user before doing anything else.
- Run `python agent.py` first if you want to test the drafting logic without touching Hostfully.
