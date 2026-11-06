/**
 * Telegram-бот: скриншот https://www.profinance.ru/gold/ по кнопке.
 *
 * Быстрый путь: браузер Cloudflare (Browser Rendering) прямо внутри Worker'а, 5–10 с.
 * Запасной путь: если браузер Cloudflare недоступен или кончился дневной лимит
 * бесплатного тарифа — запускаем старую схему через GitHub Actions (20–30 с).
 *
 * Переменные: TELEGRAM_BOT_TOKEN, GITHUB_TOKEN, WEBHOOK_SECRET (секреты),
 * GITHUB_REPO, ALLOWED_CHAT_IDS (пусто = доступно всем) — в wrangler.toml.
 * Привязка браузера: BROWSER (в wrangler.toml).
 */

import puppeteer from "@cloudflare/puppeteer";

const PAGE_URL = "https://www.profinance.ru/gold/";
const VIEWPORT = { width: 1400, height: 1000, deviceScaleFactor: 1.5 };
const END_MARKERS = ["Получить онлайн цены", "PAXG/USDT", "Palladium"];
const AD_HOSTS = [
  "an.yandex.ru", "yandex.ru/ads", "yastatic.net/partner", "adfox", "mc.yandex",
  "googlesyndication", "doubleclick", "googletagmanager", "google-analytics",
  "top-fwz1.mail.ru", "mail.ru/counter", "ad.mail.ru", "adriver", "criteo",
  "smi2", "relap", "mediametrics", "24smi", "yadro.ru", "liveinternet",
];

const BTN_SHOT = "📸 Скриншот";
const KEYBOARD = {
  keyboard: [[{ text: BTN_SHOT }]],
  resize_keyboard: true,
  is_persistent: true,
};
const WELCOME =
  "Привет! Я присылаю скриншот страницы золота с profinance.ru\n\n" +
  `Нажми ${BTN_SHOT}, пришлю график и таблицу цен\n\n` +
  "Картинка приходит примерно через 10 секунд";

const COOLDOWN_MS = 20_000; // не чаще раза в 20 секунд на человека
const lastRequest = new Map();

export default {
  async fetch(request, env, ctx) {
    if (request.method !== "POST") return new Response("ok");
    if (request.headers.get("X-Telegram-Bot-Api-Secret-Token") !== env.WEBHOOK_SECRET) {
      return new Response("forbidden", { status: 403 });
    }

    let update;
    try {
      update = await request.json();
    } catch {
      return new Response("ok");
    }
    const msg = update.message;
    if (!msg || typeof msg.text !== "string") return new Response("ok");

    const chatId = String(msg.chat.id);
    const allowed = (env.ALLOWED_CHAT_IDS || "").split(",").map((s) => s.trim()).filter(Boolean);
    if (allowed.length && !allowed.includes(chatId)) {
      await sendMessage(env, chatId, "Извините, этот бот закрытый.");
      return new Response("ok");
    }

    const text = msg.text.trim();
    const command = text.split(/\s+/)[0].split("@")[0].toLowerCase();

    if (text === BTN_SHOT || command === "/shot") {
      const now = Date.now();
      const prev = lastRequest.get(chatId) || 0;
      if (now - prev < COOLDOWN_MS) {
        const wait = Math.ceil((COOLDOWN_MS - (now - prev)) / 1000);
        await sendMessage(env, chatId, `Следующий скриншот можно сделать через ${wait} с.`);
        return new Response("ok");
      }
      lastRequest.set(chatId, now);

      const wait = await sendMessage(env, chatId, "⏳ Делаю скриншот…");
      const waitId = wait?.result?.message_id ?? null;

      // отвечаем Telegram сразу, а скриншот делаем в фоне
      ctx.waitUntil(makeAndSend(env, chatId, waitId));
    } else {
      await sendMessage(env, chatId, WELCOME);
    }
    return new Response("ok");
  },
};

