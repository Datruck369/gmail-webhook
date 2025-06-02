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
import gc  # Garbage collection
from typing import Optional, Tuple

print("="*50)
print("🚀 GMAIL WEBHOOK - MEMORY SAFE VERSION")
print("="*50)

# Setup logging with safer configuration
logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Limit Flask's memory usage
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB limit

SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '8197352509:AAFtUTiOgLq_oDIcPdlT_ud9lcBJFwFjJ20')
CHAT_ID = os.environ.get('CHAT_ID', '5972776745')

print(f"🤖 Telegram token configured: {'YES' if TELEGRAM_BOT_TOKEN else 'NO'}")
print(f"💬 Chat ID configured: {'YES' if CHAT_ID else 'NO'}")

# Global service variable
service = None

def safe_decode_base64(data: str) -> Optional[str]:
    """Safely decode base64 data with error handling"""
    try:
        if not data:
            return None
        
        # Add padding if needed
        missing_padding = len(data) % 4
        if missing_padding:
            data += '=' * (4 - missing_padding)
        
        decoded = base64.urlsafe_b64decode(data)
        return decoded.decode('utf-8', errors='ignore')
    except Exception as e:
        logger.error(f"Error decoding base64: {e}")
        return None

def extract_pickup_info(body: str) -> Tuple[Optional[str], Optional[str]]:
    """Extract pickup address and date/time from email body"""
    if not body:
        return None, None
    
    try:
        # Pattern to match "Pick-Up" followed by address and then date/time or text
        pickup_pattern = r'(?i)Pick[- ]?Up\s*[\r\n]+([^\r\n]+)[\r\n]+([^\r\n]+)'
        match = re.search(pickup_pattern, body)
        
        if match:
            pickup_address = match.group(1).strip()
            pickup_date = match.group(2).strip()
            return pickup_address, pickup_date
        
        # Fallback patterns for different formats
        fallback_patterns = [
            r'(?i)Pick[- ]?Up\s*[\r\n]+([^\r\n]+)',
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
    except Exception as e:
        logger.error(f"Error extracting pickup info: {e}")
        return None, None

def extract_delivery_info(body: str) -> Tuple[Optional[str], Optional[str]]:
    """Extract delivery address and date/time from email body"""
    if not body:
        return None, None
    
    try:
        # Pattern to match "Delivery" followed by address and then date/time or text
        delivery_pattern = r'(?i)Delivery\s*[\r\n]+([^\r\n]+)[\r\n]+([^\r\n]+)'
        match = re.search(delivery_pattern, body)
        
        if match:
            delivery_address = match.group(1).strip()
            delivery_info = match.group(2).strip()
            
            # Check if the second line looks like a date or is descriptive text
            if re.search(r'\d{1,2}/\d{1,2}/\d{2,4}|\d{1,2}:\d{2}|ASAP|Direct', delivery_info, re.IGNORECASE):
                return delivery_address, delivery_info
            else:
                # Try to get the third line
                extended_pattern = r'(?i)Delivery\s*[\r\n]+([^\r\n]+)[\r\n]+([^\r\n]+)[\r\n]+([^\r\n]+)'
                extended_match = re.search(extended_pattern, body)
                if extended_match:
                    full_address = f"{delivery_address} {match.group(2).strip()}"
                    delivery_date = extended_match.group(3).strip()
                    return full_address, delivery_date
                else:
                    return delivery_address, delivery_info
        
        # Fallback patterns
        fallback_patterns = [
            r'(?i)Delivery\s*[\r\n]+([^\r\n]+)',
            r'(?i)Deliver to[:\- ]+\s*([^\n]+)',
            r'(?i)Delivery Location[:\- ]+\s*([^\n]+)',
        ]
        
        for pattern in fallback_patterns:
            match = re.search(pattern, body)
            if match:
                return match.group(1).strip(), None
        
        return None, None
    except Exception as e:
        logger.error(f"Error extracting delivery info: {e}")
        return None, None

def extract_clean_body_from_gmail(service, message_id: str) -> str:
    """Extract clean text body from Gmail message with memory safety"""
    try:
        if not service or not message_id:
            return ""
        
        message = service.users().messages().get(userId='me', id=message_id, format='full').execute()
        if not message or 'payload' not in message:
            return ""
        
        payload = message['payload']
        
        def extract_text_from_payload(payload) -> str:
            body = ""
            try:
                if 'parts' in payload:
                    for part in payload['parts']:
                        if part.get('mimeType') == 'text/plain' and part.get('body', {}).get('data'):
                            data = part['body']['data']
                            decoded = safe_decode_base64(data)
                            if decoded:
                                body = decoded
                                break
                        elif part.get('mimeType') == 'text/html' and part.get('body', {}).get('data'):
                            data = part['body']['data']
                            decoded = safe_decode_base64(data)
                            if decoded:
                                # Use BeautifulSoup safely
                                try:
                                    soup = BeautifulSoup(decoded, 'html.parser')
                                    body = soup.get_text()
                                    # Clean up soup object
                                    soup.decompose()
                                    del soup
                                except Exception as soup_error:
                                    logger.error(f"BeautifulSoup error: {soup_error}")
                                    body = decoded  # Fallback to raw HTML
                                break
                        elif 'parts' in part:
                            body = extract_text_from_payload(part)
                            if body:
                                break
                else:
                    if payload.get('mimeType') == 'text/plain' and payload.get('body', {}).get('data'):
                        data = payload['body']['data']
                        body = safe_decode_base64(data) or ""
                    elif payload.get('mimeType') == 'text/html' and payload.get('body', {}).get('data'):
                        data = payload['body']['data']
                        decoded = safe_decode_base64(data)
                        if decoded:
                            try:
                                soup = BeautifulSoup(decoded, 'html.parser')
                                body = soup.get_text()
                                soup.decompose()
                                del soup
                            except Exception as soup_error:
                                logger.error(f"BeautifulSoup error: {soup_error}")
                                body = decoded
                
                return body
            except Exception as e:
                logger.error(f"Error in extract_text_from_payload: {e}")
                return ""
        
        result = extract_text_from_payload(payload)
        
        # Force garbage collection
        gc.collect()
        
        return result or ""
    
    except Exception as e:
        logger.error(f"Error extracting email body: {e}")
        gc.collect()  # Clean up on error
        return ""

def build_formatted_message(body: str) -> str:
    """Build formatted message from email body with safer regex operations"""
    if not body:
        return "❌ **Error: Empty email body**"
    
    try:
        def find(pattern: str, default: str = "N/A") -> str:
            try:
                match = re.search(pattern, body, re.IGNORECASE)
                return match.group(1).strip() if match and match.group(1) else default
            except Exception:
                return default

        # Use the pickup and delivery extraction functions
        pickup_address, pickup_date = extract_pickup_info(body)
        pickup_address = pickup_address or "Unknown Pickup"
        pickup_date = pickup_date or "ASAP"
        
        delivery_address, delivery_date = extract_delivery_info(body)
        delivery_address = delivery_address or "Unknown Delivery"
        delivery_date = delivery_date or "N/A"
        
        # Extract other information safely
        stops_miles = find(r'(\d+ STOPS?,\s*[0-9,]+ MILES)', "")
        estimated_miles_match = re.search(r'(\d{1,3}(?:,\d{3})*|\d+)\s*MILES', stops_miles)
        estimated_miles = estimated_miles_match.group(1) if estimated_miles_match else "N/A"
        
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

        # Limit string lengths to prevent memory issues
        def limit_length(text: str, max_len: int = 200) -> str:
            return text[:max_len] + "..." if len(text) > max_len else text

        message = f"""📦 **New Load Alert!**

📍 **Pick-up:** {limit_length(pickup_address)}
📅 **Pick-up date (EST):** {limit_length(pickup_date)}

🏁 **Deliver to:** {limit_length(delivery_address)}
📅 **Deliver date (EST):** {limit_length(delivery_date)}

🛣️ **Estimated Miles:** {estimated_miles}
🚚 **Suggested Truck Size:** {limit_length(truck_size)}
💰 **Posted Amount:** {limit_length(posted_amount)}

📦 **Pieces:** {limit_length(pieces)}
⚖️ **Weight:** {limit_length(weight)}
📏 **Dims:** {limit_length(dims)}
📚 **Stackable:** {stackable}

👨‍💼 **Broker:** {limit_length(broker_name)}
🏢 **Company:** {limit_length(broker_company)}
📞 **Phone:** {limit_length(broker_phone)}

📝 **Notes:** {limit_length(notes)}"""

        return message
    
    except Exception as e:
        logger.error(f"Error building formatted message: {e}")
        return f"❌ **Error processing email**: {str(e)[:100]}"

def send_telegram_message(message: str) -> bool:
    """Send message to Telegram with retry mechanism"""
    if not message or not TELEGRAM_BOT_TOKEN or not CHAT_ID:
        logger.error("Missing message, bot token, or chat ID")
        return False
    
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        
        # Limit message length for Telegram
        if len(message) > 4000:
            message = message[:4000] + "\n\n... (truncated)"
        
        data = {
            "chat_id": CHAT_ID,
            "text": message,
            "parse_mode": "Markdown"
        }
        
        # Set timeout and retry
        response = requests.post(url, data=data, timeout=30)
        logger.info(f"Telegram response: {response.status_code}")
        
        if response.status_code != 200:
            logger.error(f"Telegram API error: {response.text}")
        
        return response.status_code == 200
        
    except requests.exceptions.Timeout:
        logger.error("Telegram request timeout")
        return False
    except Exception as e:
        logger.error(f"Error sending Telegram message: {e}")
        return False

def load_credentials() -> Optional[Credentials]:
    """Load credentials from token.json with better error handling"""
    token_file = 'token.json'
    
    if not os.path.exists(token_file):
        logger.error(f"❌ {token_file} not found!")
        return None
    
    try:
        creds = Credentials.from_authorized_user_file(token_file, SCOPES)
        logger.info("✅ Successfully loaded credentials from token.json")
        
        # Refresh if needed
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                # Save the refreshed credentials
                with open(token_file, 'w') as token:
                    token.write(creds.to_json())
                logger.info("✅ Credentials refreshed")
            except Exception as refresh_error:
                logger.error(f"Error refreshing credentials: {refresh_error}")
                return None
        
        return creds
    except Exception as e:
        logger.error(f"❌ Error loading credentials: {e}")
        return None

def initialize_gmail_service() -> bool:
    """Initialize Gmail service with proper error handling"""
    global service
    
    logger.info("🚀 Attempting to load credentials...")
    creds = load_credentials()

    if not creds:
        logger.error("❌ Failed to load credentials")
        return False

    try:
        service = build('gmail', 'v1', credentials=creds)
        logger.info("✅ Gmail service created successfully")
        return True
    except Exception as e:
        logger.error(f"❌ Error building Gmail service: {e}")
        return False

# Initialize Gmail service at startup
if not initialize_gmail_service():
    print("❌ GMAIL SERVICE INITIALIZATION FAILED")
    sys.exit(1)

@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "status": "running",
        "version": "memory-safe-version",
        "message": "Gmail webhook with memory safety is running"
    })

