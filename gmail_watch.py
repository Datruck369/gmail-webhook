#!/usr/bin/env python3
"""
Enhanced SSL error handling for Gmail webhook
"""
import ssl
import socket
import time
import requests
import logging
from urllib3.util.retry import Retry
from requests.adapters import HTTPAdapter
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
import httplib2

logger = logging.getLogger(__name__)

# Enhanced SSL configuration
def create_ssl_context():
    """Create a more robust SSL context"""
    try:
        # Create default SSL context with stronger settings
        context = ssl.create_default_context()
        
        # More permissive SSL settings for problematic connections
        context.check_hostname = False
        context.verify_mode = ssl.CERT_REQUIRED
        
        # Set cipher suites (prefer strong ones)
        context.set_ciphers('ECDHE+AESGCM:ECDHE+CHACHA20:DHE+AESGCM:DHE+CHACHA20:!aNULL:!MD5:!DSS')
        
        # Set protocol versions
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.maximum_version = ssl.TLSVersion.TLSv1_3
        
        return context
    except Exception as e:
        logger.error(f"Error creating SSL context: {e}")
        return ssl.create_default_context()

def create_enhanced_session():
    """Create requests session with enhanced SSL handling"""
    session = requests.Session()
    
    # Enhanced retry strategy with longer backoff
    retry_strategy = Retry(
        total=5,  # Increased retries
        backoff_factor=2,  # Longer backoff
        status_forcelist=[429, 500, 502, 503, 504, 520, 521, 522, 523, 524],
        allowed_methods=["HEAD", "GET", "OPTIONS", "POST"],
        raise_on_status=False,
        respect_retry_after_header=True
    )
    
    # Custom HTTPAdapter with SSL settings
    class SSLAdapter(HTTPAdapter):
        def __init__(self, ssl_context=None, **kwargs):
            self.ssl_context = ssl_context or create_ssl_context()
            super().__init__(**kwargs)
            
        def init_poolmanager(self, *args, **kwargs):
            kwargs['ssl_context'] = self.ssl_context
            kwargs['socket_options'] = [
                (socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1),
                (socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 45),
                (socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 10),
                (socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 6)
            ]
            return super().init_poolmanager(*args, **kwargs)
    
    adapter = SSLAdapter(max_retries=retry_strategy)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    
    # Enhanced timeout settings
    session.timeout = (15, 45)  # (connect, read) - increased timeouts
    
    return session

def create_gmail_service_with_ssl_handling(credentials):
    """Create Gmail service with enhanced SSL handling"""
    try:
        # Create custom HTTP object with SSL settings
        http = httplib2.Http(timeout=30)
        
        # Configure SSL settings for httplib2
        http.ca_certs = None  # Use system certificates
        http.disable_ssl_certificate_validation = False
        
        # Build service with custom HTTP
        service = build('gmail', 'v1', 
                       credentials=credentials, 
                       http=http,
                       cache_discovery=False,
                       static_discovery=False)  # Disable static discovery
        
        return service
    except Exception as e:
        logger.error(f"Error creating Gmail service: {e}")
        return None

def extract_clean_body_with_circuit_breaker(service, message_id, max_retries=5):
    """Enhanced email extraction with circuit breaker pattern"""
    
    # Circuit breaker state
    failure_count = 0
    circuit_open = False
    last_failure_time = 0
    circuit_timeout = 60  # 1 minute timeout
    
    for attempt in range(max_retries):
        try:
            # Check circuit breaker
            current_time = time.time()
            if circuit_open:
                if current_time - last_failure_time < circuit_timeout:
                    logger.warning("Circuit breaker is open, skipping attempt")
                    time.sleep(5)
                    continue
                else:
                    logger.info("Circuit breaker timeout expired, attempting to close")
                    circuit_open = False
                    failure_count = 0
            
            # Progressive backoff with jitter
            if attempt > 0:
                backoff_time = min(2 ** attempt, 30) + (time.time() % 1)  # Add jitter
                logger.info(f"Retry attempt {attempt + 1} after {backoff_time:.2f}s")
                time.sleep(backoff_time)
            
            # Attempt to get message
            message = service.users().messages().get(
                userId='me', 
                id=message_id, 
                format='full'
            ).execute()
            
            if not message or 'payload' not in message:
                logger.warning("Empty message payload")
                return ""
            
            # Success - reset circuit breaker
            failure_count = 0
            circuit_open = False
            
            # Extract text from payload (your existing logic)
            payload = message['payload']
            return extract_text_from_payload(payload)
            
        except (ssl.SSLError, socket.error, ConnectionError) as ssl_error:
            failure_count += 1
            last_failure_time = time.time()
            
            logger.error(f"SSL/Connection error (attempt {attempt + 1}, failures: {failure_count}): {ssl_error}")
            
            # Open circuit breaker after 3 consecutive failures
            if failure_count >= 3:
                circuit_open = True
                logger.error("Circuit breaker opened due to repeated SSL failures")
            
            # Different handling based on SSL error type
            if "DECRYPTION_FAILED_OR_BAD_RECORD_MAC" in str(ssl_error):
                logger.error("MAC verification failure - possible network interference")
                # Longer wait for MAC failures
                time.sleep(5 * (attempt + 1))
            elif "WRONG_VERSION_NUMBER" in str(ssl_error):
                logger.error("SSL version mismatch")
                time.sleep(2 * (attempt + 1))
            
            if attempt == max_retries - 1:
                logger.error(f"Failed after {max_retries} attempts, giving up")
                return ""
                
        except Exception as e:
            logger.error(f"Unexpected error in email extraction: {e}")
            return ""
    
    return ""

