import os
import base64
import pickle
import logging
from flask import Flask, request
from googleapiclient.discovery import build
from google.auth.transport.requests import Request
from telegram import Bot
import re
import csv
from geopy.distance import geodesic
from geopy.geocoders import Nominatim

# === CONFIG ===
SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']
TELEGRAM_TOKEN = '8197352509:AAFtUTiOgLq_oDIcPdlT_ud9lcBJFwFjJ20'  # Replace with your bot token
DRIVERS_CSV = 'drivers.csv'
ZIP_COORDINATES_FILE = 'zip_coordinates.csv'

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
bot = Bot(token=TELEGRAM_TOKEN)
geolocator = Nominatim(user_agent="argo-expedite")

# === LOAD DRIVER ZIP COORDINATES ===
zip_coordinates = {}
if os.path.exists(ZIP_COORDINATES_FILE):
    with open(ZIP_COORDINATES_FILE, newline='') as f:
        reader = csv.DictReader(f)
        for row in reader:
            zip_coordinates[row['zip']] = (float(row['lat']), float(row['lon']))

# === LOAD TOKEN ===
def load_token():
    creds = None
    try:
        with open('/etc/secrets/token.pkl', 'rb') as token_file:
            creds = pickle.load(token_file)
    except Exception as e:
        logging.error(f"❌ Error loading token: {e}")
    return creds

# === PARSE ZIP ===
def extract_pickup_zip(body):
    match = re.search(r'Pick[-\s]?Up\s*\n\n.*?(\d{5})', body)
    return match.group(1) if match else None

# === FILTER BY ZIP DISTANCE ===
def find_nearby_drivers(pickup_zip):
    if pickup_zip not in zip_coordinates:
        logging.warning(f"❌ ZIP not found in coordinates: {pickup_zip}")
        return []
    pickup_coords = zip_coordinates[pickup_zip]
    nearby = []
    with open(DRIVERS_CSV, newline='') as f:
        reader = csv.DictReader(f)
        for driver in reader:
            zip_code = driver['zip']
            chat_id = driver['id']
            truck = driver['truck']
            if zip_code in zip_coordinates:
                driver_coords = zip_coordinates[zip_code]
                distance = geodesic(pickup_coords, driver_coords).miles
                if distance <= 150:
                    nearby.append({'id': chat_id, 'truck': truck, 'zip': zip_code, 'miles': round(distance)})
    return nearby

# === FORMAT MESSAGE ===
def format_message(driver, email_body):
    pickup = re.search(r'Pick-Up\s*\n\n(.+?)\n', email_body)
    delivery = re.search(r'Delivery\s*\n\n(.+?)\n', email_body)
    miles = re.search(r'(\d{2,5})\s+MILES', email_body)
    vehicle = re.search(r'Vehicle required: (.+)', email_body)

    text = f"🚚 New Load for {driver['truck']}:\n"
    text += f"📍 Pickup: {pickup.group(1).strip() if pickup else 'N/A'}\n"
    text += f"🏁 Delivery: {delivery.group(1).strip() if delivery else 'N/A'}\n"
    text += f"📏 Miles: {miles.group(1) if miles else 'N/A'}\n"
    text += f"🚐 Vehicle: {vehicle.group(1).strip() if vehicle else 'N/A'}\n"
    text += f"📮 {driver['zip']} is {driver['miles']} miles from pickup"
    return text

# === HANDLE GMAIL NOTIFICATION ===
@app.route('/gmail-notify', methods=['POST'])
def gmail_notify():
    print("✅ Gmail notification received!")
    creds = load_token()
    if not creds:
        return "No credentials", 400
    try:
        service = build('gmail', 'v1', credentials=creds)
        history = service.users().history().list(userId='me', historyTypes=['messageAdded'], maxResults=1).execute()
        msg_id = history['history'][0]['messages'][0]['id']
        msg = service.users().messages().get(userId='me', id=msg_id, format='full').execute()
        payload = msg['payload']
        parts = payload.get('parts', [])
        body = ''

        for part in parts:
            if part['mimeType'] == 'text/html':
                data = part['body']['data']
                body = base64.urlsafe_b64decode(data).decode('utf-8', errors='ignore')
                break

        zip_code = extract_pickup_zip(body)
        if not zip_code:
            logging.warning("❌ No pickup ZIP found.")
            return "No ZIP", 200

        drivers = find_nearby_drivers(zip_code)
        for driver in drivers:
            text = format_message(driver, body)
            bot.send_message(chat_id=driver['id'], text=text)
            logging.info(f"✅ Sent to {driver['id']} ({driver['zip']})")

    except Exception as e:
        logging.error(f"❌ Error: {e}")
    return "OK", 200

# === STARTUP LOG ===
print("🚀 LIVE VERSION STARTED 🚀")

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))
