#!/usr/bin/env python3
"""
Gmail webhook with proper email content extraction - Memory Safe Version
"""
import os
import sys
import traceback
import json
import logging
import re
import requests
from flask import Flask, request, jsonify
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from bs4 import BeautifulSoup
import base64
import gc
from typing import Optional, Tuple

def safe_decode_base64(data: str) -> Optional[str]:
    try:
        if not data:
            return None
        missing_padding = len(data) % 4
        if missing_padding:
            data += '=' * (4 - missing_padding)
        decoded = base64.urlsafe_b64decode(data)
        return decoded.decode('utf-8', errors='ignore')
    except Exception as e:
        logger.error(f"Error decoding base64: {e}")
        return None

print("="*50)
print("🚀 GMAIL WEBHOOK - MEMORY SAFE VERSION")
print("="*50)

logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '8197352509:AAFtUTiOgLq_oDIcPdlT_ud9lcBJFwFjJ20')
CHAT_ID = os.environ.get('CHAT_ID', '5972776745')

print(f"🤖 Telegram token configured: {'YES' if TELEGRAM_BOT_TOKEN else 'NO'}")
print(f"💬 Chat ID configured: {'YES' if CHAT_ID else 'NO'}")

service = None

# ... (rest of the code remains the same until send_telegram_message)

