"""
Открывает главную profinance.ru, делает скриншот и отправляет его в Telegram.
Запускается из GitHub Actions (.github/workflows/screenshot.yml).

Переменные окружения:
  TELEGRAM_BOT_TOKEN — токен бота
  CHAT_ID            — куда отправить скриншот
  MODE               — shot (экран) или full (вся страница)
  PAGE_URL           — адрес страницы (по умолчанию https://www.profinance.ru/)
  VIEWPORT           — размер окна, например 1440x900
"""

import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from playwright.sync_api import sync_playwright

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
MODE = os.environ.get("MODE", "shot").strip().lower()
PAGE_URL = os.environ.get("PAGE_URL") or "https://www.profinance.ru/"
VW, VH = (int(v) for v in (os.environ.get("VIEWPORT") or "1440x900").lower().split("x"))

API = f"https://api.telegram.org/bot{TOKEN}"


def take_screenshot(full_page: bool) -> bytes:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(
            viewport={"width": VW, "height": VH},
            locale="ru-RU",
            timezone_id="Europe/Moscow",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
            ),
        )
        page.goto(PAGE_URL, wait_until="domcontentloaded", timeout=60_000)
        # даём подгрузиться котировкам и графикам
        try:
            page.wait_for_load_state("networkidle", timeout=15_000)
        except Exception:
            pass  # страница с живыми котировками может не «успокоиться» — это нормально
        page.wait_for_timeout(3_000)
        png = page.screenshot(full_page=full_page, type="png")
        browser.close()
        return png


def send_text(text: str) -> None:
    requests.post(f"{API}/sendMessage", data={"chat_id": CHAT_ID, "text": text}, timeout=30)


def main() -> None:
    full_page = MODE == "full"
    try:
        png = take_screenshot(full_page)
    except Exception as e:
        send_text(f"Не получилось сделать скриншот: {e}")
        raise

    now = datetime.now(ZoneInfo("Europe/Moscow")).strftime("%d.%m.%Y %H:%M")
    caption = f"profinance.ru · {now} МСК"
    files_field = "document" if full_page else "photo"  # длинную страницу Telegram сожмёт как фото
    method = "sendDocument" if full_page else "sendPhoto"
    r = requests.post(
        f"{API}/{method}",
        data={"chat_id": CHAT_ID, "caption": caption},
        files={files_field: ("profinance.png", png, "image/png")},
        timeout=60,
    )
    if not r.ok:
        print(r.text, file=sys.stderr)
        r.raise_for_status()


if __name__ == "__main__":
    main()
