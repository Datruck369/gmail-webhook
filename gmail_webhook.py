#!/usr/bin/env python3
"""
Debug version to identify token.pkl issue
"""
import os
import sys
import traceback

print("="*50)
print("🚀 DEBUGGING GMAIL WEBHOOK - NEW VERSION")
print("="*50)

print(f"Python version: {sys.version}")
print(f"Current working directory: {os.getcwd()}")
print(f"Script location: {__file__}")

# List all files
print("\n📁 Files in current directory:")
for file in os.listdir('.'):
    print(f"  - {file}")

# Check for token files specifically
token_files = ['token.json', 'token.pkl', 'credentials.json']
print("\n🔑 Token file check:")
for file in token_files:
    exists = os.path.exists(file)
    print(f"  - {file}: {'✅ EXISTS' if exists else '❌ NOT FOUND'}")

# Search for any token.pkl references in the current file
print("\n🔍 Checking current file for token.pkl references:")
try:
    with open(__file__, 'r') as f:
        content = f.read()
        if 'token.pkl' in content.lower():
            print("❌ FOUND token.pkl reference in current file!")
            lines = content.split('\n')
            for i, line in enumerate(lines, 1):
                if 'token.pkl' in line.lower():
                    print(f"  Line {i}: {line.strip()}")
        else:
            print("✅ No token.pkl references found in current file")
except Exception as e:
    print(f"❌ Error reading current file: {e}")

print("\n" + "="*50)
print("STARTING ACTUAL APPLICATION")
print("="*50)

try:
    import json
    import logging
    from flask import Flask, request, jsonify
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    
    # Setup logging
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    logger = logging.getLogger(__name__)
    
    app = Flask(__name__)
    
    SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']
    TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
    CHAT_ID = os.environ.get('CHAT_ID')
    
    print(f"🤖 Telegram token configured: {'YES' if TELEGRAM_BOT_TOKEN else 'NO'}")
    print(f"💬 Chat ID configured: {'YES' if CHAT_ID else 'NO'}")
    
    def load_credentials_debug():
        """Load credentials with extensive debugging"""
        logger.info("🔍 ENTERING load_credentials_debug function")
        
        # Double-check we're not looking for token.pkl
        logger.info("📋 Files check inside function:")
        for file in ['token.json', 'token.pkl']:
            exists = os.path.exists(file)
            logger.info(f"  {file}: {'EXISTS' if exists else 'NOT FOUND'}")
        
        token_file = 'token.json'
        logger.info(f"🎯 Will attempt to load: {token_file}")
        
        if not os.path.exists(token_file):
            logger.error(f"❌ {token_file} not found!")
            logger.error(f"📁 Current directory: {os.getcwd()}")
            logger.error(f"📋 Available files: {os.listdir('.')}")
            return None
        
        try:
            logger.info(f"📖 Reading file: {token_file}")
            creds = Credentials.from_authorized_user_file(token_file, SCOPES)
            logger.info("✅ Successfully loaded credentials from token.json")
            return creds
        except Exception as e:
            logger.error(f"❌ Error loading credentials: {e}")
            logger.error(f"❌ Exception type: {type(e)}")
            logger.error(f"❌ Traceback: {traceback.format_exc()}")
            return None
    
    # Try to load credentials
    logger.info("🚀 Attempting to load credentials...")
    creds = load_credentials_debug()
    
    if not creds:
        logger.error("❌ Failed to load credentials")
        print("❌ CREDENTIALS LOADING FAILED")
        sys.exit(1)
    
    # Initialize Gmail service
    logger.info("🔧 Building Gmail service...")
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
            "version": "debug-version",
            "message": "Gmail webhook debug version is running",
            "files_found": os.listdir('.'),
            "token_json_exists": os.path.exists('token.json'),
            "token_pkl_exists": os.path.exists('token.pkl')
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
    
    @app.route("/gmail-notify", methods=["POST"])
    def gmail_notify():
        logger.info("📩 Gmail notification received")
        return jsonify({"status": "received"}), 200
    
    if __name__ == "__main__":
        print("🎯 Starting Flask application...")
        logger.info("🚀 Debug Gmail Webhook Starting")
        app.run(host="0.0.0.0", port=8080, debug=False)

except Exception as e:
    print(f"❌ CRITICAL ERROR: {e}")
    print(f"❌ Exception type: {type(e)}")
    print(f"❌ Traceback:")
    traceback.print_exc()
    sys.exit(1)
