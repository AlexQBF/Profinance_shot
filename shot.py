"""
Открывает https://www.profinance.ru/gold/, делает скриншот и отправляет его в Telegram.
Запускается из GitHub Actions (.github/workflows/screenshot.yml).
"""

import os
import subprocess
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from playwright.sync_api import sync_playwright

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
MODE = os.environ.get("MODE", "shot").strip().lower()

PAGE_URL = "https://www.profinance.ru/gold/"
VW, VH = 1400, 1000      # размер окна браузера
SCALE = 1.5              # чёткость: 1 — обычная, 2 — как на Retina
BLOCK_ADS = True         # не загружать рекламу: быстрее и чище картинка

# Где заканчивается кадр (первое найденное сверху вниз):
END_MARKERS = ["Получить онлайн цены", "PAXG/USDT", "Palladium"]

AD_HOSTS = (
    "an.yandex.ru", "yandex.ru/ads", "yastatic.net/partner", "adfox", "mc.yandex",
    "googlesyndication", "doubleclick", "googletagmanager", "google-analytics",
    "top-fwz1.mail.ru", "mail.ru/counter", "ad.mail.ru", "adriver", "criteo",
    "smi2", "relap", "mediametrics", "24smi", "yadro.ru", "liveinternet",
)

API = f"https://api.telegram.org/bot{TOKEN}"
T0 = time.time()


def log(msg: str) -> None:
    print(f"[{time.time() - T0:5.1f}s] {msg}", flush=True)


HIDE_COOKIE_AND_FIXED_JS = """() => {
    // согласиться с cookie, если есть кнопка
    for (const el of document.querySelectorAll('button, a, div, span')) {
        const t = (el.textContent || '').trim();
        if (t === 'Я согласен' || t === 'Согласен' || t === 'Принять') {
            let n = el, near = false;
            for (let k = 0; k < 6 && n; k++, n = n.parentElement) {
                const s = (n.innerText || '').toLowerCase();
                if (s.includes('cookie') || s.includes('куки')) { near = true; break; }
            }
            if (near) { try { el.click(); } catch (e) {} }
        }
    }
    // убрать всё «прилипающее» к экрану (cookie-плашка, нижние рекламные полосы),
    // кроме шапки сайта с меню
    for (const el of document.querySelectorAll('body *')) {
        const st = getComputedStyle(el);
        if (st.position !== 'fixed' && st.position !== 'sticky') continue;
        if ((el.innerText || '').includes('Markets')) { el.style.position = 'static'; continue; }
        el.style.setProperty('display', 'none', 'important');
    }
}"""


FIND_CLIP_JS = """(endMarkers) => {
    const vis = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
    const all = [...document.querySelectorAll('body *')].filter(vis);
    const leaf = (t) => all.filter((el) => el.children.length === 0 && (el.textContent || '').trim() === t);
    const R = (el) => { const r = el.getBoundingClientRect();
        return { left: r.left + scrollX, right: r.right + scrollX, top: r.top + scrollY, bottom: r.bottom + scrollY }; };

    // таблица с ценами — по строке «Gold»
    const goldCells = leaf('Gold').filter((el) => el.closest('tr'));
    const priceTable = goldCells.length ? goldCells[goldCells.length - 1].closest('table') : null;
    if (!priceTable) return null;
    const tbl = R(priceTable);

    // шапка: поднимаемся от пункта меню «Markets», пока блок не дотянется до левого края контента
    const anchor = leaf('Markets')[0];
    if (!anchor) return null;
    let header = anchor;
    while (header.parentElement && header !== document.body && R(header).left > tbl.left + 4) {
        header = header.parentElement;
    }
    const hdr = R(header);

    // низ кадра
    let bottom = null;
    for (const m of endMarkers) {
        const cands = all.filter((el) => (el.innerText || '').trim().startsWith(m) && R(el).top > tbl.top);
        if (cands.length) {
            cands.sort((a, b) => R(a).bottom - R(b).bottom);   // самый маленький блок
            const el = cands[0].closest('tr') || cands[0];
            bottom = R(el).bottom;
            break;
        }
    }
    if (bottom === null) bottom = tbl.bottom;

    const x = Math.max(0, Math.min(hdr.left, tbl.left) - 6);
    const right = Math.min(document.documentElement.scrollWidth, Math.max(hdr.right, tbl.right) + 6);
    const y = Math.max(0, hdr.top);
    return { x, y, width: right - x, height: bottom - y + 12 };
}"""


def launch(p):
    # Google Chrome уже установлен на машинах GitHub — не тратим время на скачивание
    try:
        return p.chromium.launch(channel="chrome", headless=True)
    except Exception:
        log("Chrome не найден, ставлю Chromium")
        subprocess.run([sys.executable, "-m", "playwright", "install", "--with-deps", "chromium"], check=True)
        return p.chromium.launch(headless=True)


def take_screenshot(full_page: bool) -> bytes:
    with sync_playwright() as p:
        browser = launch(p)
        log("браузер запущен")
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
        if BLOCK_ADS:
            page.route(
                "**/*",
                lambda route: route.abort()
                if any(h in route.request.url for h in AD_HOSTS)
                else route.continue_(),
            )

        page.goto(PAGE_URL, wait_until="domcontentloaded", timeout=45_000)
        log("страница открыта")
        # ждём, пока появятся цены в таблице, а потом немного — чтобы дорисовался график
        try:
            page.get_by_text("Palladium", exact=True).first.wait_for(timeout=15_000)
        except Exception:
            log("таблица не появилась за 15 с")
        try:
            page.wait_for_load_state("networkidle", timeout=4_000)
        except Exception:
            pass
        page.wait_for_timeout(1_500)
        page.evaluate(HIDE_COOKIE_AND_FIXED_JS)
        page.wait_for_timeout(300)
        log("страница готова")

        clip = None if full_page else page.evaluate(FIND_CLIP_JS, END_MARKERS)
        log(f"кадр: {clip}")
        if clip and clip["width"] > 200 and clip["height"] > 200:
            png = page.screenshot(full_page=True, clip=clip, type="png")
        else:
            png = page.screenshot(full_page=full_page, type="png")
        browser.close()
        log("скриншот сделан")
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
    method, field = ("sendDocument", "document") if full_page else ("sendPhoto", "photo")
    r = requests.post(
        f"{API}/{method}",
        data={"chat_id": CHAT_ID, "caption": caption},
        files={field: ("profinance.png", png, "image/png")},
        timeout=60,
    )
    if not r.ok:
        print(r.text, file=sys.stderr)
        r.raise_for_status()
    log("отправлено в Telegram")


if __name__ == "__main__":
    main()
