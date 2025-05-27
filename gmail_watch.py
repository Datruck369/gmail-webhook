import os
import pickle
import google.auth.transport.requests
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# Required Gmail API scope
SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]

# Google Cloud project and Pub/Sub topic
PROJECT_ID = "gmaildispatcher-460517"
TOPIC_NAME = "projects/gmaildispatcher-460517/topics/gmail-topic"

# Your Gmail label ID for "SPRINTER"
SPRINTER_LABEL_ID = "Label_962352309899224093"

def main():
    creds = None

    # Load saved credentials
    if os.path.exists("token.pkl"):
        with open("token.pkl", "rb") as token_file:
            creds = pickle.load(token_file)

    # Refresh or start new OAuth flow
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(google.auth.transport.requests.Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                "client_secret.json", SCOPES
            )
            creds = flow.run_local_server(port=8080)

        with open("token.pkl", "wb") as token_file:
            pickle.dump(creds, token_file)

    # Create Gmail API client
    service = build("gmail", "v1", credentials=creds)

    # SPRINTER label only
    request = {
        "labelIds": [SPRINTER_LABEL_ID],
        "topicName": TOPIC_NAME
    }

    try:
        response = service.users().watch(userId="me", body=request).execute()
        print("🔔 Gmail watch started:", response)
    except Exception as e:
        print("❌ Error starting Gmail watch:")
        print(e)

if __name__ == "__main__":
    main()
