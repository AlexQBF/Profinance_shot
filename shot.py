"""
Открывает https://www.profinance.ru/gold/, делает скриншот и отправляет его в Telegram.
Запускается из GitHub Actions (.github/workflows/screenshot.yml).
"""

import json
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
WAIT_MSG_ID = os.environ.get("WAIT_MSG_ID", "").strip()  # сообщение «Делаю скриншот…», удалим после отправки

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

# Кнопка под полем ввода — прикладываем к каждому сообщению, иначе Telegram её прячет
KEYBOARD = json.dumps({
    "keyboard": [[{"text": "📸 Скриншот"}]],
    "resize_keyboard": True,
    "is_persistent": True,
})
T0 = time.time()


def log(msg: str) -> None:
    print(f"[{time.time() - T0:5.1f}s] {msg}", flush=True)


TABLE_READY_JS = """() => [...document.querySelectorAll('tr')].some(
    (tr) => /Palladium/.test(tr.innerText || '') && /\\d/.test(tr.innerText || ''))"""


CHART_READY_JS = """() => (function chartReady() {
  const sel = [...document.querySelectorAll("select")].find((s) =>
    [...s.options].some((o) => (o.text || "").trim() === "1Min")) ||
    [...document.querySelectorAll("body *")].find((el) =>
      el.children.length === 0 && (el.textContent || "").trim() === "1Min");
  let box = sel;
  while (box && box.getBoundingClientRect().height < 400) box = box.parentElement;
  if (!box) return true; // не нашли блок графика — не ждём
  const els = [...box.querySelectorAll("canvas, img, svg, iframe")].filter((e) => {
    const r = e.getBoundingClientRect();
    return r.width > 300 && r.height > 200;
  });
  for (const e of els) {
    if (e.tagName === "IMG") { if (e.complete && e.naturalWidth > 0) return true; continue; }
    if (e.tagName === "CANVAS") {
      try {
        const ctx = e.getContext("2d");
        if (!ctx) return true;
        const d = ctx.getImageData(0, 0, e.width, e.height).data;
        let n = 0;
        for (let i = 0; i < d.length; i += 4 * 97) {
          if (d[i + 3] > 0 && (d[i] < 200 || d[i + 1] < 200 || d[i + 2] < 200)) n++;
          if (n > 50) return true;
        }
      } catch (err) { return true; }
      continue;
    }
    return true;
  }
  return false;
})()"""


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

        # ждём, пока таблица цен заполнится цифрами; если нет — перезагружаем страницу
        for attempt in range(1, 4):
            if attempt == 1:
                page.goto(PAGE_URL, wait_until="domcontentloaded", timeout=45_000)
            else:
                log(f"таблица не загрузилась, перезагружаю (попытка {attempt})")
                page.reload(wait_until="domcontentloaded", timeout=45_000)
            log("страница открыта")
            try:
                page.wait_for_function(TABLE_READY_JS, timeout=15_000, polling=300)
                page.wait_for_function(CHART_READY_JS, timeout=10_000, polling=300)
                break
            except Exception:
                pass
        try:
            page.wait_for_load_state("networkidle", timeout=4_000)
        except Exception:
            pass
        page.wait_for_timeout(700)
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
    requests.post(f"{API}/sendMessage", data={"chat_id": CHAT_ID, "text": text, "reply_markup": KEYBOARD}, timeout=30)


def delete_wait_message() -> None:
    if WAIT_MSG_ID.isdigit():
        try:
            requests.post(f"{API}/deleteMessage",
                          data={"chat_id": CHAT_ID, "message_id": WAIT_MSG_ID}, timeout=15)
        except Exception:
            pass


def main() -> None:
    full_page = MODE == "full"
    try:
        png = take_screenshot(full_page)
    except Exception as e:
        delete_wait_message()
        send_text(f"Не получилось сделать скриншот: {e}")
        raise

    now = datetime.now(ZoneInfo("Europe/Moscow")).strftime("%d.%m.%Y %H:%M")
    caption = f"profinance.ru/gold · {now} МСК"
    method, field = ("sendDocument", "document") if full_page else ("sendPhoto", "photo")
    r = requests.post(
        f"{API}/{method}",
        data={"chat_id": CHAT_ID, "caption": caption, "reply_markup": KEYBOARD},
        files={field: ("profinance.png", png, "image/png")},
        timeout=60,
    )
    if not r.ok:
        print(r.text, file=sys.stderr)
        r.raise_for_status()
    log("отправлено в Telegram")
    delete_wait_message()


if __name__ == "__main__":
    main()
