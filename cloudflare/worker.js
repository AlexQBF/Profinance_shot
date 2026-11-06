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

const COOLDOWN_MS = 30_000; // не чаще раза в 30 секунд на человека
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
        await sendMessage(env, chatId, `Скриншот уже готовится. Повторить можно через ${wait} с.`);
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
    await fallbackToGitHub(env, chatId, waitId);
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

async function takeScreenshot(env) {
  const browser = await puppeteer.launch(env.BROWSER);
  try {
    const page = await browser.newPage();
    await page.setViewport(VIEWPORT);
    await page.setRequestInterception(true);
    page.on("request", (req) => {
      const url = req.url();
      if (AD_HOSTS.some((h) => url.includes(h))) req.abort();
      else req.continue();
    });

    await page.goto(PAGE_URL, { waitUntil: "domcontentloaded", timeout: 20_000 });
    // ждём таблицу цен, потом немного — чтобы дорисовался график
    await page
      .waitForFunction(() => document.body && document.body.innerText.includes("Palladium"), { timeout: 10_000 })
      .catch(() => {});
    await new Promise((r) => setTimeout(r, 1500));

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

async function fallbackToGitHub(env, chatId, waitId) {
  if (waitId) await editMessage(env, chatId, waitId, "⏳ Делаю скриншот, это займёт секунд 20–30…");
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
