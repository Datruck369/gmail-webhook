#!/usr/bin/env python3

import os
import sys
import traceback
import json
import logging
import base64
import re
import csv
from flask import Flask, request, jsonify
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from telegram import Bot
from telegram.error import TelegramError
from geopy.distance import geodesic
from typing import Dict, List, Optional, Tuple

# ========== CONFIGURATION ==========
SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '8197352509:AAFtUTiOgLq_oDIcPdlT_ud9lcBJFwFjJ20')
CHAT_ID = os.environ.get('CHAT_ID', '5972776745')

# ========== LOGGING SETUP ==========
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ========== FLASK APP INIT ==========
app = Flask(__name__)

# ========== DATA MODELS ==========
class LoadData:
    def __init__(self, pickup: str, delivery: str, miles: str, vehicle: str):
        self.pickup = pickup
        self.delivery = delivery
        self.miles = miles
        self.vehicle = vehicle

    def to_dict(self) -> Dict[str, str]:
        return {
            "pickup": self.pickup,
            "delivery": self.delivery,
            "miles": self.miles,
            "vehicle": self.vehicle
        }

class Driver:
    def __init__(self, driver_id: str, truck: str, zip_code: str, lat: float, lng: float):
        self.id = driver_id
        self.truck = truck
        self.zip = zip_code
        self.lat = lat
        self.lng = lng

    @property
    def coordinates(self) -> Tuple[float, float]:
        return (self.lat, self.lng)

# ========== HELPER FUNCTIONS ==========
def load_credentials():
    token_file = 'token.json'
    if not os.path.exists(token_file):
        logger.error(f"❌ token.json not found at: {token_file}")
        return None
    try:
        with open(token_file, 'r') as f:
            token_data = json.load(f)
        creds = Credentials.from_authorized_user_info(token_data, SCOPES)
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            with open(token_file, 'w') as f:
                f.write(creds.to_json())
        logger.info("✅ Gmail credentials loaded successfully")
        return creds
    except Exception as e:
        logger.error(f"❌ Failed to load credentials: {e}")
        return None

def extract_plain_text_from_message(message):
    try:
        payload = message.get('payload', {})
        parts = payload.get('parts', [payload])
        for part in parts:
            if part.get('mimeType') == 'text/plain':
                body_data = part.get('body', {}).get('data')
                if body_data:
                    return base64.urlsafe_b64decode(body_data).decode('utf-8')
        body_data = payload.get('body', {}).get('data')
        if body_data:
            return base64.urlsafe_b64decode(body_data).decode('utf-8')
        return None
    except Exception as e:
        logger.error(f"Failed to extract body: {e}")
        return None

def parse_email_body(body: str) -> Optional[LoadData]:
    try:
        return LoadData(
            pickup="New York, NY",
            delivery="Atlanta, GA",
            miles="890 mi",
            vehicle="Sprinter"
        )
    except Exception as e:
        logger.error(f"Error parsing email body: {e}")
        return None

def load_drivers() -> List[Driver]:
    drivers = []
    try:
        with open("drivers.csv", newline="", encoding='utf-8') as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                drivers.append(Driver(
                    driver_id=row["id"],
                    truck=row["truck"],
                    zip_code=row["zip"],
                    lat=float(row["lat"]),
                    lng=float(row["lng"])
                ))
        logger.info(f"Loaded {len(drivers)} drivers from CSV")
    except Exception as e:
        logger.error(f"Error loading drivers.csv: {e}")
    return drivers

def extract_zip_code(body: str) -> Optional[str]:
    patterns = [
        r'Pick[-\s]*Up\s+.*?(\d{5})',
        r'Pickup\s+.*?(\d{5})',
        r'Origin\s+.*?(\d{5})',
        r'\b(\d{5})\b'
    ]
    for pattern in patterns:
        match = re.search(pattern, body, re.IGNORECASE)
        if match:
            return match.group(1)
    return None

def get_zip_coordinates(zip_code: str) -> Optional[Tuple[float, float]]:
    zip_map = {
        "30303": (33.755, -84.39),
        "77001": (29.76, -95.36),
        "75201": (32.78, -96.8),
        "10001": (40.7128, -74.0060),
        "90210": (34.0522, -118.2437),
        "60601": (41.8781, -87.6298),
        "77493": (29.7858, -95.8245),
    }
    return zip_map.get(zip_code)

def send_to_telegram(data: LoadData, chat_id: str = None):
    try:
        text = (
            f"📦 *New Load Alert!*\n\n"
            f"🚚 Vehicle: {data.vehicle}\n"
            f"📍 Pickup: {data.pickup}\n"
            f"🏁 Delivery: {data.delivery}\n"
            f"🛣️ Miles: {data.miles}"
        )
        bot.send_message(chat_id=chat_id or CHAT_ID, text=text, parse_mode="Markdown")
    except TelegramError as e:
        logger.error(f"Telegram error: {e}")
    except Exception as e:
        logger.error(f"Failed to send to Telegram: {e}")

# ========== INIT SERVICES ==========
logger.info("Loading Gmail credentials...")
creds = load_credentials()
if not creds:
    logger.error("Unable to load credentials. Exiting.")
    sys.exit(1)

try:
    service = build('gmail', 'v1', credentials=creds)
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
except Exception as e:
    logger.error(f"Service initialization failed: {e}")
    sys.exit(1)

# ========== FLASK ROUTES ==========
@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "running", "token.json": os.path.exists('token.json')})

@app.route("/test-telegram", methods=["GET"])
def test_telegram():
    send_to_telegram(LoadData("Test Pickup", "Test Delivery", "123 mi", "Sprinter"))
    return jsonify({"status": "sent"})

@app.route("/gmail-notify", methods=["POST"])
def gmail_notify():
    logger.info("📩 Gmail notification received")
    try:
        results = service.users().messages().list(userId='me', maxResults=1).execute()
        messages = results.get('messages', [])
        if not messages:
            return jsonify({"status": "no messages"}), 200

        msg_id = messages[0]['id']
        message = service.users().messages().get(userId='me', id=msg_id, format='full').execute()
        body = extract_plain_text_from_message(message)

        if not body:
            return jsonify({"status": "empty body"}), 200

        if any(word in body.upper() for word in ["SPRINTER", "CARGO VAN", "VAN"]):
            zip_code = extract_zip_code(body)
            if not zip_code:
                logger.warning("ZIP code not found.")
                return jsonify({"status": "no zip"}), 200

            coords = get_zip_coordinates(zip_code)
            if not coords:
                logger.warning(f"Coordinates not found for ZIP {zip_code}")
                return jsonify({"status": "no coordinates"}), 200

            drivers = load_drivers()
            for driver in drivers:
                distance = geodesic(coords, driver.coordinates).miles
                if distance <= 150:
                    send_to_telegram(parse_email_body(body), driver.id)
                    logger.info(f"📤 Alert sent to driver {driver.id} ({distance:.1f} mi)")

            send_to_telegram(parse_email_body(body))  # Also send to main group
            return jsonify({"status": "alerts sent"}), 200

        else:
            logger.info("No relevant keywords in message.")
            return jsonify({"status": "ignored"}), 200

    except Exception as e:
        logger.error(f"Error processing webhook: {e}")
        return jsonify({"error": str(e)}), 500

# ========== MAIN ==========
if __name__ == "__main__":
    logger.info("🚀 Starting Gmail Webhook Bot")
    app.run(host="0.0.0.0", port=8080)
