from flask import Flask, request
import base64
import os
import pickle
import re
import requests
from googleapiclient.discovery import build
from google.auth.transport.requests import Request
from geopy.distance import geodesic
from geopy.geocoders import Nominatim
import csv

app = Flask(__name__)
print("🚀 LIVE VERSION STARTED 🚀")

TELEGRAM_BOT_TOKEN = "8197352509:AAFtUTiOgLq_oDIcPdlT_ud9lcBJFwFjJ20"

def get_gmail_service():
    creds = None
    if os.path.exists("token.pkl"):
        with open("token.pkl", "rb") as token_file:
            creds = pickle.load(token_file)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return build("gmail", "v1", credentials=creds)

def get_latest_email(service):
    try:
        results = service.users().messages().list(userId='me', maxResults=1).execute()
        messages = results.get("messages", [])
        if not messages:
            print("⚠️ No messages found.")
            return None
        msg = service.users().messages().get(userId='me', id=messages[0]['id'], format='full').execute()
        parts = msg['payload'].get('parts', [])
        for part in parts:
            data = part['body'].get('data')
            if data:
                return base64.urlsafe_b64decode(data).decode("utf-8")
        print("⚠️ No data part found.")
        return None
    except Exception as e:
        print("❌ Error fetching latest email:", e)
        return None

def extract_zip(text):
    match = re.search(r"\b\d{5}\b", text)
    return match.group() if match else None

def get_coords(zip_code):
    geolocator = Nominatim(user_agent="zip-locator")
    location = geolocator.geocode(f"{zip_code}, USA")
    if location:
        return (location.latitude, location.longitude)
    return None

def get_nearby_drivers(pickup_zip):
    pickup_coords = get_coords(pickup_zip)
    if not pickup_coords:
        return []
    nearby = []
    with open("drivers.csv", newline='') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            driver_zip = row["zip"]
            driver_coords = get_coords(driver_zip)
            if driver_coords:
                distance = geodesic(pickup_coords, driver_coords).miles
                if distance <= 150:
                    nearby.append({
                        "id": row["id"],
                        "truck": row["truck"],
                        "zip": driver_zip,
                        "distance": round(distance)
                    })
    return nearby

def send_to_telegram(chat_id, message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = {"chat_id": chat_id, "text": message}
    response = requests.post(url, data=data)
    return response.ok

@app.route("/gmail-notify", methods=["POST"])
def gmail_notify():
    print("✅ Gmail notification received!")

    try:
        data = request.get_json()
        print("📜 Raw push payload:", data)

        history_id = data.get("historyId")
        print("📜 History ID:", history_id)

        service = get_gmail_service()
        message = get_latest_email(service)

        if not message:
            print("⚠️ No email content returned.")
            return "", 200

        print("📩 FULL EMAIL BODY ↓↓↓↓↓↓↓")
        print(message)

        pickup_zip = extract_zip(message)
        if not pickup_zip:
            print("❌ No ZIP code found in message.")
            return "", 200

        print("📍 Found pickup ZIP:", pickup_zip)

        drivers = get_nearby_drivers(pickup_zip)
        print(f"🚛 Found {len(drivers)} drivers near {pickup_zip}")

        for driver in drivers:
            text = f"🚚 New Load for {driver['truck']}:\n\n📦 Pickup ZIP: {pickup_zip}\n📏 Distance: {driver['distance']} mi"
            sent = send_to_telegram(driver['id'], text)
            print(f"📨 Sent to driver {driver['truck']} ✅" if sent else f"❌ Failed to send to {driver['truck']}")

    except Exception as e:
        print("❌ Error:", e)

    return "", 200
