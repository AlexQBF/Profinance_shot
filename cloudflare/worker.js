/**
 * Cloudflare Worker — «звонок» из Telegram в GitHub Actions.
 *
 * Telegram присылает сюда сообщения боту (webhook). На команды /shot и /full
 * Worker отвечает «делаю…» и запускает workflow в GitHub (repository_dispatch).
 *
 * Переменные Worker (Settings → Variables and Secrets):
 *   TELEGRAM_BOT_TOKEN  (secret) — токен бота
 *   GITHUB_TOKEN        (secret) — fine-grained token GitHub с доступом к репозиторию
 *   WEBHOOK_SECRET      (secret) — любая длинная случайная строка
 *   GITHUB_REPO         (text)   — например  ivanov/profinance-shot
 *   ALLOWED_CHAT_IDS    (text)   — id чатов через запятую, кому можно (ваш id)
 */

export default {
  async fetch(request, env) {
    if (request.method !== "POST") {
      return new Response("ok");
    }

    // принимаем только запросы от Telegram с нашим секретом
    if (request.headers.get("X-Telegram-Bot-Api-Secret-Token") !== env.WEBHOOK_SECRET) {
      return new Response("forbidden", { status: 403 });
    }

    let update;
    try {
      update = await request.json();
    } catch {
      return new Response("ok");
    }

    const msg = update.message || update.channel_post;
    if (!msg || typeof msg.text !== "string") return new Response("ok");

    const chatId = String(msg.chat.id);
    const allowed = (env.ALLOWED_CHAT_IDS || "")
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    if (!allowed.includes(chatId)) return new Response("ok"); // чужим молча не отвечаем

    // "/shot", "/shot@MyBot", "/full"
    const command = msg.text.trim().split(/\s+/)[0].split("@")[0].toLowerCase();

    if (command === "/shot" || command === "/full") {
      const res = await fetch(`https://api.github.com/repos/${env.GITHUB_REPO}/dispatches`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${env.GITHUB_TOKEN}`,
          Accept: "application/vnd.github+json",
          "X-GitHub-Api-Version": "2022-11-28",
          "User-Agent": "profinance-shot-worker",
        },
        body: JSON.stringify({
          event_type: command.slice(1), // shot | full
          client_payload: { chat_id: chatId },
        }),
      });

      if (res.ok) {
        await sendMessage(env, chatId, "Делаю скриншот, пришлю примерно через минуту…");
      } else {
        const text = await res.text();
        await sendMessage(env, chatId, `GitHub не запустил задачу (${res.status}): ${text.slice(0, 300)}`);
      }
    } else if (command === "/start" || command === "/help") {
      await sendMessage(
        env,
        chatId,
        "/shot — скриншот главной profinance.ru\n/full — вся страница целиком (файлом)"
      );
    }

    return new Response("ok");
  },
};

async function sendMessage(env, chatId, text) {
  await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ chat_id: chatId, text }),
  });
}
