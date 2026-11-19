"""Однократное получение YT_REFRESH_TOKEN (запускать у себя на компьютере, не в Actions).

    pip install google-auth-oauthlib
    python -m src.get_refresh_token --client-id XXX --client-secret YYY

Откроется браузер → войдите в аккаунт с каналом → разрешите доступ → в консоли появится refresh token.

Без своего компьютера (например, в Google Cloud Shell) — флаг --manual: скрипт напечатает ссылку, вы
разрешаете доступ в браузере, браузер уйдёт на «http://localhost…» (страница не откроется — это нормально),
копируете ВЕСЬ адрес из адресной строки и вставляете его в консоль.
"""
from __future__ import annotations

import argparse

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/youtube",                 # удаление роликов
    "https://www.googleapis.com/auth/youtube.force-ssl",       # комментарий канала с вопросом зрителю
    "https://www.googleapis.com/auth/yt-analytics.readonly",   # удержание и досмотры в статистике
]


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--client-id", required=True)
    p.add_argument("--client-secret", required=True)
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--manual", action="store_true", help="без локального браузера: вставить адрес вручную")
    a = p.parse_args()
    if not a.client_id.endswith(".apps.googleusercontent.com"):
        raise SystemExit("Client ID должен заканчиваться на .apps.googleusercontent.com — подставьте свой из "
                         "Google Cloud → APIs & Services → Credentials (вместо ВАШ_ID).")
    if len(a.client_secret) < 10 or not a.client_secret.isascii():
        raise SystemExit("Client secret не похож на настоящий — подставьте свой (обычно начинается с GOCSPX-).")
    flow = InstalledAppFlow.from_client_config(
        {"installed": {
            "client_id": a.client_id, "client_secret": a.client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }},
        SCOPES,
    )
    if a.manual:
        import os
        os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"  # редирект на http://localhost — только для этой выдачи
        flow.redirect_uri = f"http://localhost:{a.port}/"
        url, _ = flow.authorization_url(access_type="offline", prompt="consent")
        print("\n1) Откройте ссылку в браузере и разрешите ВСЕ доступы:\n\n" + url)
        print("\n2) Браузер перейдёт на http://localhost... и покажет ошибку — это нормально.")
        resp = input("3) Скопируйте ВЕСЬ адрес из адресной строки и вставьте сюда, затем Enter:\n> ").strip()
        if not resp.startswith("http://localhost"):
            raise SystemExit("Нужен адрес, который начинается с http://localhost (после «Продолжить» в Google). "
                             "Если Google показал ошибку — проверьте Client ID и секрет и запустите команду заново.")
        flow.fetch_token(authorization_response=resp)
        creds = flow.credentials
    else:
        creds = flow.run_local_server(port=a.port, access_type="offline", prompt="consent")
    print("\n=== YT_REFRESH_TOKEN ===\n" + creds.refresh_token + "\n========================")
    print("Сохраните его в GitHub → Settings → Secrets and variables → Actions → YT_REFRESH_TOKEN")


if __name__ == "__main__":
    main()
