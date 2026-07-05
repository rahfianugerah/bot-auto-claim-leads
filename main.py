"""

Telegram userbot that auto-replies `CLAIM <code>` to new lead
notifications sent by a specific bot, regardless of which chat/group
they land in.

Runs two things concurrently:
  - a Telethon client (personal account session) listening for new messages from LEAD_BOT_USERNAME
  - a tiny aiohttp server so Cloud Run sees an open $PORT and treats the container
    as healthy (this service never actually serves real traffic)

"""

# Import required libraries
import os
import re
import asyncio
import logging

from aiohttp import web
from telethon import TelegramClient, events
from telethon.sessions import StringSession

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

log = logging.getLogger("autoclaimleads")

API_ID = int(os.environ["TELEGRAM_API_ID"])
API_HASH = os.environ["TELEGRAM_API_HASH"]
STRING_SESSION = os.environ["TELEGRAM_STRING_SESSION"]
PORT = int(os.environ.get("PORT", "8080"))

# Filtering by the bot's username (sender) instead of a chat ID means we
# don't need to know which specific chat/group the lead lands in, and it
# keeps working automatically if we get added to a new group or the bot
# DMs us in a new context.
LEAD_BOT_USERNAME = os.environ["LEAD_BOT_USERNAME"].strip().lstrip("@")

# Primary: the bot's own literal command line - whatever characters appear
# here are, by definition, exactly what the bot expects back, so this is
# more trustworthy than any other field in the message.
# Anchored on the runner emoji so we don't match unrelated prose.
#
# \S+ (not [A-Za-z0-9]+) is deliberate: the vendor's codes can contain
# look-alike Unicode characters (e.g. Cyrillic "E" vs Latin "E") that are
# visually identical but are different characters. An ASCII-only class
# stops matching at the first such character, silently truncating the
# captured code - which reliably produces a "not found" response. \S+
# copies the exact underlying bytes through, whatever they are.
CLAIM_LINE_RE = re.compile(r"🏃\s*CLAIM\s+(\S+)")

# Fallback only: the "Kode Kontak:" display field. Only used if the
# action line is missing, since a mismatch between this field and the
# action line can independently produce the wrong code.
CODE_CONTACT_RE = re.compile(r"Kode Kontak:\s*(\S+)")

client = TelegramClient(StringSession(STRING_SESSION), API_ID, API_HASH)

# Per-process dedupe so a redelivered/edited event doesn't double-claim.
_claimed_codes: set[str] = set()

# Toggled via /on and /off messages sent to yourself (Saved Messages).
# Resets to True on every restart/redeploy - no persistence by design.
_auto_claim_enabled = True
ON_COMMANDS = {"/on", "on"}
OFF_COMMANDS = {"/off", "off"}
STATUS_COMMANDS = {"/status", "status"}

def extract_claim_code(text: str) -> str | None:
    match = CLAIM_LINE_RE.search(text) or CODE_CONTACT_RE.search(text)
    return match.group(1) if match else None

@client.on(events.NewMessage(from_users=LEAD_BOT_USERNAME))
async def handle_new_lead(event: events.NewMessage.Event) -> None:
    if event.out:
        return # ignore our own messages

    if not _auto_claim_enabled:
        return

    code = extract_claim_code(event.raw_text or "")
    if not code:
        return

    log.info("Extracted code %r from message %r", code, event.raw_text)

    if code in _claimed_codes:
        log.info("Code is Already Claimed: %s", code)
        return
    _claimed_codes.add(code)

    reply_text = f"CLAIM {code}"
    try:
        await event.respond(reply_text)
        log.info("Sent %r in Chat %s", reply_text, event.chat_id)
    except Exception:
        log.exception("Failed to Send Claim for Code %s", code)
        _claimed_codes.discard(code)

@client.on(events.NewMessage(chats="me"))
async def handle_control_command(event: events.NewMessage.Event) -> None:
    global _auto_claim_enabled

    text = (event.raw_text or "").strip().lower()
    if text in ON_COMMANDS:
        _auto_claim_enabled = True
        await event.respond("Auto Claim is On. New Leads Claimed Automatically.")
        log.info("Auto Claim Enabled via Saved Messages")
    elif text in OFF_COMMANDS:
        _auto_claim_enabled = False
        await event.respond("Auto Claim is Off. New Leads NOT Claimed Automatically.")
        log.info("Auto Claim Disabled via Saved Messages")
    elif text in STATUS_COMMANDS:
        state = "ON" if _auto_claim_enabled else "OFF"
        await event.respond(f"Auto Claim is Currently {state}.")

async def health(_request: web.Request) -> web.Response:
    return web.Response(text="ok")

async def run_health_server() -> None:
    app = web.Application()
    app.router.add_get("/", health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    log.info("Health Server Listening on :%s", PORT)

async def main() -> None:
    await client.start()
    me = await client.get_me()
    log.info("Logged in As %s (ID=%s)", getattr(me, "username", None), me.id)
    log.info("Watching messages from @%s for Lead Claims", LEAD_BOT_USERNAME)

    await run_health_server()
    await client.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())