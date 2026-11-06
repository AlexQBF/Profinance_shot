"""
Открывает https://www.profinance.ru/gold/, делает скриншот и отправляет его в Telegram.
Запускается из GitHub Actions (.github/workflows/screenshot.yml).

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
PAGE_URL = "https://www.profinance.ru/gold/"
# Ширина окна браузера: чем больше, тем мельче выглядит страница (как уменьшение масштаба)
VW, VH = 1600, 1000

# Кадр для /shot: от шапки ProFinance до строки таблицы с этим названием (включительно).
# Чтобы захватить все таблицы ниже — поставьте "PAXG/USDT".
CLIP_UNTIL = "Palladium"
# Чёткость картинки (1 — обычная, 2 — как на Retina)
SCALE = 1.25

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


FIND_CLIP_JS = """(untilText) => {
    const vis = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
    const all = [...document.querySelectorAll('body *')];
    const leaf = (t) => all.filter((el) => vis(el) && el.children.length === 0
                                     && (el.textContent || '').trim() === t);
    const wideAncestor = (el, share) => {
        let n = el;
        while (n && n !== document.body) {
            if (n.getBoundingClientRect().width >= innerWidth * share) return n;
            n = n.parentElement;
        }
        return null;
    };

    // шапка: строка меню с пунктом «Markets» (или логотип ProFinance)
    const anchor = leaf('Markets')[0] || leaf('ProFinance')[0];
    if (!anchor) return null;
    const bar = wideAncestor(anchor, 0.6);
    if (!bar) return null;
    const b = bar.getBoundingClientRect();
    const top = b.top + scrollY;

    // низ: строка таблицы с нужным названием
    const cell = leaf(untilText).find((el) => el.getBoundingClientRect().top + scrollY > top + 200);
    if (!cell) return null;
    const row = cell.closest('tr') || wideAncestor(cell, 0.4) || cell;
    const bottom = row.getBoundingClientRect().bottom + scrollY;

    const x = Math.max(0, b.left + scrollX);
    const right = Math.min(document.documentElement.scrollWidth, b.right + scrollX);
    return { x, y: Math.max(0, top - 4), width: right - x, height: bottom - top + 16 };
}"""


def take_screenshot(full_page: bool) -> bytes:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(
            device_scale_factor=SCALE,
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
        # прокрутить вниз и обратно, чтобы подгрузились график и таблицы
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(1_500)
        page.evaluate("window.scrollTo(0, 0)")
        page.wait_for_timeout(4_000)
        close_cookie_banner(page)

        clip = None if full_page else page.evaluate(FIND_CLIP_JS, CLIP_UNTIL)
        if clip and clip["width"] > 100 and clip["height"] > 100:
            png = page.screenshot(full_page=True, clip=clip, type="png")
        else:
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
    caption = f"profinance.ru/gold · {now} МСК"
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
