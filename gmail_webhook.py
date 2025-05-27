import base64
import email
import os
import pickle
import re
from flask import Flask, request
from googleapiclient.discovery import build
from telegram import Bot
from geopy.distance import geodesic
import csv
import logging

# Setup logging
logging.basicConfig(level=logging.INFO)

# Your Telegram bot info (hardcoded as requested)
TELEGRAM_BOT_TOKEN = '8197352509:AAFtUTiOgLq_oDIcPdlT_ud9lcBJFwFjJ20'
CHAT_ID = '5972776745'
bot = Bot(token=TELEGRAM_BOT_TOKEN)

# Load token.pkl (Google credentials)
try:
    with open("token.pkl", "rb") as token_file:
        creds = pickle.load(token_file)
except Exception as e:
    logging.error("❌ Error loading token: %s", e)
    exit(1)

# Build Gmail service
service = build("gmail", "v1", credentials=creds)

# Load driver info from CSV
def load_drivers():
    drivers = []
    try:
        with open("drivers.csv", newline="") as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                drivers.append({
                    "id": row["id"],
                    "truck": row["truck"],
                    "zip": row["zip"],
                    "lat": float(row["lat"]),
                    "lng": float(row["lng"]),
                })
    except Exception as e:
        logging.error("❌ Error loading drivers.csv: %s", e)
    return drivers

def extract_zip(body):
    match = re.search(r'Pick[-\s]*Up\s+.*?(\d{5})', body)
    return match.group(1) if match else None

def get_zip_coordinates(zip_code):
    # You should replace this with a real ZIP-to-coordinates lookup.
    zip_map = {
        "30303": (33.755, -84.39),  # Atlanta
        "77001": (29.76, -95.36),   # Houston
        "75201": (32.78, -96.8),    # Dallas
    }
    return zip_map.get(zip_code)

# Flask app
app = Flask(__name__)

@app.route("/gmail-notify", methods=["POST"])
def gmail_notify():
    logging.info("📩 Gmail push notification received")
    try:
        history = service.users().history().list(userId="me", startHistoryId="1").execute()
        messages = history.get("history", [])
        for message in messages:
            msg_id = message["messages"][0]["id"]
            msg = service.users().messages().get(userId="me", id=msg_id, format="raw").execute()
            raw = base64.urlsafe_b64decode(msg["raw"].encode("ASCII"))
            parsed_msg = email.message_from_bytes(raw)
            body = parsed_msg.get_payload(decode=True).decode(errors="ignore")

            if "CARGO VAN" not in body.upper() and "SPRINTER" not in body.upper():
                logging.info("⏩ Skipped non-van email.")
                continue

            zip_code = extract_zip(body)
            if not zip_code:
                logging.warning("⚠️ ZIP not found.")
                continue

            coords = get_zip_coordinates(zip_code)
            if not coords:
                logging.warning("⚠️ Coordinates not found for ZIP %s", zip_code)
                continue

            drivers = load_drivers()
            for driver in drivers:
                driver_coords = (driver["lat"], driver["lng"])
                distance = geodesic(coords, driver_coords).miles
                if distance <= 150:
                    message = f"🚚 New Load!\nZIP: {zip_code}\n{body[:500]}..."
                    bot.send_message(chat_id=driver["id"], text=message)
                    logging.info(f"📤 Sent to driver {driver['id']} (distance: {distance:.1f} mi)")

    except Exception as e:
        logging.error("❌ Error processing notification: %s", e)

    return "", 200

if __name__ == "__main__":
    logging.info("🚀 LIVE VERSION STARTED 🚀")
    app.run(host="0.0.0.0", port=8080)
