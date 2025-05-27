from flask import Flask, request
import base64
import json
import requests
import csv
import pickle
import os
from googleapiclient.discovery import build
from google.auth.transport.requests import Request
from geopy.geocoders import Nominatim
from geopy.distance import geodesic
from bs4 import BeautifulSoup
import re

app = Flask(__name__)

# === CONFIG ===
TELEGRAM_BOT_TOKEN = "8197352509:AAFtUTiOgLq_oDIcPdlT_ud9lcBJFwFjJ20"  # ⬅️ Replace this
GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
RADIUS_MILES = 150
geolocator = Nominatim(user_agent="zip-radius-filter")

# === HELPERS ===

def load_drivers():
    with open("drivers.csv", newline="") as f:
        return list(csv.DictReader(f))

def get_gmail_service():
    creds = None
    if os.path.exists("token.pkl"):
        with open("token.pkl", "rb") as token_file:
            creds = pickle.load(token_file)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return build("gmail", "v1", credentials=creds)

def extract_text_from_parts(parts):
    for part in parts:
        mime_type = part.get("mimeType", "")
        body_data = part.get("body", {}).get("data", "")

        if "multipart" in mime_type and "parts" in part:
            result = extract_text_from_parts(part["parts"])
            if result:
                return result

        if mime_type == "text/html" and body_data:
            raw_html = base64.urlsafe_b64decode(body_data).decode("utf-8", errors="ignore")
            soup = BeautifulSoup(raw_html, "html.parser")
            return soup.get_text()
        elif mime_type == "text/plain" and body_data:
            return base64.urlsafe_b64decode(body_data).decode("utf-8", errors="ignore")

    return None

def extract_zip_from_body(body):
    zip_codes = re.findall(r"\d{5}", body)
    print(f"\U0001f9e0 ZIPs found in email: {zip_codes}")

    pickup_zip = None
    for z in zip_codes:
        try:
            location = geolocator.geocode(z)
            if location:
                pickup_zip = z
                print(f"✅ Using ZIP: {pickup_zip}")
                break
        except:
            continue

    if not pickup_zip and zip_codes:
        pickup_zip = zip_codes[0]
        print(f"⚠️ Using fallback ZIP: {pickup_zip}")

    return pickup_zip

def extract_zip_from_body(body):
    zip_codes = re.findall(r"\d{5}", body)
    print(f"🧠 ZIPs found in email: {zip_codes}")

    pickup_zip = None
    delivery_zip = None

    # Correct regex (removed double backslashes)
    pickup_match = re.search(r'(?i)Pick[- ]?Up[^\n]*?(\b\d{5}\b)', body)
    delivery_match = re.search(r'(?i)Deliver[y]?[^\n]*?(\b\d{5}\b)', body)

    if pickup_match:
        pickup_zip = pickup_match.group(1)
        print(f"📍 Found pickup ZIP: {pickup_zip}")
        return pickup_zip

    if delivery_match:
        delivery_zip = delivery_match.group(1)
        print(f"📍 Found delivery ZIP: {delivery_zip}")
        return delivery_zip

    # Fallback to any ZIP that geolocates
    for z in zip_codes:
        try:
            location = geolocator.geocode(z)
            if location:
                print(f"⚠️ Using fallback ZIP: {z}")
                return z
        except:
            continue

    print("⚠️ No valid ZIP could be located.")
    return None



def send_telegram(chat_id, message):
    requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
        json={"chat_id": chat_id, "text": message}
    )

# === MAIN GMAIL WEBHOOK ===

@app.route("/gmail-notify", methods=["POST"])
def gmail_notify():
    print("✅ Gmail notification received!")

    try:
        data = request.json
        message_data = data["message"]["data"]
        decoded = base64.urlsafe_b64decode(message_data).decode("utf-8")
        message_json = json.loads(decoded)
        print(f"📜 History ID: {message_json.get('historyId')}")

        # Get latest email
        service = get_gmail_service()
        messages = service.users().messages().list(userId="me", labelIds=["INBOX"], maxResults=1).execute().get("messages", [])
        if not messages:
            return "No new messages", 200

        msg_id = messages[0]["id"]
        msg = service.users().messages().get(userId="me", id=msg_id, format="full").execute()

        snippet = msg.get("snippet", "")
        payload = msg.get("payload", {})
        parts = payload.get("parts", [])
        body = ""

        if parts:
            body = extract_text_from_parts(parts)
        elif "body" in payload and "data" in payload["body"]:
            raw_html = base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="ignore")
            soup = BeautifulSoup(raw_html, "html.parser")
            body = soup.get_text()

        email_content = body if body else snippet

        print("\n📩 FULL EMAIL BODY ↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓")
        print(email_content)
        print("↑↑↑↑↑↑↑↑↑↑↑↑↑↑↑↑↑↑↑↑↑↑↑↑↑↑↑↑↑↑↑↑↑↑↑↑↑↑↑↑↑↑↑↑↑↑↑↑↑↑↑↑↑↑↑\n")

        pickup_zip = extract_zip_from_body(email_content)
        if not pickup_zip:
            print("⚠️ No ZIP found in full email content.")
            return "", 200

        pickup, delivery = extract_pickup_delivery(email_content)

        pickup_coords = geolocator.geocode(pickup_zip)
        if not pickup_coords:
            print(f"⚠️ Could not geolocate ZIP: {pickup_zip}")
            return "", 200

        pickup_coords = (pickup_coords.latitude, pickup_coords.longitude)
        drivers = load_drivers()

        for driver in drivers:
            driver_zip = driver["zip"]
            driver_coords = geolocator.geocode(driver_zip)
            if not driver_coords:
                continue

            driver_coords = (driver_coords.latitude, driver_coords.longitude)
            distance = geodesic(pickup_coords, driver_coords).miles
            print(f"📏 Distance from {pickup_zip} to {driver_zip}: {round(distance)} mi")

            if distance <= RADIUS_MILES:
                message = (
                    f"🚚 New Load for {driver['driver_name']}:\n\n"
                    f"📦 Pickup: {pickup}\n"
                    f"🏁 Delivery: {delivery}\n"
                    f"📏 Distance to Pickup: {round(distance)} mi"
                )
                send_telegram(driver["chat_id"], message)
                print(f"📨 Sent to {driver['driver_name']} ({driver['chat_id']}) ✅")

    except Exception as e:
        print("❌ Error:", e)

    return "", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
