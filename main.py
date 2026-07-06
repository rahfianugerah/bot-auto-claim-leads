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

LEAD_BOT_USERNAME = os.environ["LEAD_BOT_USERNAME"].strip().lstrip("@")

CLAIM_DELAY_SECONDS = 0.2

CLAIM_LINE_RE = re.compile(r"🏃\s*CLAIM\s+(\S+)")

CODE_CONTACT_RE = re.compile(r"Kode Kontak:\s*(\S+)")

NOT_FOUND_RE = re.compile(r"kode\s+(\S+)\s+tidak ditemukan", re.IGNORECASE)
# Retry fast (0.1s) but bounded to roughly the same window a human
# competitor takes to copy-paste (~2.5s = 25 attempts at 0.1s apart).
# Not unbounded: Telegram's own flood-control will penalize an account
# sending messages this fast for too long, and a permanently-broken
# code (the duplicate-character vendor bug) would otherwise retry
# forever for no benefit.
MAX_CLAIM_ATTEMPTS = 25
RETRY_DELAY_SECONDS = 0.1

client = TelegramClient(StringSession(STRING_SESSION), API_ID, API_HASH)

# Tracks attempt count per code, both to dedupe first-time sends and to
# cap retries after a "not found" response.
_claim_attempts: dict[str, int] = {}

def has_duplicate_char(code: str) -> bool:
    letters = code.upper()
    return len(set(letters)) != len(letters)

# Toggled via /on and /off messages sent to yourself (Saved Messages).
# Resets to True on every restart/redeploy - no persistence by design.
_auto_claim_enabled = True
ON_COMMANDS = {"/on", "on"}
OFF_COMMANDS = {"/off", "off"}
STATUS_COMMANDS = {"/status", "status"}

def extract_claim_code(text: str) -> str | None:
    match = CLAIM_LINE_RE.search(text) or CODE_CONTACT_RE.search(text)
    return match.group(1) if match else None

async def send_claim(event: events.NewMessage.Event, code: str) -> None:
    _claim_attempts[code] = _claim_attempts.get(code, 0) + 1
    attempt = _claim_attempts[code]
    reply_text = f"CLAIM {code}"
    try:
        await event.respond(reply_text)
        log.info("Sent %r (attempt %d) in Chat %s", reply_text, attempt, event.chat_id)
    except Exception:
        log.exception("Failed to Send Claim for Code %s", code)


@client.on(events.NewMessage(from_users=LEAD_BOT_USERNAME))
async def handle_new_lead(event: events.NewMessage.Event) -> None:
    if event.out:
        return # ignore our own messages

    text = event.raw_text or ""

    not_found_match = NOT_FOUND_RE.search(text)
    if not_found_match:
        code = not_found_match.group(1)
        attempts = _claim_attempts.get(code, 0)
        if not _auto_claim_enabled:
            log.info("Auto Claim is Off, Not Retrying %s", code)
        elif 0 < attempts < MAX_CLAIM_ATTEMPTS:
            log.info("Got 'Not Found' for %s, Retrying (Attempt %d/%d)", code, attempts + 1, MAX_CLAIM_ATTEMPTS)
            await asyncio.sleep(RETRY_DELAY_SECONDS)
            await send_claim(event, code)
        elif attempts >= MAX_CLAIM_ATTEMPTS:
            log.warning("Giving up on %s after %d attempts", code, attempts)
        return

    if not _auto_claim_enabled:
        log.info("Auto Claim is Off, Not Processing %s", code)
        return

    code = extract_claim_code(text)
    if not code:
        return

    log.info("Extracted code %r from message %r", code, text)

    if code in _claim_attempts:
        log.info("Code already attempted: %s", code)
        return

    if has_duplicate_char(code):
        log.warning(
            "Code %s has a duplicate character - past cases like this failed "
            "with 'not found' even on a fresh, correctly-extracted code, which "
            "looks like a vendor-side bug rather than something this script can fix",
            code,
        )

    if CLAIM_DELAY_SECONDS > 0:
        await asyncio.sleep(CLAIM_DELAY_SECONDS)

    await send_claim(event, code)

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