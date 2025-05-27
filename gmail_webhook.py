import os
import base64
import pickle
import logging
from flask import Flask, request
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from google_auth_oauthlib.flow import InstalledAppFlow
from telegram import Bot
from geopy.distance import geodesic

# ========== CONFIG ==========
SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")  # Set this in Render Environment
CHAT_ID = os.environ.get("CHAT_ID")  # Optional: For testing single recipient

app = Flask(__name__)

# ========== TEMPORARY TOKEN CREATION ==========
if not os.path.exists("token.pkl"):
    print("🔐 No token.pkl found. Starting auth flow...")
    flow = InstalledAppFlow.from_client_secrets_file("client_secret.json", SCOPES)
    creds = flow.run_local_server(port=0)
    with open("token.pkl", "wb") as token:
        pickle.dump(creds, token)
    print("✅ token.pkl created on server")

# ========== LOAD CREDS ==========
try:
    with open('token.pkl', 'rb') as token:
        creds = pickle.load(token)
except Exception as e:
    logging.error(f"❌ Error loading token: {e}")
    creds = None

# ========== TELEGRAM BOT ==========
bot = Bot(token=TELEGRAM_TOKEN)

# ========== GMAIL API SERVICE ==========
service = build('gmail', 'v1', credentials=creds)

# ========== HELPER ==========
def parse_email(body):
    # Placeholder example parser — replace with your real logic
    return {
        "pickup": "New York, NY",
        "delivery": "Atlanta, GA",
        "miles": "890 mi",
        "vehicle": "Sprinter"
    }

def send_to_telegram(data):
    text = f"📦 *New Load Alert!*\n\n🚚 Vehicle: {data['vehicle']}\n📍 Pickup: {data['pickup']}\n🏁 Delivery: {data['delivery']}\n🛣️ Miles: {data['miles']}"
    bot.send_message(chat_id=CHAT_ID, text=text, parse_mode="Markdown")

# ========== WEBHOOK ==========
@app.route('/gmail-notify', methods=['POST'])
def gmail_notify():
    try:
        history_id = request.json.get('historyId')
        logging.info(f"📬 Notification received, historyId: {history_id}")

        # Example: List latest message
        results = service.users().messages().list(userId='me', maxResults=1).execute()
        msg_id = results['messages'][0]['id']
        msg = service.users().messages().get(userId='me', id=msg_id, format='full').execute()
        payload = msg['payload']
        parts = payload.get('parts', [payload])
        for part in parts:
            if 'body' in part and 'data' in part['body']:
                data = base64.urlsafe_b64decode(part['body']['data']).decode('utf-8')
                parsed = parse_email(data)
                send_to_telegram(parsed)
                break

        return '', 200
    except Exception as e:
        logging.error(f"❌ Error processing Gmail notification: {e}")
        return '', 400

# ========== MAIN ==========
if __name__ == '__main__':
    print("🚀 LIVE VERSION STARTED 🚀")
    app.run(host='0.0.0.0', port=8080)
