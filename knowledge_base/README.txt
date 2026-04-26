GUEST COMMUNICATIONS AGENT — PROJECT FILES
==========================================
Created: March 29, 2026

PURPOSE
-------
This project contains the complete guest message archive for Serenity Cabins,
scraped and compiled from Hostfully (current PMS) and Airbnb (pre-Hostfully era).
These files are intended as training/reference data for an AI guest communications agent.

COVERAGE
--------
- Total guests served: ~143
- Airbnb era: October 2023 – June 2025 (pre-Hostfully, Serenity Cabin only)
- Hostfully era: July 2025 – present (all properties)

FILES
-----
1. hostfully_messages_final.txt  (~626 KB)
   Full formatted conversation archive from Hostfully.
   208 total records across all properties:
     - Serenity Cabin:             102 conversations
     - Rokeby Road:                 77 conversations
     - The Clover House Georgetown: 13 conversations
     - Rokeby Pool House:            7 conversations
     - Woodward Cottage:             7 conversations
     - Other:                        2 conversations

2. hostfully_data_clean.json  (~630 KB)
   Raw JSON data behind the above — same 208 records, machine-readable format.
   Fields: i (id), g (guest), p (property), ch (channel), st (status),
           ci (check-in), co (check-out), cv (conversation text)

3. airbnb_messages.txt  (~60 KB)
   Full formatted conversation archive from Airbnb (pre-Hostfully era).
   42 total conversations, all Serenity Cabin:
     - 13 confirmed stays (past guests)
     - 29 inquiries, declined, or canceled (never checked in)

4. airbnb_data.json  (~61 KB)
   Raw JSON data behind the above — same 42 records, machine-readable format.
   Fields: airbnb_idx, g (guest), last_msg_date, cv (conversation), details, p (property)

KEY REFERENCE NOTES
-------------------
- Local restaurant recommendations given to guests:
    Dining:   Molly's Irish Pub (Warrenton), The Whole Ox (Marshall),
              Red Truck Bakery (Marshall)
    Grocery:  Food Lion (Marshall), Harris Teeter (Warrenton), Wegmans (Gainesville)
    Wineries: Barrel Oak Winery, Three Fox Vineyards (both nearby, gorgeous)
    Outdoors: Sky Meadows State Park (short drive, great hike with views)

- Cabin access directions (standard):
    "Drive past the main house and go straight. You will see a gravel road marked
     with reflective stakes. Follow the road and the cabin is at the end."

- Check-in: 4:00 PM | Check-out: 11:00 AM
- Smart lock: Yale Access / August Home app integration
- Bear sightings have occurred in the area; guests advised to secure trash in shed.
