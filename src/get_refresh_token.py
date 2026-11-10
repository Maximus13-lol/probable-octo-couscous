"""Однократное получение YT_REFRESH_TOKEN (запускать у себя на компьютере, не в Actions).

    pip install google-auth-oauthlib
    python -m src.get_refresh_token --client-id XXX --client-secret YYY

Откроется браузер → войдите в аккаунт с каналом → разрешите доступ → в консоли появится refresh token.
"""
from __future__ import annotations

import argparse

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.readonly"]


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--client-id", required=True)
    p.add_argument("--client-secret", required=True)
    p.add_argument("--port", type=int, default=8080)
    a = p.parse_args()
    flow = InstalledAppFlow.from_client_config(
        {"installed": {
            "client_id": a.client_id, "client_secret": a.client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }},
        SCOPES,
    )
    creds = flow.run_local_server(port=a.port, access_type="offline", prompt="consent")
    print("\n=== YT_REFRESH_TOKEN ===\n" + creds.refresh_token + "\n========================")
    print("Сохраните его в GitHub → Settings → Secrets and variables → Actions → YT_REFRESH_TOKEN")


if __name__ == "__main__":
    main()
