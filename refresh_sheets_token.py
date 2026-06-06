"""
Google Calendar + Sheets 両方のトークンを一度に再取得するスクリプト
実行するとURLが表示されるので、ブラウザで開いてGoogleアカウントでログインしてください。
取得した値を GOOGLE_CREDENTIALS に設定すればカレンダーもスプレッドシートも動きます。
"""
import json
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/spreadsheets",
]
TOKEN_FILE = "token_combined.json"
CLIENT_SECRET = "client_secret.json"

flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET, SCOPES)
creds = flow.run_local_server(port=0, open_browser=False)

with open(TOKEN_FILE, "w") as f:
    f.write(creds.to_json())

print("✅ トークン更新完了！")
print()

data = json.loads(creds.to_json())
minimal = {
    "client_id":     data["client_id"],
    "client_secret": data["client_secret"],
    "refresh_token": data["refresh_token"],
    "token_uri":     data.get("token_uri", "https://oauth2.googleapis.com/token"),
    "scopes":        data.get("scopes", SCOPES),
}
print("=== Renderの GOOGLE_CREDENTIALS に貼り付ける値 ===")
print(json.dumps(minimal))
