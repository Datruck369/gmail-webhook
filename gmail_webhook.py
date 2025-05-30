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
TELEGRAM_BOT_TOKEN = '8197352509:AAFtUTiOgLq_oDIcPdlT_ud9lcBJFwFjJ20'
CHAT_ID = '5972776745'

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
            'pickup': self.pickup,
            'delivery': self.delivery,
            'miles': self.miles,
            'vehicle': self.vehicle
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
    credentials_file = 'credentials.json'
    
    if not os.path.exists(token_file):
        logger.error(f"token.json not found in {os.getcwd()}")
        logger.info("You need to run the OAuth flow first. Create a credentials.json file from Google Cloud Console.")
        return None
    
    try:
        creds = Credentials.from_authorized_user_file(token_file, SCOPES)
        
        # Check if credentials are valid
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                logger.info("Token expired, attempting to refresh...")
                try:
                    creds.refresh(Request())
                    # Save the refreshed token
                    with open(token_file, 'w') as token:
                        token.write(creds.to_json())
                    logger.info("✅ Token refreshed successfully")
                except Exception as refresh_error:
                    logger.error(f"Failed to refresh token: {refresh_error}")
                    logger.error("You may need to re-run the OAuth flow")
                    return None
            else:
                logger.error("Invalid credentials - no refresh token available")
                logger.error("You need to delete token.json and run the OAuth flow again")
                return None
        
        return creds
        
    except Exception as e:
        logger.error(f"Failed to load credentials: {e}")
        logger.error("Try deleting token.json and running the OAuth flow again")
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
        # Extract pickup location
        pickup_match = re.search(r'\*\*Pick-Up\*\*\s*\n?\*\*([^*]+)\*\*', body, re.IGNORECASE | re.MULTILINE)
        pickup = pickup_match.group(1).strip() if pickup_match else "Unknown Pickup"
        
        # Extract delivery location
        delivery_match = re.search(r'\*\*Delivery\*\*\s*\n?\*\*([^*]+)\*\*', body, re.IGNORECASE | re.MULTILINE)
        delivery = delivery_match.group(1).strip() if delivery_match else "Unknown Delivery"
        
        # Extract miles (looking for pattern like "2 STOPS, 156 MILES")
        miles_match = re.search(r'\*\*.*?(\d+)\s+MILES\*\*', body, re.IGNORECASE)
        miles = f"{miles_match.group(1)} miles" if miles_match else "Unknown Miles"
        
        # Extract vehicle type
        vehicle_match = re.search(r'\*\*Vehicle required:\s*([^*]+)\*\*', body, re.IGNORECASE)
        vehicle = vehicle_match.group(1).strip() if vehicle_match else "Unknown Vehicle"
        
        # Also check Load Type as backup
        if vehicle == "Unknown Vehicle":
            load_type_match = re.search(r'\*\*Load Type:\s*([^*]+)\*\*', body, re.IGNORECASE)
            vehicle = load_type_match.group(1).strip() if load_type_match else "Unknown Vehicle"
        
        logger.info(f"📋 Parsed load: {pickup} → {delivery}, {miles}, {vehicle}")
        
        return LoadData(
            pickup=pickup,
            delivery=delivery,
            miles=miles,
            vehicle=vehicle
        )
        
    except Exception as e:
        logger.error(f"Error parsing email body: {e}")
        # Return a basic load data with available info
        return LoadData(
            pickup="Parse Error",
            delivery="Parse Error", 
            miles="Unknown",
            vehicle="Unknown"
        )

def load_drivers() -> List[Driver]:
    drivers = []
    try:
        with open('drivers.csv', newline='', encoding='utf-8') as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                drivers.append(Driver(
                    driver_id=row['id'],
                    truck=row['truck'],
                    zip_code=row['zip'],
                    lat=float(row['lat']),
                    lng=float(row['lng'])
                ))
        logger.info(f"Loaded {len(drivers)} drivers from CSV")
    except Exception as e:
        logger.error(f"Error loading drivers.csv: {e}")
    return drivers

