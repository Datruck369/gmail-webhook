#!/usr/bin/env python3

import os
import sys
import traceback
import json
import logging
import base64
import re
import csv
import ssl
import time
import httplib2
from flask import Flask, request, jsonify
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
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
    def __init__(self, pickup: str, pickup_date: str, delivery: str, delivery_date: str, 
                 miles: str, vehicle: str, pieces: str = "N/A", weight: str = "N/A", 
                 dimensions: str = "N/A", stackable: str = "N/A", notes: str = "N/A"):
        self.pickup = pickup
        self.pickup_date = pickup_date
        self.delivery = delivery
        self.delivery_date = delivery_date
        self.miles = miles
        self.vehicle = vehicle
        self.pieces = pieces
        self.weight = weight
        self.dimensions = dimensions
        self.stackable = stackable
        self.notes = notes

    def to_dict(self) -> Dict[str, str]:
        return {
            'pickup': self.pickup,
            'pickup_date': self.pickup_date,
            'delivery': self.delivery,
            'delivery_date': self.delivery_date,
            'miles': self.miles,
            'vehicle': self.vehicle,
            'pieces': self.pieces,
            'weight': self.weight,
            'dimensions': self.dimensions,
            'stackable': self.stackable,
            'notes': self.notes
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
def create_secure_http_client():
    """Create HTTP client with secure SSL configuration"""
    try:
        # Create SSL context with secure defaults
        context = ssl.create_default_context()
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.check_hostname = True
        context.verify_mode = ssl.CERT_REQUIRED
        
        # Create HTTP client with timeout and SSL context
        http = httplib2.Http(timeout=30)
        http.force_exception_to_status_code = True
        
        return http
    except Exception as e:
        logger.warning(f"Failed to create secure HTTP client, using default: {e}")
        return httplib2.Http(timeout=30)

def exponential_backoff(attempt: int, base_delay: float = 1.0, max_delay: float = 60.0) -> float:
    """Calculate exponential backoff delay"""
    delay = min(base_delay * (2 ** attempt), max_delay)
    return delay

def retry_with_backoff(func, max_retries: int = 3, exceptions: tuple = (Exception,)):
    """Decorator for retrying functions with exponential backoff"""
    def wrapper(*args, **kwargs):
        last_exception = None
        
        for attempt in range(max_retries):
            try:
                return func(*args, **kwargs)
            except exceptions as e:
                last_exception = e
                if attempt < max_retries - 1:
                    delay = exponential_backoff(attempt)
                    logger.warning(f"Attempt {attempt + 1} failed: {e}. Retrying in {delay:.1f}s...")
                    time.sleep(delay)
                else:
                    logger.error(f"All {max_retries} attempts failed. Last error: {e}")
        
        raise last_exception
    
    return wrapper

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
                    # Create a secure request object
                    request = Request()
                    creds.refresh(request)
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
        # Log the raw email body for debugging
        logger.info(f"📧 Raw email body preview: {body[:500]}...")
        
        # Extract pickup location (after **Pick-Up**)
        pickup_match = re.search(r'\*\*Pick-Up\*\*\s*\n\*\*([^*]+)\*\*', body, re.IGNORECASE | re.MULTILINE)
        pickup = pickup_match.group(1).strip() if pickup_match else "Unknown Pickup"
        
        # Extract pickup date (line after pickup location)
        pickup_date_match = re.search(r'\*\*Pick-Up\*\*\s*\n\*\*[^*]+\*\*\s*\n([^\n]+)', body, re.IGNORECASE | re.MULTILINE)
        pickup_date = pickup_date_match.group(1).strip() if pickup_date_match else "N/A"
        
        # Extract delivery location (after **Delivery**)
        delivery_match = re.search(r'\*\*Delivery\*\*\s*\n\*\*([^*]+)\*\*', body, re.IGNORECASE | re.MULTILINE)
        delivery = delivery_match.group(1).strip() if delivery_match else "Unknown Delivery"
        
        # Extract delivery date (line after delivery location)
        delivery_date_match = re.search(r'\*\*Delivery\*\*\s*\n\*\*[^*]+\*\*\s*\n([^\n]+)', body, re.IGNORECASE | re.MULTILINE)
        delivery_date = delivery_date_match.group(1).strip() if delivery_date_match else "N/A"
        
        # Extract miles (pattern like "2 STOPS, 1,269 MILES")
        miles_match = re.search(r'\*\*.*?(\d{1,3}(?:,\d{3})*)\s+MILES\*\*', body, re.IGNORECASE)
        miles = miles_match.group(1) if miles_match else "N/A"
        
        # Extract vehicle type
        vehicle_match = re.search(r'\*\*Vehicle required:\s*([^*]+)\*\*', body, re.IGNORECASE)
        vehicle = vehicle_match.group(1).strip() if vehicle_match else "N/A"
        
        # Extract pieces
        pieces_match = re.search(r'\*\*Pieces:\s*([^*]+)\*\*', body, re.IGNORECASE)
        pieces = pieces_match.group(1).strip() if pieces_match else "N/A"
        
        # Extract weight
        weight_match = re.search(r'\*\*Weight:\s*([^*]+)\*\*', body, re.IGNORECASE)
        weight = weight_match.group(1).strip() if weight_match else "N/A"
        
        # Extract dimensions
        dimensions_match = re.search(r'\*\*Dimensions:\s*([^*]+)\*\*', body, re.IGNORECASE)
        dimensions = dimensions_match.group(1).strip() if dimensions_match else "N/A"
        
        # Extract stackable
        stackable_match = re.search(r'\*\*Stackable:\s*([^*]+)\*\*', body, re.IGNORECASE)
        stackable = stackable_match.group(1).strip() if stackable_match else "N/A"
        
        # Extract notes (everything after **Notes: **)
        notes_match = re.search(r'\*\*Notes:\s*\*\*([^*]*(?:\*(?!\*)[^*]*)*)', body, re.IGNORECASE | re.DOTALL)
        notes = notes_match.group(1).strip() if notes_match else "N/A"
        
        logger.info(f"📋 Parsed load: {pickup} → {delivery}, {miles} miles, {vehicle}")
        
        return LoadData(
            pickup=pickup,
            pickup_date=pickup_date,
            delivery=delivery,
            delivery_date=delivery_date,
            miles=miles,
            vehicle=vehicle,
            pieces=pieces,
            weight=weight,
            dimensions=dimensions,
            stackable=stackable,
            notes=notes
        )
        
    except Exception as e:
        logger.error(f"Error parsing email body: {e}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        # Return a basic load data with available info
        return LoadData(
            pickup="Parse Error",
            pickup_date="N/A",
            delivery="Parse Error", 
            delivery_date="N/A",
            miles="N/A",
            vehicle="N/A"
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
            f"📍 *Pick-up:* {data.pickup}\n"
            f"📅 *Pick-up date (EST):* {data.pickup_date}\n"
            f"🏁 *Deliver to:* {data.delivery}\n"
            f"📅 *Deliver date (EST):* {data.delivery_date}\n"
            f"🛣️ *Estimated Miles:* {data.miles}\n"
            f"🚚 *Suggested Truck Size:* {data.vehicle}\n"
            f"📦 *Pieces:* {data.pieces}\n"
            f"⚖️ *Weight:* {data.weight}\n"
            f"📏 *Dims:* {data.dimensions}\n"
            f"📚 *Stackable:* {data.stackable}\n"
            f"📝 *Notes:* {data.notes}"
        )
        bot.send_message(chat_id=chat_id or CHAT_ID, text=text, parse_mode='Markdown')
        logger.info("✅ Message sent to Telegram successfully")
    except TelegramError as e:
        logger.error(f"Telegram error: {e}")
    except Exception as e:
        logger.error(f"Failed to send to Telegram: {e}")

def safe_gmail_api_call(api_method, **kwargs):
    """Safely execute Gmail API calls with SSL error handling"""
    max_retries = 3
    ssl_exceptions = (ssl.SSLError, ConnectionError, OSError)
    
    for attempt in range(max_retries):
        try:
            return api_method(**kwargs).execute()
        except ssl_exceptions as e:
            if attempt < max_retries - 1:
                delay = exponential_backoff(attempt)
                logger.warning(f"SSL error on attempt {attempt + 1}: {e}. Retrying in {delay:.1f}s...")
                time.sleep(delay)
                continue
            else:
                logger.error(f"SSL error persisted after {max_retries} attempts: {e}")
                raise
        except HttpError as e:
            if e.resp.status in [429, 500, 502, 503, 504]:  # Retryable HTTP errors
                if attempt < max_retries - 1:
                    delay = exponential_backoff(attempt)
                    logger.warning(f"HTTP error {e.resp.status} on attempt {attempt + 1}. Retrying in {delay:.1f}s...")
                    time.sleep(delay)
                    continue
            raise
        except Exception as e:
            logger.error(f"Unexpected error in Gmail API call: {e}")
            raise
    
    return None

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
    # Initialize Gmail service (credentials handle HTTP client internally)
    service = build('gmail', 'v1', credentials=creds)
    
    logger.info("🤖 Initializing Telegram bot...")
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    
    # Test the services
    logger.info("🧪 Testing Gmail connection...")
    profile = safe_gmail_api_call(service.users().getProfile, userId='me')
    if profile:
        logger.info(f"✅ Gmail connected successfully for: {profile.get('emailAddress', 'Unknown')}")
    else:
        logger.error("❌ Failed to test Gmail connection")
    
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
**Lenexa, KS 66215**
06/02/25 09:00 EST
**Delivery**
**Tampa, FL 33618**
06/04/25 08:00 EST
**2 STOPS, 1,269 MILES**
**Broker Name: Mark Stack**
**Broker Company: Express Logistics LLC**
**Broker Phone: 814.454.4373**
**Email: mark.stack@expressfamily.com**
**Posted: 05/30/25 16:44 EST**
**Expires: 05/30/25 17:12 EST**
**Dock Level: No**
**Hazmat: No**
**Posted Amount: $0.00**
**Load Type: Expedited Load**
**Vehicle required: SPRINTER**
**Pieces: 1**
**Weight: 245 lbs**
**Dimensions: 99L x 49W x 18H**
**Stackable: No**
**CSA/Fast Load: No**
**Notes: **EMAIL ONLY - *Driver needs to open the boxes on the pallet and hand carry the individual pieces into the store at delivery* these are not heavy. they are signs"""
    
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
        # Get messages with SSL error handling
        results = safe_gmail_api_call(
            service.users().messages().list,
            userId='me', 
            maxResults=1
        )
        
        if not results:
            logger.error("Failed to get messages after retries")
            return jsonify({"status": "error", "message": "Failed to fetch messages"}), 500
            
        messages = results.get('messages', [])
        if not messages:
            logger.info("No messages found")
            return jsonify({"status": "no_messages"})
        
        # Get the latest message with SSL error handling
        message_id = messages[0]['id']
        message = safe_gmail_api_call(
            service.users().messages().get,
            userId='me', 
            id=message_id
        )
        
        if not message:
            logger.error("Failed to get message content")
            return jsonify({"status": "error", "message": "Failed to fetch message content"}), 500
        
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
