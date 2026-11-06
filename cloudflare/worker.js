const BTN_SHOT = "📸 Скриншот";
const BTN_FULL = "📄 Вся страница";
const BTN_HELP = "❓ Помощь";

const KEYBOARD = {
  keyboard: [[{ text: BTN_SHOT }], [{ text: BTN_FULL }, { text: BTN_HELP }]],
  resize_keyboard: true,
  is_persistent: true,
};

const WELCOME =
  "Привет! Я присылаю скриншот страницы золота с profinance.ru.\n\n" +
  `${BTN_SHOT} — график и таблица цен\n` +
  `${BTN_FULL} — вся страница целиком (файлом)\n\n` +
  "Картинка приходит примерно через минуту.";

const COOLDOWN_MS = 60_000; // не чаще раза в минуту на человека
const lastRequest = new Map();

export default {
  async fetch(request, env) {
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
    const allowed = (env.ALLOWED_CHAT_IDS || "")
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    if (allowed.length && !allowed.includes(chatId)) {
      await sendMessage(env, chatId, "Извините, этот бот закрытый.");
      return new Response("ok");
    }

    const text = msg.text.trim();
    const command = text.split(/\s+/)[0].split("@")[0].toLowerCase();

    let mode = null;
    if (text === BTN_SHOT || command === "/shot") mode = "shot";
    else if (text === BTN_FULL || command === "/full") mode = "full";

    if (mode) {
      const now = Date.now();
      const prev = lastRequest.get(chatId) || 0;
      if (now - prev < COOLDOWN_MS) {
        const wait = Math.ceil((COOLDOWN_MS - (now - prev)) / 1000);
        await sendMessage(env, chatId, `Скриншот уже готовится. Повторить можно через ${wait} с.`);
        return new Response("ok");
      }
      lastRequest.set(chatId, now);

      const res = await fetch(`https://api.github.com/repos/${env.GITHUB_REPO}/dispatches`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${env.GITHUB_TOKEN}`,
          Accept: "application/vnd.github+json",
          "X-GitHub-Api-Version": "2022-11-28",
          "User-Agent": "profinance-shot-worker",
        },
        body: JSON.stringify({ event_type: mode, client_payload: { chat_id: chatId } }),
      });

      if (res.ok) {
        await sendMessage(env, chatId, "⏳ Делаю скриншот, пришлю примерно через минуту…");
      } else {
        lastRequest.delete(chatId);
        console.log("GitHub dispatch failed", res.status, await res.text());
        await sendMessage(env, chatId, "Не получилось запустить скриншот, попробуйте позже.");
      }
    } else {
      await sendMessage(env, chatId, WELCOME);
    }

    return new Response("ok");
  },
};

async function sendMessage(env, chatId, text) {
  await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ chat_id: chatId, text, reply_markup: KEYBOARD }),
  });
}