def extract_zip_code(body: str) -> Optional[str]:
    # Look for ZIP codes in pickup and delivery sections
    patterns = [
        r'\*\*Pick-Up\*\*\s*\n?\*\*[^,]+,\s*[A-Z]{2}\s+(\d{5})\*\*',  # Pickup ZIP
        r'\*\*Delivery\*\*\s*\n?\*\*[^,]+,\s*[A-Z]{2}\s+(\d{5})\*\*',  # Delivery ZIP
        r'\b(\d{5})\b'  # Any 5-digit number as fallback
    ]
    
    for pattern in patterns:
        match = re.search(pattern, body, re.IGNORECASE | re.MULTILINE)
        if match:
            zip_code = match.group(1)
            logger.info(f"📍 Found ZIP code: {zip_code}")
            return zip_code
    
    logger.warning("📍 No ZIP code found in email")
    return None

def get_zip_coordinates(zip_code: str) -> Optional[Tuple[float, float]]:
    # Expanded ZIP code mapping including Texas locations from your example
    zip_map = {
        # Texas locations from your example
        '78040': (27.5036, -99.5075),  # Laredo, TX
        '78265': (29.3013, -98.2781),  # San Antonio, TX (far west side)
        '78201': (29.4241, -98.4936),  # San Antonio, TX (downtown)
        
        # Other major cities
        '30303': (33.755, -84.39),     # Atlanta, GA
        '77001': (29.76, -95.36),      # Houston, TX
        '75201': (32.78, -96.8),       # Dallas, TX
        '10001': (40.7128, -74.0060),  # New York, NY
        '90210': (34.0522, -118.2437), # Beverly Hills, CA
        '60601': (41.8781, -87.6298),  # Chicago, IL
        '44854': (41.0895, -82.6188),  # Norwalk, OH (added this one from your logs)
        '33101': (25.7617, -80.1918),  # Miami, FL
        '85001': (33.4484, -112.0740), # Phoenix, AZ
        '98101': (47.6062, -122.3321), # Seattle, WA
        '80201': (39.7392, -104.9903), # Denver, CO
        '70112': (29.9511, -90.0715),  # New Orleans, LA
        '37201': (36.1627, -86.7816),  # Nashville, TN
        '28201': (35.2271, -80.8431),  # Charlotte, NC
        '32801': (28.5383, -81.3792),  # Orlando, FL
        '89101': (36.1699, -115.1398), # Las Vegas, NV
    }
    
    coords = zip_map.get(zip_code)
    if coords:
        logger.info(f"📍 Found coordinates for ZIP {zip_code}: {coords}")
    else:
        logger.warning(f"📍 Coordinates not found for ZIP {zip_code}")
    
    return coords

def send_to_telegram(data: LoadData, chat_id: str = None):
    try:
        text = (
            f"📦 *New Load Alert!*\n\n"
            f"🚚 *Vehicle:* {data.vehicle}\n"
            f"📍 *Pickup:* {data.pickup}\n"
            f"🏁 *Delivery:* {data.delivery}\n"
            f"🛣️ *Miles:* {data.miles}"
        )
        bot.send_message(chat_id=chat_id or CHAT_ID, text=text, parse_mode='Markdown')
    except TelegramError as e:
        logger.error(f"Telegram error: {e}")
    except Exception as e:
        logger.error(f"Failed to send to Telegram: {e}")

# ========== INIT SERVICES ==========
logger.info("🔑 Loading Gmail credentials...")
creds = load_credentials()
if not creds:
    logger.error("❌ Unable to load credentials. Please check your OAuth setup.")
    logger.error("Steps to fix:")
    logger.error("1. Ensure you have credentials.json from Google Cloud Console")
    logger.error("2. Delete token.json if it exists")
    logger.error("3. Run OAuth flow to generate new token.json")
    sys.exit(1)