@app.route("/test-gmail", methods=["GET"])
def test_gmail():
    try:
        if not service:
            return jsonify({"status": "error", "error": "Gmail service not initialized"}), 500
            
        profile = service.users().getProfile(userId='me').execute()
        return jsonify({
            "status": "success",
            "email": profile.get('emailAddress'),
            "message": "Gmail API is working!"
        })
    except Exception as e:
        logger.error(f"Gmail test error: {e}")
        return jsonify({
            "status": "error",
            "error": str(e)
        }), 500

@app.route("/test-latest-email", methods=["GET"])
def test_latest_email():
    """Test endpoint to process the latest email with memory safety"""
    try:
        if not service:
            return jsonify({"status": "error", "error": "Gmail service not initialized"}), 500
        
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
        
        if not body:
            return jsonify({
                "status": "error",
                "message": "Failed to extract email body"
            })
        
        # Build formatted message
        formatted_message = build_formatted_message(body)
        
        # Send to Telegram
        success = send_telegram_message(formatted_message)
        
        # Force garbage collection
        gc.collect()
        
        return jsonify({
            "status": "success" if success else "telegram_error",
            "message": "Email processed and sent to Telegram" if success else "Email processed but Telegram failed",
            "formatted_message": formatted_message,
            "body_preview": body[:500] + "..." if len(body) > 500 else body
        })
        
    except Exception as e:
        logger.error(f"Error in test_latest_email: {e}")
        gc.collect()  # Clean up on error
        return jsonify({
            "status": "error",
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500

@app.route("/gmail-notify", methods=["POST"])
def gmail_notify():
    """Handle Gmail webhook notifications with memory safety"""
    try:
        logger.info("📩 Gmail notification received")
        
        if not service:
            logger.error("Gmail service not initialized")
            return jsonify({"status": "error", "error": "Service not initialized"}), 500
        
        # Get notification data
        notification_data = request.get_json()
        if not notification_data:
            logger.error("No notification data received")
            return jsonify({"status": "error", "error": "No data"}), 400
        
        logger.info(f"Notification data: {notification_data}")
        
        # Process notification
        if 'message' in notification_data:
            message_data = notification_data['message']
            if 'data' in message_data:
                # Decode the message data safely
                decoded_data = safe_decode_base64(message_data['data'])
                if not decoded_data:
                    logger.error("Failed to decode pub/sub data")
                    return jsonify({"status": "error", "error": "Decode failed"}), 400
                
                try:
                    pub_sub_data = json.loads(decoded_data)
                    logger.info(f"Decoded pub/sub data: {pub_sub_data}")
                except json.JSONDecodeError as e:
                    logger.error(f"JSON decode error: {e}")
                    return jsonify({"status": "error", "error": "Invalid JSON"}), 400
                
                # Get the history ID to fetch new messages
                history_id = pub_sub_data.get('historyId')
                if history_id:
                    # Get recent messages from Sprinter label
                    results = service.users().messages().list(
                        userId='me', 
                        labelIds=['Label_1'],  # Sprinter label
                        maxResults=3  # Reduced from 5 to limit memory usage
                    ).execute()
                    messages = results.get('messages', [])
                    
                    processed_count = 0
                    for msg in messages:
                        if processed_count >= 1:  # Only process 1 email per notification
                            break
                            
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
                            
                            processed_count += 1
                            
                            # Force garbage collection after processing
                            gc.collect()
        
        return jsonify({"status": "processed"}), 200
        
    except Exception as e:
        logger.error(f"Error processing Gmail notification: {e}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        
        # Force garbage collection on error
        gc.collect()
        
        return jsonify({
            "status": "error",
            "error": str(e)[:200]  # Limit error message length
        }), 500

@app.errorhandler(413)
def request_entity_too_large(error):
    logger.error("Request entity too large")
    return jsonify({"status": "error", "error": "Request too large"}), 413

@app.errorhandler(500)
def internal_server_error(error):
    logger.error(f"Internal server error: {error}")
    gc.collect()  # Clean up on server error
    return jsonify({"status": "error", "error": "Internal server error"}), 500

if __name__ == "__main__":
    print("🎯 Starting Flask application...")
    logger.info("🚀 Memory Safe Gmail Webhook Starting")
    
    # Configure Flask for production-like settings
    app.run(
        host="0.0.0.0", 
        port=8080, 
        debug=False,
        threaded=True,
        use_reloader=False  # Disable reloader to prevent memory issues
    )
