"""

One-time local helper to log in to your personal Telegram account and
print a StringSession.

Run this ONCE on your own machine (never in cloud):
    python generate_session.py

It will prompt for your phone number and the OTP code Telegram sends you.
Copy the printed string into Secret Manager as TELEGRAM_STRING_SESSION.
Treat it like a password: anyone with this string can log in as you.

"""

import os

from telethon.sync import TelegramClient
from telethon.sessions import StringSession
from dotenv import load_dotenv

load_dotenv() # take environment variables from .env

API_ID = int(os.getenv("TELEGRAM_API_ID", 0))
API_HASH = os.getenv("TELEGRAM_API_HASH")

if not API_ID or not API_HASH:
    raise EnvironmentError("TELEGRAM_API_ID and TELEGRAM_API_HASH must be set in .env")

with TelegramClient(StringSession(), API_ID, API_HASH) as client:
    print("Logged in Successfully")
    print(f"TELEGRAM_STRING_SESSION: {client.session.save()}")