def send_telegram_message(message: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or not CHAT_ID or not message:
        logger.error("Missing bot token, chat ID, or message")
        return False

    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = {"chat_id": CHAT_ID, "text": message, "parse_mode": "Markdown"}

        # Use direct post without custom TLS adapter
        response = requests.post(url, data=data, timeout=30)
        logger.info(f"Telegram status: {response.status_code}")
        return response.status_code == 200

    except Exception as e:
        logger.error(f"Telegram error: {e}")
        return False

    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = {"chat_id": CHAT_ID, "text": message, "parse_mode": "Markdown"}

        session = requests.Session()
        session.mount("https://", TLSHttpAdapter())

        response = session.post(url, data=data, timeout=30)
        logger.info(f"Telegram status: {response.status_code}")
        return response.status_code == 200

    except Exception as e:
        logger.error(f"Telegram error: {e}")
        return False
        
def extract_delivery_info(body: str) -> Tuple[Optional[str], Optional[str]]:
    if not body:
        return None, None
    try:
        body = body.replace('\r\n', '\n').replace('\r', '\n')
        pattern = r'(?i)Delivery\s*\n+([^\n]+)\n+([^\n]+)'
        match = re.search(pattern, body)
        if match:
            return match.group(1).strip(), match.group(2).strip()
        return None, None
    except Exception as e:
        logger.error(f"Delivery extraction error: {e}")
        return None, None

def extract_clean_body_from_gmail(service, message_id: str) -> str:
    try:
        message = service.users().messages().get(userId='me', id=message_id, format='full').execute()
        payload = message.get('payload', {})
        def extract_text(payload) -> str:
            if 'parts' in payload:
                for part in payload['parts']:
                    text = extract_text(part)
                    if text:
                        return text
            mime = payload.get('mimeType', '')
            data = payload.get('body', {}).get('data')
            if mime == 'text/plain' and data:
                return safe_decode_base64(data)
            elif mime == 'text/html' and data:
                decoded = safe_decode_base64(data)
                soup = BeautifulSoup(decoded or '', 'html.parser')
                return soup.get_text()
            return ''
        result = extract_text(payload)
        gc.collect()
        return result or ''
    except Exception as e:
        logger.error(f"Email body extraction failed: {e}")
        gc.collect()
        return ''

def build_formatted_message(body: str) -> str:
    if not body:
        return "❌ **Error: Empty email body**"
    body = body.replace('\r\n', '\n').replace('\r', '\n')
    body = re.sub(r'\n{3,}', '\n\n', body)

    def find(pattern: str, default: str = "N/A") -> str:
        try:
            match = re.search(pattern, body, re.IGNORECASE)
            return match.group(1).strip() if match else default
        except:
            return default

    pickup_address, pickup_date = extract_pickup_info(body)
    delivery_address, delivery_date = extract_delivery_info(body)
    estimated_miles = find(r'(\d{1,3}(?:,\d{3})*|\d+)\s*MILES', "N/A")
    pieces = find(r'Pieces:\s*(.*)', "N/A")
    weight = find(r'Weight:\s*(.*)', "N/A")
    dims = find(r'Dimensions:\s*(.*)', "N/A")
    stackable = find(r'Stackable:\s*(.*)', "N/A")
    truck_size = find(r'Vehicle required:\s*(.*)', "N/A")
    notes = find(r'Notes:\s*(.*)', "N/A")
    broker_name = find(r'Broker Name:\s*(.*)', "N/A")
    broker_company = find(r'Broker Company:\s*(.*)', "N/A")
    broker_phone = find(r'Broker Phone:\s*(.*)', "N/A")
    posted_amount = find(r'Posted Amount:\s*(.*)', "N/A")

    def cut(text, max_len=200): return text[:max_len] + '...' if len(text) > max_len else text

    return f"""📦 **New Load Alert!**

📍 **Pick-up:** {cut(pickup_address or "Unknown Pickup")}
📅 **Pick-up date (EST):** {cut(pickup_date or "N/A")}

🏁 **Deliver to:** {cut(delivery_address or "Unknown Delivery")}
📅 **Deliver date (EST):** {cut(delivery_date or "N/A")}

🛣️ **Estimated Miles:** {estimated_miles}
🚚 **Suggested Truck Size:** {cut(truck_size)}
💰 **Posted Amount:** {cut(posted_amount)}

📦 **Pieces:** {cut(pieces)}
⚖️ **Weight:** {cut(weight)}
📏 **Dims:** {cut(dims)}
📚 **Stackable:** {stackable}

👨‍💼 **Broker:** {cut(broker_name)}
🏢 **Company:** {cut(broker_company)}
📞 **Phone:** {cut(broker_phone)}

📝 **Notes:** {cut(notes)}"""

def send_telegram_message(message: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or not CHAT_ID or not message:
        logger.error("Missing bot token, chat ID, or message")
        return False
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = {"chat_id": CHAT_ID, "text": message, "parse_mode": "Markdown"}
        response = requests.post(url, data=data, timeout=30)
        logger.info(f"Telegram status: {response.status_code}")
        return response.status_code == 200
    except Exception as e:
        logger.error(f"Telegram error: {e}")
        return False

def load_credentials() -> Optional[Credentials]:
    if not os.path.exists('token.json'):
        logger.error("token.json not found")
        return None
    try:
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            with open('token.json', 'w') as f:
                f.write(creds.to_json())
        return creds
    except Exception as e:
        logger.error(f"Credential load error: {e}")
        return None

def initialize_gmail_service() -> bool:
    global service
    creds = load_credentials()
    if not creds:
        return False
    try:
        service = build('gmail', 'v1', credentials=creds)
        return True
    except Exception as e:
        logger.error(f"Gmail init error: {e}")
        return False

if not initialize_gmail_service():
    print("❌ GMAIL SERVICE INITIALIZATION FAILED")
    sys.exit(1)

@app.route("/", methods=["GET"])
def home():
    return jsonify({"status": "running"})

@app.route("/test-latest-email", methods=["GET"])
def test_latest_email():
    try:
        results = service.users().messages().list(userId='me', labelIds=['Label_962352309899224093'], maxResults=1).execute()
        messages = results.get('messages', [])
        if not messages:
            return jsonify({"status": "no_messages"})
        msg_id = messages[0]['id']
        body = extract_clean_body_from_gmail(service, msg_id)
        msg = build_formatted_message(body)
        sent = send_telegram_message(msg)
        return jsonify({"status": "success" if sent else "telegram_failed", "message": msg})
    except Exception as e:
        logger.error(f"Test error: {e}")
        return jsonify({"status": "error", "error": str(e)}), 500

@app.route("/gmail-notify", methods=["POST"])
def gmail_notify():
    try:
        logger.info("📩 Gmail notification received")
        data = request.get_json()
        if 'message' in data and 'data' in data['message']:
            decoded = safe_decode_base64(data['message']['data'])
            pubsub_data = json.loads(decoded)
            history_id = pubsub_data.get('historyId')
            results = service.users().messages().list(userId='me', labelIds=['Label_962352309899224093'], maxResults=1).execute()
            messages = results.get('messages', [])
            for msg in messages:
                msg_id = msg['id']
                body = extract_clean_body_from_gmail(service, msg_id)
                if body and len(body) > 50:
                    formatted = build_formatted_message(body)
                    send_telegram_message(formatted)
        return jsonify({"status": "processed"})
    except Exception as e:
        logger.error(f"Notify error: {e}")
        return jsonify({"status": "error", "error": str(e)}), 500

if __name__ == "__main__":
    print("🎯 Starting Flask application...")
    app.run(host="0.0.0.0", port=8080, debug=False, threaded=True, use_reloader=False)
