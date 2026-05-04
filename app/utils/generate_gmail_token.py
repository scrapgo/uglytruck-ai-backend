from google_auth_oauthlib.flow import InstalledAppFlow

# Gmail send mail scope
SCOPES = ["https://www.googleapis.com/auth/gmail.send"]

flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
creds = flow.run_local_server(port=8080)  # opens browser for consent

with open("google_token.json", "w") as token:
    token.write(creds.to_json())

print("✅ Token generated and saved as google_token.json")