try:
    logger.info("🔧 Initializing Gmail service...")
    service = build('gmail', 'v1', credentials=creds)
    
    logger.info("🤖 Initializing Telegram bot...")
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    
    # Test the services
    logger.info("🧪 Testing Gmail connection...")
    profile = service.users().getProfile(userId='me').execute()
    logger.info(f"✅ Gmail connected successfully for: {profile.get('emailAddress', 'Unknown')}")
    
    logger.info("🧪 Testing Telegram connection...")
    bot_info = bot.get_me()
    logger.info(f"✅ Telegram bot connected successfully: @{bot_info.username}")
    
except Exception as e:
    logger.error(f"❌ Service initialization failed: {e}")
    logger.error("This could be due to:")
    logger.error("1. Invalid or expired OAuth token")
    logger.error("2. Invalid Telegram bot token") 
    logger.error("3. Network connectivity issues")
    sys.exit(1)

# ========== FLASK ROUTES ==========
@app.route('/', methods=['GET'])
def health():
    return jsonify({"status": "running", "token.json": os.path.exists('token.json')})

@app.route('/test-telegram', methods=['GET'])
def test_telegram():
    # Test with your actual email format
    test_email = """**Pick-Up**
**Laredo, TX 78040**
Deliver Direct
**Delivery**
**San Antonio, TX 78265**
Deliver Direct
**2 STOPS, 156 MILES**
**Broker Name: Terrance Crawford**
**Broker Company: XPO LOGISTICS LLC**
**Broker Phone: 855.744.7976**
**Email: terrance.crawford@rxo.com**
**Posted: 05/30/25 16:31 EST**
**Expires: 05/30/25 16:59 EST**
**Dock Level: No**
**Hazmat: No**
**Posted Amount: $0.00**
**Load Type: Sprinter**
**Vehicle required: CARGO VAN**
**Pieces: 0**
**Weight: 0 lbs**
**Dimensions: 0L x 0W x 0H**
**Stackable: No**
**CSA/Fast Load: No**
**Notes: **CALL DO NOT EMAIL 704.785.1944"""
    
    # Parse the test email
    load_data = parse_email_body(test_email)
    
    # Send to Telegram
    send_to_telegram(load_data)
    
    return jsonify({
        "status": "test sent",
        "parsed_data": load_data.to_dict() if load_data else None
    })

@app.route('/gmail-notify', methods=['POST'])
def gmail_notify():
    logger.info("📩 Gmail notification received")
    try:
        # Add retry logic for SSL errors
        max_retries = 3
        results = None
        
        for attempt in range(max_retries):
            try:
                results = service.users().messages().list(userId='me', maxResults=1).execute()
                break
            except Exception as ssl_error:
                if "SSL" in str(ssl_error) and attempt < max_retries - 1:
                    logger.warning(f"SSL error on attempt {attempt + 1}, retrying...")
                    continue
                else:
                    raise ssl_error
        
        if not results:
            logger.error("Failed to get messages after retries")
            return jsonify({"status": "error", "message": "Failed to fetch messages"}), 500
            
        messages = results.get('messages', [])
        if not messages:
            logger.info("No messages found")
            return jsonify({"status": "no_messages"})
        
        # Get the latest message
        message_id = messages[0]['id']
        message = service.users().messages().get(userId='me', id=message_id).execute()
        
        # Extract email body
        body = extract_plain_text_from_message(message)
        if not body:
            logger.warning("Could not extract email body")
            return jsonify({"status": "no_body"})
        
        # Parse the load data
        load_data = parse_email_body(body)
        if load_data:
            # Send to Telegram
            send_to_telegram(load_data)
            logger.info("✅ Load data sent to Telegram")
            
            return jsonify({
                "status": "success",
                "message": "Load data processed and sent to Telegram",
                "data": load_data.to_dict()
            })
        else:
            logger.warning("Failed to parse load data")
            return jsonify({"status": "parse_error"})
            
    except Exception as e:
        logger.error(f"Error processing Gmail notification: {e}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        return jsonify({"status": "error", "message": str(e)}), 500

# ========== MAIN ==========
if __name__ == '__main__':
    logger.info("🚀 Starting Gmail Webhook Bot")
    app.run(host='0.0.0.0', port=8080)