async function makeAndSend(env, chatId, waitId) {
  let image;
  try {
    image = await takeScreenshot(env);
  } catch (e) {
    console.log("Cloudflare browser failed, fallback to GitHub:", String(e));
    await fallbackToGitHub(env, chatId, waitId, String(e).slice(0, 200));
    return;
  }

  const now = new Date().toLocaleString("ru-RU", {
    timeZone: "Europe/Moscow", day: "2-digit", month: "2-digit", year: "numeric",
    hour: "2-digit", minute: "2-digit",
  });
  const form = new FormData();
  form.append("chat_id", chatId);
  form.append("caption", `profinance.ru/gold · ${now} МСК`);
  form.append("reply_markup", JSON.stringify(KEYBOARD));
  form.append("photo", new Blob([image], { type: "image/jpeg" }), "profinance.jpg");
  const r = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendPhoto`, {
    method: "POST",
    body: form,
  });
  if (!r.ok) console.log("sendPhoto failed", r.status, await r.text());
  if (waitId) await deleteMessage(env, chatId, waitId);
}

// Бесплатный Cloudflare даёт запускать новый браузер примерно раз в 20 секунд.
// Если упёрлись в лимит — ждём и пробуем снова, а не уходим сразу на GitHub.
async function launchBrowser(env) {
  for (let attempt = 1; ; attempt++) {
    try {
      return await puppeteer.launch(env.BROWSER);
    } catch (e) {
      const msg = String(e);
      const limited = /429|rate limit|too many/i.test(msg);
      if (!limited || attempt >= 4) throw e;
      console.log(`launch limited, retry ${attempt}:`, msg);
      await new Promise((r) => setTimeout(r, 5000));
    }
  }
}

// Время на странице — по Москве и в 24-часовом формате, как у пользователя в браузере
function forceMoscowTime() {
  const TZ = "Europe/Moscow", LOC = "ru-RU";
  const wrap = (fn) => function (locales, options) {
    return fn.call(this, LOC, Object.assign({}, options || {}, { timeZone: TZ }));
  };
  Date.prototype.toLocaleString = wrap(Date.prototype.toLocaleString);
  Date.prototype.toLocaleTimeString = wrap(Date.prototype.toLocaleTimeString);
  Date.prototype.toLocaleDateString = wrap(Date.prototype.toLocaleDateString);
  const DTF = Intl.DateTimeFormat;
  const Patched = function (locales, options) {
    return new DTF(LOC, Object.assign({}, options || {}, { timeZone: TZ }));
  };
  Patched.prototype = DTF.prototype;
  Patched.supportedLocalesOf = DTF.supportedLocalesOf;
  Intl.DateTimeFormat = Patched;
  Object.defineProperty(navigator, "language", { get: () => LOC });
  Object.defineProperty(navigator, "languages", { get: () => [LOC, "ru"] });
}

async function takeScreenshot(env) {
  const browser = await launchBrowser(env);
  try {
    const page = await browser.newPage();
    await page.setViewport(VIEWPORT);
    await page.evaluateOnNewDocument(forceMoscowTime);
    await page.setRequestInterception(true);
    page.on("request", (req) => {
      const url = req.url();
      if (AD_HOSTS.some((h) => url.includes(h))) req.abort();
      else req.continue();
    });

    await page.goto(PAGE_URL, { waitUntil: "domcontentloaded", timeout: 20_000 });
    // ждём, пока таблица цен заполнится цифрами; если не дождались — не шлём
    // неполную картинку, а отдаём задачу запасному пути (GitHub)
    const ok = await page
      .waitForFunction(tableReady, { timeout: 12_000, polling: 300 })
      .then(() => true, () => false);
    if (!ok) throw new Error("таблица цен не загрузилась за 12 с");
    // ждём, пока нарисуется график
    const chartOk = await page
      .waitForFunction(chartReady, { timeout: 8_000, polling: 300 })
      .then(() => true, () => false);
    if (!chartOk) throw new Error("график не загрузился за 8 с");
    await new Promise((r) => setTimeout(r, 700));

    await page.evaluate(hideCookieAndFixed);
    await new Promise((r) => setTimeout(r, 200));
    const clip = await page.evaluate(findClip, END_MARKERS);

    const opts = { type: "jpeg", quality: 88 };
    if (clip && clip.width > 200 && clip.height > 200) {
      opts.clip = clip;
      opts.captureBeyondViewport = true;
    }
    return await page.screenshot(opts);
  } finally {
    await browser.close();
  }
}

// --- выполняется внутри страницы ---

function tableReady() {
  return [...document.querySelectorAll("tr")].some(
    (tr) => /Palladium/.test(tr.innerText || "") && /\d/.test(tr.innerText || "")
  );
}

function chartReady() {
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
}

function hideCookieAndFixed() {
  for (const el of document.querySelectorAll("button, a, div, span")) {
    const t = (el.textContent || "").trim();
    if (t === "Я согласен" || t === "Согласен" || t === "Принять") {
      let n = el;
      for (let k = 0; k < 6 && n; k++, n = n.parentElement) {
        const s = (n.innerText || "").toLowerCase();
        if (s.includes("cookie") || s.includes("куки")) {
          try { el.click(); } catch (e) {}
          break;
        }
      }
    }
  }
  for (const el of document.querySelectorAll("body *")) {
    const st = getComputedStyle(el);
    if (st.position !== "fixed" && st.position !== "sticky") continue;
    if ((el.innerText || "").includes("Markets")) { el.style.position = "static"; continue; }
    el.style.setProperty("display", "none", "important");
  }
}

function findClip(endMarkers) {
  const vis = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const all = [...document.querySelectorAll("body *")].filter(vis);
  const leaf = (t) => all.filter((el) => el.children.length === 0 && (el.textContent || "").trim() === t);
  const R = (el) => { const r = el.getBoundingClientRect();
    return { left: r.left + scrollX, right: r.right + scrollX, top: r.top + scrollY, bottom: r.bottom + scrollY }; };

  const goldCells = leaf("Gold").filter((el) => el.closest("tr"));
  const priceTable = goldCells.length ? goldCells[goldCells.length - 1].closest("table") : null;
  if (!priceTable) return null;
  const tbl = R(priceTable);

  const anchor = leaf("Markets")[0];
  if (!anchor) return null;
  let header = anchor;
  while (header.parentElement && header !== document.body && R(header).left > tbl.left + 4) {
    header = header.parentElement;
  }
  const hdr = R(header);

  let bottom = null;
  for (const m of endMarkers) {
    const cands = all.filter((el) => (el.innerText || "").trim().startsWith(m) && R(el).top > tbl.top);
    if (cands.length) {
      cands.sort((a, b) => R(a).bottom - R(b).bottom);
      bottom = R(cands[0].closest("tr") || cands[0]).bottom;
      break;
    }
  }
  if (bottom === null) bottom = tbl.bottom;

  const x = Math.max(0, Math.min(hdr.left, tbl.left) - 6);
  const right = Math.min(document.documentElement.scrollWidth, Math.max(hdr.right, tbl.right) + 6);
  const y = Math.max(0, hdr.top);
  return { x, y, width: right - x, height: bottom - y + 12 };
}

// --- запасной путь через GitHub ---

async function fallbackToGitHub(env, chatId, waitId, reason) {
  // DEBUG: причину показываем прямо в сообщении — потом эту часть можно убрать
  const note = reason ? `\n\n(быстрый способ не сработал: ${reason})` : "";
  if (waitId) await editMessage(env, chatId, waitId, "⏳ Делаю скриншот, это займёт секунд 20–30…" + note);
  const res = await fetch(`https://api.github.com/repos/${env.GITHUB_REPO}/dispatches`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "profinance-shot-worker",
    },
    body: JSON.stringify({ event_type: "shot", client_payload: { chat_id: chatId, wait_message_id: waitId } }),
  });
  if (!res.ok) {
    console.log("GitHub dispatch failed", res.status, await res.text());
    lastRequest.delete(chatId);
    if (waitId) await deleteMessage(env, chatId, waitId);
    await sendMessage(env, chatId, "Не получилось сделать скриншот, попробуйте позже.");
  }
}

// --- Telegram ---

async function tg(env, method, body) {
  const r = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/${method}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  try { return await r.json(); } catch { return null; }
}

const sendMessage = (env, chatId, text) => tg(env, "sendMessage", { chat_id: chatId, text, reply_markup: KEYBOARD });
const editMessage = (env, chatId, id, text) => tg(env, "editMessageText", { chat_id: chatId, message_id: id, text });
const deleteMessage = (env, chatId, id) => tg(env, "deleteMessage", { chat_id: chatId, message_id: id });
