#!/usr/bin/env python3
"""
Gmail webhook with proper email content extraction
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

print("="*50)
print("🚀 GMAIL WEBHOOK - UPDATED VERSION")
print("="*50)

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)

SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '8197352509:AAFtUTiOgLq_oDIcPdlT_ud9lcBJFwFjJ20')
CHAT_ID = os.environ.get('CHAT_ID', '5972776745')  # You'll need to set this

print(f"🤖 Telegram token configured: {'YES' if TELEGRAM_BOT_TOKEN else 'NO'}")
print(f"💬 Chat ID configured: {'YES' if CHAT_ID else 'NO'}")

def extract_pickup_info(body):
    """Extract pickup address and date/time from email body"""
    # Pattern to match "Pick-Up" followed by address and then date/time or text
    pickup_pattern = r'(?i)Pick[- ]?Up\s*[\r\n]+([^\r\n]+)[\r\n]+([^\r\n]+)'
    match = re.search(pickup_pattern, body)
    
    if match:
        pickup_address = match.group(1).strip()
        pickup_date = match.group(2).strip()
        return pickup_address, pickup_date
    
    # Fallback patterns for different formats
    fallback_patterns = [
        r'(?i)Pick[- ]?Up\s*[\r\n]+([^\r\n]+)',  # Just pickup address
        r'(?i)Pickup Location[:\- ]+\s*([^\n]+)',
        r'(?i)Pick[- ]?Up[:\- ]+\s*([^\n]+)',
        r'(?i)Pickup[:\- ]+\s*([^\n]+)',
        r'(?i)P/U[:\- ]+\s*([^\n]+)'
    ]
    
    for pattern in fallback_patterns:
        match = re.search(pattern, body)
        if match:
            return match.group(1).strip(), None
    
    return None, None

def extract_delivery_info(body):
    """Extract delivery address and date/time from email body"""
    # Pattern to match "Delivery" followed by address and then date/time or text
    delivery_pattern = r'(?i)Delivery\s*[\r\n]+([^\r\n]+)[\r\n]+([^\r\n]+)'
    match = re.search(delivery_pattern, body)
    
    if match:
        delivery_address = match.group(1).strip()
        delivery_info = match.group(2).strip()
        
        # Check if the second line looks like a date or is descriptive text
        # If it contains digits and common date patterns, treat as date
        # Otherwise, treat as delivery instructions
        if re.search(r'\d{1,2}/\d{1,2}/\d{2,4}|\d{1,2}:\d{2}|ASAP|Direct', delivery_info, re.IGNORECASE):
            return delivery_address, delivery_info
        else:
            # If second line doesn't look like date/time info, it might be part of address
            # Try to get the third line
            extended_pattern = r'(?i)Delivery\s*[\r\n]+([^\r\n]+)[\r\n]+([^\r\n]+)[\r\n]+([^\r\n]+)'
            extended_match = re.search(extended_pattern, body)
            if extended_match:
                full_address = f"{delivery_address} {match.group(2).strip()}"
                delivery_date = extended_match.group(3).strip()
                return full_address, delivery_date
            else:
                return delivery_address, delivery_info
    
    # Fallback patterns for different formats
    fallback_patterns = [
        r'(?i)Delivery\s*[\r\n]+([^\r\n]+)',  # Just delivery address
        r'(?i)Deliver to[:\- ]+\s*([^\n]+)',  # "Deliver to:" format
        r'(?i)Delivery Location[:\- ]+\s*([^\n]+)',  # "Delivery Location:" format
    ]
    
    for pattern in fallback_patterns:
        match = re.search(pattern, body)
        if match:
            return match.group(1).strip(), None
    
    return None, None

def extract_clean_body_from_gmail(service, message_id):
    """Extract clean text body from Gmail message"""
    try:
        message = service.users().messages().get(userId='me', id=message_id, format='full').execute()
        payload = message['payload']
        
        def extract_text_from_payload(payload):
            body = ""
            if 'parts' in payload:
                for part in payload['parts']:
                    if part['mimeType'] == 'text/plain':
                        data = part['body']['data']
                        body = base64.urlsafe_b64decode(data).decode('utf-8')
                        break
                    elif part['mimeType'] == 'text/html':
                        data = part['body']['data']
                        html = base64.urlsafe_b64decode(data).decode('utf-8')
                        soup = BeautifulSoup(html, 'html.parser')
                        body = soup.get_text()
                        break
                    elif 'parts' in part:
                        body = extract_text_from_payload(part)
                        if body:
                            break
            else:
                if payload['mimeType'] == 'text/plain':
                    data = payload['body']['data']
                    body = base64.urlsafe_b64decode(data).decode('utf-8')
                elif payload['mimeType'] == 'text/html':
                    data = payload['body']['data']
                    html = base64.urlsafe_b64decode(data).decode('utf-8')
                    soup = BeautifulSoup(html, 'html.parser')
                    body = soup.get_text()
            
            return body
        
        return extract_text_from_payload(payload)
    
    except Exception as e:
        logger.error(f"Error extracting email body: {e}")
        return ""

def build_formatted_message(body: str) -> str:
    """Build formatted message from email body"""
    def find(pattern, default="N/A"):
        match = re.search(pattern, body, re.IGNORECASE)
        return match.group(1).strip() if match else default

    # Use the pickup and delivery extraction functions
    pickup_address, pickup_date = extract_pickup_info(body)
    if not pickup_address:
        pickup_address = "Unknown Pickup"
    if not pickup_date:
        pickup_date = "ASAP"
    
    delivery_address, delivery_date = extract_delivery_info(body)
    if not delivery_address:
        delivery_address = "Unknown Delivery"
    if not delivery_date:
        delivery_date = "N/A"
    
    # Extract other information
    stops_miles = find(r'(\d+ STOPS?,\s*[0-9,]+ MILES)', "")
    estimated_miles = re.search(r'(\d{1,3}(?:,\d{3})*|\d+)\s*MILES', stops_miles)
    estimated_miles = estimated_miles.group(1) if estimated_miles else "N/A"
    
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

    message = f"""📦 **New Load Alert!**

