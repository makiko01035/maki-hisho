# Googleカレンダーの認証情報を取得するスクリプト
# 最初に一度だけ実行してください

import json
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']

def main():
    flow = InstalledAppFlow.from_client_secrets_file(
        'client_secret.json',
        SCOPES
    )
    creds = flow.run_local_server(port=0)

    creds_data = {
        'token': creds.token,
        'refresh_token': creds.refresh_token,
        'client_id': creds.client_id,
        'client_secret': creds.client_secret,
        'scopes': list(creds.scopes),
    }

    print("\n=== 以下をコピーしてください（GOOGLE_CREDENTIALS に設定します）===\n")
    print(json.dumps(creds_data, ensure_ascii=False))
    print("\n=== ここまで ===")

if __name__ == '__main__':
    main()
