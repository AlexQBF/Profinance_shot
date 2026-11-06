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


COOKIE_WORDS = ("cookie", "куки", "файлы cookie", "персональных данных")
ACCEPT_TEXTS = (
    "Принять", "Принимаю", "Согласен", "Согласна", "Согласиться", "Я согласен",
    "Хорошо", "Понятно", "Ок", "OK", "Accept", "Accept all", "Got it", "Закрыть",
)


def close_cookie_banner(page) -> None:
    """Соглашается с cookie-баннером, а если не вышло — прячет его."""
    # 1. Нажать кнопку согласия рядом с текстом про cookie
    for frame in page.frames:
        for text in ACCEPT_TEXTS:
            try:
                btn = frame.get_by_role("button", name=text, exact=True)
                if btn.count() == 0:
                    btn = frame.locator(
                        f"a:text-is('{text}'), div:text-is('{text}'), span:text-is('{text}')"
                    )
                for i in range(btn.count()):
                    el = btn.nth(i)
                    if not el.is_visible():
                        continue
                    # кнопка должна быть в блоке, где говорится про cookie
                    near = el.evaluate(
                        """(el) => {
                            let n = el;
                            for (let k = 0; k < 6 && n; k++, n = n.parentElement) {
                                const t = (n.innerText || '').toLowerCase();
                                if (t.includes('cookie') || t.includes('куки')) return true;
                            }
                            return false;
                        }"""
                    )
                    if near:
                        el.click(timeout=3_000)
                        page.wait_for_timeout(700)
                        return
            except Exception:
                continue

    # 2. Запасной вариант: спрятать закреплённые блоки, где упоминаются cookie
    page.evaluate(
        """(words) => {
            for (const el of document.querySelectorAll('body *')) {
                const st = getComputedStyle(el);
                if (st.position !== 'fixed' && st.position !== 'sticky') continue;
                const t = (el.innerText || '').toLowerCase();
                if (t.length < 2000 && words.some(w => t.includes(w))) {
                    el.style.setProperty('display', 'none', 'important');
                }
            }
        }""",
        list(COOKIE_WORDS),
    )
    page.wait_for_timeout(300)


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
        close_cookie_banner(page)
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
