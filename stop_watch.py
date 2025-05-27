from googleapiclient.discovery import build
import pickle

with open("token.pkl", "rb") as token_file:
    creds = pickle.load(token_file)

service = build("gmail", "v1", credentials=creds)
service.users().stop(userId="me").execute()
print("🛑 Gmail watch stopped.")
