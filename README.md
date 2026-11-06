# Скриншот profinance.ru по команде в Telegram

Пишете боту `/shot` → примерно через минуту приходит скриншот главной https://www.profinance.ru/.
Свой сервер не нужен: скриншот делает GitHub Actions, а команду из Telegram в GitHub передаёт
бесплатный Cloudflare Worker.

```
Telegram  ──/shot──▶  Cloudflare Worker  ──запуск──▶  GitHub Actions
   ▲                                                       │
   └──────────────── скриншот (через Telegram API) ────────┘
```

## Команды
- `/shot` — скриншот видимой части главной (фото)
- `/full` — вся страница целиком (файлом, чтобы Telegram не сжал)

## Файлы
| Файл | Зачем |
|---|---|
| `shot.py` | открывает страницу, снимает скриншот, отправляет в Telegram |
| `requirements.txt` | библиотеки Python |
| `.github/workflows/screenshot.yml` | задача GitHub Actions |
| `cloudflare/worker.js` | прослойка Telegram → GitHub (вставляется в Cloudflare, в GitHub не исполняется) |

Всё делается в браузере, ставить ничего не нужно. Займёт около 20 минут.

---

## Шаг 1. Бот в Telegram

Нужен **отдельный бот** под эту задачу: у бота с webhook нельзя забирать сообщения
другим способом, и если ваш бот-выжимка читает свои сообщения, он сломается.
(Если бот-выжимка только *отправляет* вам сообщения — можно взять его, но надёжнее новый.)

1. В Telegram: @BotFather → `/newbot` → имя → получите **токен** вида `123456789:AA...`.
2. @userinfobot → получите свой **id** (число).
3. Откройте своего нового бота и нажмите **Start** — иначе он не сможет вам писать.

## Шаг 2. Репозиторий на GitHub

1. https://github.com/new → имя `profinance-shot` → **Private** → **Create repository**.
2. **uploading an existing file** → перетащите **содержимое** папки `profinance-shot`
   (папки `.github` и `cloudflare` тоже) → **Commit changes**.
   Папка `.github` начинается с точки и может быть скрыта:
   Windows — «Вид → Показать → Скрытые элементы», macOS — `Cmd + Shift + .`
   Проверьте после загрузки, что в репозитории есть `.github/workflows/screenshot.yml`.
3. **Settings → Secrets and variables → Actions → New repository secret**, добавьте два секрета:
   - `TELEGRAM_BOT_TOKEN` — токен бота из шага 1;
   - `TELEGRAM_CHAT_ID` — ваш id (нужен для ручного запуска кнопкой).

**Проверка:** вкладка **Actions** → «Скриншот profinance» → **Run workflow** → Run.
Через ~1 минуту в Telegram должен прийти скриншот. Если пришёл — GitHub-часть готова.

## Шаг 3. Токен GitHub для прослойки

1. https://github.com/settings/personal-access-tokens/new (Fine-grained token).
2. Name: `profinance-shot-worker`, Expiration — максимально допустимый срок.
3. **Repository access → Only select repositories** → `profinance-shot`.
4. **Permissions → Repository permissions → Contents → Read and write**
   (это право нужно, чтобы запускать задачу извне; доступ — только к этому репозиторию).
5. **Generate token** → скопируйте (показывается один раз).

## Шаг 4. Cloudflare Worker

1. Зарегистрируйтесь на https://dash.cloudflare.com (бесплатно).
2. **Workers & Pages → Create → Create Worker** (шаблон «Hello World») → имя `profinance-shot` → **Deploy**.
3. **Edit code** → удалите всё → вставьте содержимое `cloudflare/worker.js` → **Deploy**.
4. Запомните адрес Worker'а, вида `https://profinance-shot.ВАШ-ПОДДОМЕН.workers.dev`.
5. Вернитесь в Worker → **Settings → Variables and Secrets → Add**:

   | Имя | Тип | Значение |
   |---|---|---|
   | `TELEGRAM_BOT_TOKEN` | Secret | токен бота |
   | `GITHUB_TOKEN` | Secret | токен из шага 3 |
   | `WEBHOOK_SECRET` | Secret | придумайте длинную строку из латиницы и цифр, напр. `k3J9xQw7Lm2Pz8Rt` |
   | `GITHUB_REPO` | Text | `ВАШ_ЛОГИН/profinance-shot` |
   | `ALLOWED_CHAT_IDS` | Text | ваш id (несколько — через запятую) |

   → **Deploy**.

## Шаг 5. Связать бота с Worker'ом

Откройте в браузере одну ссылку, подставив свои значения:

```
https://api.telegram.org/botТОКЕН_БОТА/setWebhook?url=АДРЕС_WORKER&secret_token=WEBHOOK_SECRET
```

Должен прийти ответ `{"ok":true,"result":true,"description":"Webhook was set"}`.

## Готово

Напишите боту `/shot`. Он ответит «Делаю скриншот…», через ~1 минуту придёт картинка.

---

## Если не работает
- **Бот молчит совсем** — откройте `https://api.telegram.org/botТОКЕН_БОТА/getWebhookInfo`:
  в `last_error_message` будет причина. Частое: опечатка в адресе Worker'а или в `WEBHOOK_SECRET`.
  Также проверьте, что ваш id есть в `ALLOWED_CHAT_IDS`.
- **«GitHub не запустил задачу (404/403)»** — неверный `GITHUB_REPO` или у токена нет права
  Contents: Read and write на этот репозиторий, либо истёк срок токена.
- **«Делаю…» пришло, скриншот нет** — смотрите вкладку **Actions** в репозитории, там лог ошибки.
  Частое: не добавлен секрет `TELEGRAM_BOT_TOKEN`, или вы не нажали Start у бота.

## Настройки (необязательно)
**Settings → Secrets and variables → Actions → Variables**:
- `PAGE_URL` — другая страница вместо главной;
- `VIEWPORT` — размер окна, по умолчанию `1440x900`.

## Сколько стоит
- Cloudflare Workers: бесплатный тариф — 100 000 запросов в день, хватит с огромным запасом.
- GitHub Actions: один скриншот ≈ 1 минута. В приватном репозитории бесплатно 2000 минут в месяц
  (общие с другими вашими workflow, включая бота-выжимку).