def send_telegram_with_ssl_retry(message, bot_token, chat_id, max_retries=5):
    """Enhanced Telegram sending with SSL retry logic"""
    
    for attempt in range(max_retries):
        session = None
        try:
            if attempt > 0:
                # Progressive backoff with randomization
                wait_time = min(2 ** attempt, 30) + (time.time() % 2)
                logger.info(f"Telegram retry attempt {attempt + 1} after {wait_time:.2f}s")
                time.sleep(wait_time)
            
            # Create fresh session for each attempt
            session = create_enhanced_session()
            
            url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
            
            # Limit message length
            if len(message) > 4000:
                message = message[:4000] + "\n\n... (truncated)"
            
            data = {
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "Markdown"
            }
            
            # Make request with enhanced error handling
            response = session.post(url, data=data, timeout=(15, 45))
            
            if response.status_code == 200:
                logger.info("Telegram message sent successfully")
                return True
            elif response.status_code == 429:
                # Rate limited - respect retry-after header
                retry_after = int(response.headers.get('Retry-After', 30))
                logger.warning(f"Rate limited, waiting {retry_after}s")
                time.sleep(retry_after)
                continue
            elif response.status_code in [500, 502, 503, 504]:
                logger.warning(f"Server error {response.status_code}, will retry")
                continue
            else:
                logger.error(f"Telegram API error: {response.status_code} - {response.text}")
                return False
                
        except (ssl.SSLError, socket.error, ConnectionError) as conn_error:
            logger.error(f"Connection error sending to Telegram (attempt {attempt + 1}): {conn_error}")
            
            # Specific handling for different SSL errors
            if "DECRYPTION_FAILED_OR_BAD_RECORD_MAC" in str(conn_error):
                logger.error("MAC failure in Telegram request - network issue")
                time.sleep(10)  # Longer wait for MAC failures
            
            if attempt == max_retries - 1:
                logger.error("Failed to send Telegram message after all retries")
                return False
                
        except requests.exceptions.Timeout:
            logger.error(f"Telegram request timeout (attempt {attempt + 1})")
            continue
            
        except Exception as e:
            logger.error(f"Unexpected error sending to Telegram: {e}")
            return False
            
        finally:
            if session:
                session.close()
    
    return False

def extract_text_from_payload(payload):
    """Your existing text extraction logic - keeping it as is"""
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
                        try:
                            from bs4 import BeautifulSoup
                            soup = BeautifulSoup(decoded, 'html.parser')
                            body = soup.get_text()
                            soup.decompose()
                            del soup
                        except Exception as soup_error:
                            logger.error(f"BeautifulSoup error: {soup_error}")
                            body = decoded
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
                        from bs4 import BeautifulSoup
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

def safe_decode_base64(data):
    """Your existing base64 decode function"""
    import base64
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

# Health check function
def check_ssl_connectivity():
    """Check SSL connectivity to Google services"""
    try:
        session = create_enhanced_session()
        response = session.get('https://www.googleapis.com/discovery/v1/apis/gmail/v1/rest', timeout=10)
        session.close()
        return response.status_code == 200
    except Exception as e:
        logger.error(f"SSL connectivity check failed: {e}")
        return False