📍 **Pick-up:** {pickup_address}
📅 **Pick-up date (EST):** {pickup_date}

🏁 **Deliver to:** {delivery_address}
📅 **Deliver date (EST):** {delivery_date}

🛣️ **Estimated Miles:** {estimated_miles}
🚚 **Suggested Truck Size:** {truck_size}
💰 **Posted Amount:** {posted_amount}

📦 **Pieces:** {pieces}
⚖️ **Weight:** {weight}
📏 **Dims:** {dims}
📚 **Stackable:** {stackable}

👨‍💼 **Broker:** {broker_name}
🏢 **Company:** {broker_company}
📞 **Phone:** {broker_phone}

📝 **Notes:** {notes}"""

    return message

def send_telegram_message(message):
    """Send message to Telegram"""
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = {
            "chat_id": CHAT_ID,
            "text": message,
            "parse_mode": "Markdown"
        }
        response = requests.post(url, data=data)
        logger.info(f"Telegram response: {response.status_code}")
        return response.status_code == 200
    except Exception as e:
        logger.error(f"Error sending Telegram message: {e}")
        return False

def load_credentials():
    """Load credentials from token.json"""
    token_file = 'token.json'
    
    if not os.path.exists(token_file):
        logger.error(f"❌ {token_file} not found!")
        return None
    
    try:
        creds = Credentials.from_authorized_user_file(token_file, SCOPES)
        logger.info("✅ Successfully loaded credentials from token.json")
        
        # Refresh if needed
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            # Save the refreshed credentials
            with open(token_file, 'w') as token:
                token.write(creds.to_json())
        
        return creds
    except Exception as e:
        logger.error(f"❌ Error loading credentials: {e}")
        return None

# Initialize Gmail service
logger.info("🚀 Attempting to load credentials...")
creds = load_credentials()

if not creds:
    logger.error("❌ Failed to load credentials")
    print("❌ CREDENTIALS LOADING FAILED")
    sys.exit(1)

try:
    service = build('gmail', 'v1', credentials=creds)
    logger.info("✅ Gmail service created successfully")
except Exception as e:
    logger.error(f"❌ Error building Gmail service: {e}")
    print(f"❌ GMAIL SERVICE FAILED: {e}")
    sys.exit(1)

@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "status": "running",
        "version": "updated-version",
        "message": "Gmail webhook with proper email extraction is running"
    })

@app.route("/test-gmail", methods=["GET"])
def test_gmail():
    try:
        profile = service.users().getProfile(userId='me').execute()
        return jsonify({
            "status": "success",
            "email": profile.get('emailAddress'),
            "message": "Gmail API is working!"
        })
    except Exception as e:
        return jsonify({
            "status": "error",
            "error": str(e)
        }), 500

@app.route("/test-latest-email", methods=["GET"])
def test_latest_email():
    """Test endpoint to process the latest email"""
    try:
        # Get the latest email from Sprinter label
        results = service.users().messages().list(userId='me', labelIds=['Label_1'], maxResults=1).execute()
        messages = results.get('messages', [])
        
        if not messages:
            return jsonify({
                "status": "no_messages",
                "message": "No messages found in Sprinter label"
            })
        
        message_id = messages[0]['id']
        logger.info(f"Processing latest email with ID: {message_id}")
        
        # Extract email body
        body = extract_clean_body_from_gmail(service, message_id)
        logger.info(f"Extracted body length: {len(body)}")
        
        # Build formatted message
        formatted_message = build_formatted_message(body)
        
        # Send to Telegram
        success = send_telegram_message(formatted_message)
        
        return jsonify({
            "status": "success" if success else "telegram_error",
            "message": "Email processed and sent to Telegram" if success else "Email processed but Telegram failed",
            "formatted_message": formatted_message,
            "body_preview": body[:500] + "..." if len(body) > 500 else body
        })
        
    except Exception as e:
        logger.error(f"Error in test_latest_email: {e}")
        return jsonify({
            "status": "error",
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500

@app.route("/gmail-notify", methods=["POST"])
def gmail_notify():
    """Handle Gmail webhook notifications"""
    try:
        logger.info("📩 Gmail notification received")
        
        # Get notification data
        notification_data = request.get_json()
        logger.info(f"Notification data: {notification_data}")
        
        # Decode the message data
        if 'message' in notification_data:
            message_data = notification_data['message']
            if 'data' in message_data:
                # Decode base64 data
                decoded_data = base64.b64decode(message_data['data']).decode('utf-8')
                pub_sub_data = json.loads(decoded_data)
                logger.info(f"Decoded pub/sub data: {pub_sub_data}")
                
                # Get the history ID to fetch new messages
                history_id = pub_sub_data.get('historyId')
                if history_id:
                    # Get recent messages from Sprinter label
                    results = service.users().messages().list(
                        userId='me', 
                        labelIds=['Label_1'],  # Sprinter label
                        maxResults=5
                    ).execute()
                    messages = results.get('messages', [])
                    
                    for msg in messages:
                        message_id = msg['id']
                        logger.info(f"Processing message ID: {message_id}")
                        
                        # Extract email body
                        body = extract_clean_body_from_gmail(service, message_id)
                        
                        if body and len(body) > 100:  # Only process emails with substantial content
                            # Build formatted message
                            formatted_message = build_formatted_message(body)
                            
                            # Send to Telegram
                            success = send_telegram_message(formatted_message)
                            logger.info(f"Telegram message sent: {success}")
                            
                            # Only process the first new email to avoid spam
                            break
        
        return jsonify({"status": "processed"}), 200
        
    except Exception as e:
        logger.error(f"Error processing Gmail notification: {e}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        return jsonify({
            "status": "error",
            "error": str(e)
        }), 500

if __name__ == "__main__":
    print("🎯 Starting Flask application...")
    logger.info("🚀 Updated Gmail Webhook Starting")
    app.run(host="0.0.0.0", port=8080, debug=False)
