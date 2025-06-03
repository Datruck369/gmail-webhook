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
import ssl
from flask import Flask, request, jsonify
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from bs4 import BeautifulSoup
import base64
import gc
from typing import Optional, Tuple
from requests.adapters import HTTPAdapter
from urllib3.util.ssl_ import create_urllib3_context

class TLSAdapter(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        context = create_urllib3_context()
        if hasattr(context, 'minimum_version'):
            context.minimum_version = ssl.TLSVersion.TLSv1_2
        kwargs['ssl_context'] = context
        return super().init_poolmanager(*args, **kwargs)

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

def send_telegram_message(message: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or not CHAT_ID or not message:
        logger.error("Missing bot token, chat ID, or message")
        return False

    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = {"chat_id": CHAT_ID, "text": message, "parse_mode": "Markdown"}

        session = requests.Session()
        session.mount("https://", TLSAdapter())
        response = session.post(url, data=data, timeout=30)

        logger.info(f"Telegram status: {response.status_code}")
        return response.status_code == 200

    except Exception as e:
        logger.error(f"Telegram error: {e}")
        return False

def extract_clean_body_from_gmail(service, message_id: str) -> str:
    try:
        message = service.users().messages().get(userId='me', id=message_id, format='full').execute()
        payload = message.get('payload', {})

        def extract_text(payload):
            if 'parts' in payload:
                for part in payload['parts']:
                    if part.get('mimeType') == 'text/plain' and part.get('body', {}).get('data'):
                        return safe_decode_base64(part['body']['data'])
                    if part.get('mimeType') == 'text/html' and part.get('body', {}).get('data'):
                        decoded = safe_decode_base64(part['body']['data'])
                        soup = BeautifulSoup(decoded, 'html.parser')
                        return soup.get_text()
            elif payload.get('body', {}).get('data'):
                return safe_decode_base64(payload['body']['data'])
            return ""

        body = extract_text(payload)
        gc.collect()
        return body or ""

    except Exception as e:
        logger.error(f"Error extracting email body: {e}")
        return ""

def build_formatted_message(body: str) -> str:
    try:
        pickup = re.search(r'(?i)Pick[- ]?Up\s*\n+([\w\W]+?)\n+([\w\W]+?)\n+', body)
        delivery = re.search(r'(?i)Delivery\s*\n+([\w\W]+?)\n+([\w\W]+?)\n+', body)

        pickup_address = pickup.group(1).strip() if pickup else "Unknown Pickup"
        pickup_date = pickup.group(2).strip() if pickup else "N/A"
        delivery_address = delivery.group(1).strip() if delivery else "Unknown Delivery"
        delivery_date = delivery.group(2).strip() if delivery else "N/A"

        pieces = re.search(r'Pieces:\s*(.*)', body)
        weight = re.search(r'Weight:\s*(.*)', body)
        dims = re.search(r'Dimensions:\s*(.*)', body)
        truck = re.search(r'Vehicle required:\s*(.*)', body)
        notes = re.search(r'Notes:\s*(.*)', body)

        return f"""📦 **New Load Alert!**

📍 **Pick-up:** {pickup_address}
📅 **Pick-up date (EST):** {pickup_date}

🏁 **Deliver to:** {delivery_address}
📅 **Deliver date (EST):** {delivery_date}

🚚 **Truck:** {truck.group(1) if truck else 'N/A'}
📦 **Pieces:** {pieces.group(1) if pieces else 'N/A'}
⚖️ **Weight:** {weight.group(1) if weight else 'N/A'}
📏 **Dimensions:** {dims.group(1) if dims else 'N/A'}
📝 **Notes:** {notes.group(1) if notes else 'N/A'}"""
    except Exception as e:
        logger.error(f"Message formatting error: {e}")
        return "❌ Failed to format message"

def load_credentials() -> Optional[Credentials]:
    token_file = 'token.json'
    if not os.path.exists(token_file):
        logger.error(f"Token file not found: {token_file}")
        return None

    try:
        creds = Credentials.from_authorized_user_file(token_file, SCOPES)
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            with open(token_file, 'w') as f:
                f.write(creds.to_json())
        return creds
    except Exception as e:
        logger.error(f"Failed to load credentials: {e}")
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
        logger.error(f"Failed to initialize Gmail service: {e}")
        return False

@app.route("/", methods=["GET"])
def home():
    return jsonify({"status": "running"})

@app.route("/gmail-notify", methods=["POST"])
def gmail_notify():
    try:
        logger.info("📩 Gmail notification received")
        notification_data = request.get_json()
        logger.info(f"Notification data: {notification_data}")

        if not service:
            logger.error("Gmail service not initialized")
            return jsonify({"status": "error", "error": "Service not initialized"}), 500

        if 'message' in notification_data:
            message_data = notification_data['message']
            if 'data' in message_data:
                decoded_data = safe_decode_base64(message_data['data'])
                if not decoded_data:
                    logger.error("Failed to decode pub/sub data")
                    return jsonify({"status": "error", "error": "Decode failed"}), 400

                pub_sub_data = json.loads(decoded_data)
                logger.info(f"Decoded pub/sub data: {pub_sub_data}")

                history_id = pub_sub_data.get('historyId')
                if history_id:
                    results = service.users().messages().list(
                        userId='me', 
                        labelIds=['Label_962352309899224093'],
                        maxResults=1
                    ).execute()
                    messages = results.get('messages', [])

                    for msg in messages:
                        message_id = msg['id']
                        logger.info(f"Processing message ID: {message_id}")
                        body = extract_clean_body_from_gmail(service, message_id)

                        if body and len(body) > 50:
                            formatted_message = build_formatted_message(body)
                            send_telegram_message(formatted_message)

        return jsonify({"status": "processed"})

    except Exception as e:
        logger.error(f"Error processing Gmail notification: {e}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        return jsonify({"status": "error", "error": str(e)}), 500

if __name__ == "__main__":
    if not initialize_gmail_service():
        logger.error("❌ Failed to initialize Gmail API")
        sys.exit(1)
    logger.info("🚀 Memory Safe Gmail Webhook Starting")
    app.run(host="0.0.0.0", port=8080, debug=False, use_reloader=False, threaded=True)
