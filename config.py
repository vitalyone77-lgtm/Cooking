"""
Конфигурация бота. Все секреты читаются из .env (см. .env.example).
"""
import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")

# Основной провайдер LLM. Порядок автоматического фолбэка при ошибке/пустом ответе —
# см. LLM_FALLBACK_ORDER в ai.py: deepseek -> groq -> openrouter (пропускаются те,
# у кого не задан ключ).
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "deepseek").lower()  # deepseek | groq | openrouter

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
# "deepseek-chat"/"deepseek-reasoner" — старые имена, отключаются 24.07.2026, не используем.
# Текущее поколение (V4.1) — модель "deepseek-flash" с параметром thinking (enabled/disabled)
# в теле запроса; thinking по умолчанию ВКЛЮЧЁН, поэтому ai.py явно шлёт thinking:disabled,
# чтобы не тратить токены и время на скрытые рассуждения там, где они не нужны.
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-flash")
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct:free")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Сколько результатов веб-поиска брать для анализа
SEARCH_RESULTS_COUNT = 5

# "Меню на несколько дней" — пилот на 3 дня (см. week_menu.py), чтобы проверить, как
# работает идея с продуктовой корзиной + генерацией по дням, прежде чем расширять до недели.
WEEK_MENU_DAYS = 3

# Tavily (tavily.com) — основной поиск в интернете, платный API без скрейпинга и без
# рейтлимитов, в отличие от DuckDuckGo (который используется как резерв, если ключ
# не задан или Tavily недоступен).
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")
# Платный резервный поиск DeepSeek (тот же ключ DEEPSEEK_API_KEY) — только когда Tavily и DuckDuckGo пусты
DEEPSEEK_SEARCH_ENABLED = os.getenv("DEEPSEEK_SEARCH_ENABLED", "true").lower() == "true"

# Напоминание "что приготовить завтра"
REMINDER_ENABLED = os.getenv("REMINDER_ENABLED", "true").lower() == "true"
REMINDER_HOUR = int(os.getenv("REMINDER_HOUR", "14"))
REMINDER_MINUTE = int(os.getenv("REMINDER_MINUTE", "0"))
REMINDER_TIMEZONE = os.getenv("REMINDER_TIMEZONE", "Europe/Moscow")

# ---------------------------------------------------------------------------
# Магазины (пакет stores/)
# ---------------------------------------------------------------------------
# Шаблоны ссылок на поиск товара в магазине. {q} — url-кодированный запрос.
# Ссылки на Лавку и Купер НЕ проверены (сайты закрыты от автоматических запросов) —
# если ссылка не открывает поиск, поправь шаблон здесь или через .env, код менять не нужно.
STORE_URL_FIVEKA = os.getenv("STORE_URL_FIVEKA", "https://5ka.ru/search/?text={q}")
STORE_URL_VKUSVILL = os.getenv("STORE_URL_VKUSVILL", "https://vkusvill.ru/search/?q={q}")
STORE_URL_LAVKA = os.getenv("STORE_URL_LAVKA", "https://lavka.yandex.ru/search?text={q}")
STORE_URL_PEREKRESTOK = os.getenv("STORE_URL_PEREKRESTOK", "https://www.perekrestok.ru/cat/search?search={q}")
STORE_URL_KUPER = os.getenv("STORE_URL_KUPER", "https://web.kuper.ru/multisearch?q={q}")

# ВкусВилл: официальный (экспериментальный) MCP-сервер — единственный магазин, у которого
# бот получает реальные цены и собирает ссылку-корзину. Без ключей.
VKUSVILL_PRICES_ENABLED = os.getenv("VKUSVILL_PRICES_ENABLED", "true").lower() == "true"
VKUSVILL_MCP_URLS = [
    u.strip() for u in os.getenv(
        "VKUSVILL_MCP_URL", "https://mcp.vkusvill.ru/mcp,https://mcp001.vkusvill.ru/mcp"
    ).split(",") if u.strip()
]
VKUSVILL_MCP_API_KEY = os.getenv("VKUSVILL_MCP_API_KEY", "")
# Общий лимит времени на подсчёт цен корзины (секунды) — дальше показываем только ссылки
STORE_PRICES_TIMEOUT = float(os.getenv("STORE_PRICES_TIMEOUT", "40"))

# ---------------------------------------------------------------------------
# Веб-версия (web_api.py + web/index.html) — работает на своём поддомене, см. DEPLOY_WEB.md
# ---------------------------------------------------------------------------
WEB_SECRET = os.getenv("WEB_SECRET", "")               # подпись cookie админки (/admin)
YANDEX_VERIFICATION = os.getenv("YANDEX_VERIFICATION", "")              # код из Яндекс Вебмастера
GOOGLE_SITE_VERIFICATION = os.getenv("GOOGLE_SITE_VERIFICATION", "")    # код из Google Search Console
WEB_APP_URL = os.getenv("WEB_APP_URL", "")             # например https://menu.ymayaka.ru
# УСТАРЕЛО (схема «страница на Тильде + API») — нигде не используется, можно удалить из .env
WEB_ALLOWED_ORIGINS = [
    o.strip() for o in os.getenv("WEB_ALLOWED_ORIGINS", WEB_APP_URL).split(",") if o.strip()
]

# Автономная веб-версия (web_api.py отдаёт и страницу, и API)
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")            # пароль входа в /admin
WEB_LIMIT_RECIPE = int(os.getenv("WEB_LIMIT_RECIPE", "3"))     # блюд в сутки на устройство (веб)
WEB_LIMIT_DAYMENU = int(os.getenv("WEB_LIMIT_DAYMENU", "1"))   # меню на день в сутки
WEB_LIMIT_MISSING = int(os.getenv("WEB_LIMIT_MISSING", "5"))   # замен «нет таких-то продуктов» в сутки
WEB_LIMIT_WEEK = int(os.getenv("WEB_LIMIT_WEEK", "1"))         # планов на 3 дня за 3 календарных дня
# Общий потолок генераций ИИ в сутки (МСК) на весь проект — бот + сайт + напоминания. 0 — без потолка.
DAILY_GEN_CAP = int(os.getenv("DAILY_GEN_CAP", "500"))
# Напоминания — только тем, кто пропал: через сколько дней тишины каждое следующее («4,6» — первое через 4 дня
# без визитов, второе ещё через 6; дальше молчим, пока человек не зайдёт снова)
REMINDER_GAPS = [int(x) for x in os.getenv("REMINDER_GAPS", "4,6").split(",") if x.strip()]
# Отдельный дневной запас для сайта БЕЗ входа через Telegram (внутри общего потолка): накрутка через новые
# устройства не может съесть запас пользователей Telegram и подписчиков
DAILY_GEN_CAP_ANON = int(os.getenv("DAILY_GEN_CAP_ANON", "150"))
# Генераций в сутки с одного IP для сайта без входа (сколько бы устройств с него ни создали)
WEB_IP_DAILY_GENS = int(os.getenv("WEB_IP_DAILY_GENS", "40"))   # у мобильных операторов за одним IP много людей

# ---------------------------------------------------------------------------
# Подписка (plans.py). Пока PAYWALL_ENABLED=false — у всех лимиты LIMITS_OPEN, как раньше.
# Лимит «вид=сколько/за сколько дней», например recipe=2/1 — 2 блюда в сутки, week=1/3 — 1 план за 3 дня.
# ---------------------------------------------------------------------------
PAYWALL_ENABLED = os.getenv("PAYWALL_ENABLED", "false").lower() == "true"
LIMITS_OPEN = os.getenv("LIMITS_OPEN", f"recipe={WEB_LIMIT_RECIPE}/1,daymenu={WEB_LIMIT_DAYMENU}/1,"
                                       f"week={WEB_LIMIT_WEEK}/3,missing={WEB_LIMIT_MISSING}/1,photo=5/1")
LIMITS_FREE = os.getenv("LIMITS_FREE", "recipe=3/1,daymenu=1/7,week=0/3,missing=2/1,photo=1/1")
LIMITS_PREMIUM = os.getenv("LIMITS_PREMIUM", "recipe=6/1,daymenu=2/1,week=1/2,missing=10/1,photo=5/1")
LIMITS_ANON = os.getenv("LIMITS_ANON", "recipe=1/1,daymenu=0/1,week=0/3,missing=1/1,photo=0/1")   # сайт без входа
# Функции только для подписки (при PAYWALL_ENABLED): memory (полка — продукты, которые уже есть дома), pdf, cart (корзина ВкусВилла с ценами), noads (сайт без рекламы)
PREMIUM_FEATURES = [x.strip() for x in os.getenv("PREMIUM_FEATURES", "memory,pdf,cart,noads").split(",") if x.strip()]
# Новые функции — кому включены: off (никому), testers (только тестировщикам), premium (подписке), all (всем).
# voice — голосовой ввод, photo_recipe — рецепт по фото холодильника, photo_shelf — полка по фото
FEATURE_ROLLOUT = os.getenv("FEATURE_ROLLOUT", "voice=testers,photo_recipe=testers,photo_shelf=testers")
# Тестировщики: Telegram id через запятую — им видны функции в режиме testers и полный доступ при подписке
TESTER_IDS = [x.strip() for x in os.getenv("TESTER_IDS", "").split(",") if x.strip().lstrip("-").isdigit()]
VISION_MODEL = os.getenv("VISION_MODEL", "deepseek-v4-flash-vision-exp")   # DeepSeek с картинками
STT_MODEL = os.getenv("STT_MODEL", "whisper-large-v3-turbo")              # распознавание голоса (Groq)
VOICE_DAILY = int(os.getenv("VOICE_DAILY", "30"))                          # голосовых в сутки на человека
TRIAL_DAYS = int(os.getenv("TRIAL_DAYS", "7"))     # пробный полный доступ — включается сам при первом упоре в лимит
# Тарифы: имя=дни:цена_в_рублях:цена_в_звёздах
PREMIUM_PLANS = os.getenv("PREMIUM_PLANS", "month=30:290:220,year=365:2900:2200")
# Промокоды: КОД=дней (для блогера и акций), регистр не важен
PROMO_CODES = os.getenv("PROMO_CODES", "")
# Оплата: Telegram Stars работают без договора; рубли — через платёжный сервис (данные пришлют позже):
# PAYMENT_PROVIDER_TOKEN — токен из @BotFather → Payments (ЮKassa и др.) для счёта прямо в боте;
PAYMENT_STARS_ENABLED = os.getenv("PAYMENT_STARS_ENABLED", "true").lower() == "true"
PAYMENT_PROVIDER_TOKEN = os.getenv("PAYMENT_PROVIDER_TOKEN", "")
# PayAnyWay (Монета): номер бизнес-счёта и «код проверки целостности данных» из кабинета (код только в .env, в git и в чат не попадает)
PAYANYWAY_MNT_ID = os.getenv("PAYANYWAY_MNT_ID", "").strip()
PAYANYWAY_CODE = os.getenv("PAYANYWAY_CODE", "").strip()
PAYANYWAY_URL = os.getenv("PAYANYWAY_URL", "https://www.payanyway.ru/assistant.htm")
# Реквизиты продавца для страницы /oplata (требование платёжного сервиса): только в .env на сервере
SELLER_NAME = os.getenv("SELLER_NAME", "").strip()
SELLER_INN = os.getenv("SELLER_INN", "").strip()
SELLER_PHONE = os.getenv("SELLER_PHONE", "").strip()
SELLER_EMAIL = os.getenv("SELLER_EMAIL", "").strip()
PAYANYWAY_CHECK_RUB = int(os.getenv("PAYANYWAY_CHECK_RUB", "0") or 0)   # служебный тариф для проверки новой платёжки настоящими деньгами: 0 — нет его ни у кого
PAYANYWAY_SIGN_FORM = os.getenv("PAYANYWAY_SIGN_FORM", "1")   # 0 — не подписывать форму оплаты (на счёте самозанятого подпись формы не включена, с подписью Монета отвечает «Неправильная подпись»)
PAYANYWAY_TEST = os.getenv("PAYANYWAY_TEST", "1")          # 1 — тестовый режим (по умолчанию), 0 — боевой: включить, когда проверено
# Кому из Telegram приходят тревоги (потолок, баланс DeepSeek) и кто может /grant: id через запятую
ADMIN_TG_IDS = [int(x) for x in os.getenv("ADMIN_TG_IDS", "").replace(" ", "").split(",") if x.strip().lstrip("-").isdigit()]
BOT_USERNAME = os.getenv("BOT_USERNAME", "Cookingvkusno_bot")
# Вход партнёра (блогера) в админку: статистика, деньги, цены, лимиты, функции подписки, промокоды
# «Рецепт дня» в сообщество ВКонтакте и Telegram-канал (social.py). Ключи только в .env на сервере.
VK_GROUP_ID = (os.getenv("VK_GROUP_ID", "") or "").strip()       # число-ID или короткий адрес сообщества (kbzum), можно ссылкой
VK_GROUP_TOKEN = os.getenv("VK_GROUP_TOKEN", "")                 # ключ доступа сообщества с правом на стену
VK_API_VERSION = os.getenv("VK_API_VERSION", "5.199")
VK_API_URL = os.getenv("VK_API_URL", "https://api.vk.com")
# Вход на сайте через VK ID (для тех, у кого Telegram не открывается без VPN). ID приложения не секретный; защищённый ключ не нужен:
# виджет обменивает код в браузере, а сервер проверяет токен запросом к VK.
BOOK_REPEAT_DAYS = int(os.getenv("BOOK_REPEAT_DAYS", "14"))     # один и тот же рецепт из книги одному человеку не чаще, чем раз в столько дней
VK_CLIENT_ID = os.getenv("VK_CLIENT_ID", "54875605")
VK_ID_API_URL = os.getenv("VK_ID_API_URL", "https://id.vk.ru")      # вход ВК живёт на vk.ru: с id.vk.com телефон не видит сессию и ходит по кругу
VK_LOGIN = os.getenv("VK_LOGIN", "true").strip().lower() not in ("0", "false", "no", "off")
TG_CHANNEL_ID = os.getenv("TG_CHANNEL_ID", "")                   # @имя_канала или число; бот должен быть админом канала
SOCIAL_AUTOPOST = os.getenv("SOCIAL_AUTOPOST", "false").lower() == "true"   # false: только вручную через /postvk
SOCIAL_POST_HOUR = int(os.getenv("SOCIAL_POST_HOUR", "9"))
SOCIAL_POST_MINUTE = int(os.getenv("SOCIAL_POST_MINUTE", "30"))
SOCIAL_FEATURE_DAY = os.getenv("SOCIAL_FEATURE_DAY", "wed")       # пост о функции раз в неделю, между рецептами дня
SOCIAL_FEATURE_HOUR = int(os.getenv("SOCIAL_FEATURE_HOUR", "18"))
# Картинки блюд через Leonardo.ai (images.py): ключ владельца; SOCIAL_IMAGES=true — рисовать картинку к каждому рецепту дня
# Чем делать картинку: card (фирменная карточка рецепта, бесплатно, по умолчанию), free (pollinations.ai, как в autobot),
# leonardo (платный) или off. Если free или leonardo не справились, берётся карточка
IMAGE_PROVIDER = os.getenv("IMAGE_PROVIDER", "card").strip().lower()
FREE_IMAGE_MODEL = os.getenv("FREE_IMAGE_MODEL", "flux")
POLLINATIONS_KEY = os.getenv("POLLINATIONS_KEY", "")           # необязательно: ключ аккаунта pollinations снимает лимиты
LEONARDO_API_KEY = os.getenv("LEONARDO_API_KEY", "")
LEONARDO_MODEL_ID = os.getenv("LEONARDO_MODEL_ID", "7b592283-e8a7-4c5a-9ba6-d18c31f258b9")
RECIPE_IMAGE_SIZE = int(os.getenv("RECIPE_IMAGE_SIZE", "768"))
_si = os.getenv("SOCIAL_IMAGES", "").strip().lower()      # пусто: картинка к каждому посту, если она бесплатная
SOCIAL_IMAGES = (_si == "true") if _si else IMAGE_PROVIDER in ("card", "free")
# Как картинка попадает в запись ВК: link (ссылка на страницу рецепта, ВК показывает превью-карточку; работает с ключом сообщества)
# или upload (загрузка фото в ВК, нужен ПОЛЬЗОВАТЕЛЬСКИЙ ключ: ключ сообщества получает отказ, код 27)
VK_IMAGE_MODE = os.getenv("VK_IMAGE_MODE", "link").strip().lower()
# Постоянные картинки направлений из альбома сообщества: zavtrak=457239028,obed=457239029 (см. design/vk/sections)
VK_SECTION_PHOTOS = os.getenv("VK_SECTION_PHOTOS", "").strip()
VK_PHOTO_TOKEN = os.getenv("VK_PHOTO_TOKEN", "").strip()      # ключ пользователя с правом photos: только для загрузки фото на стену (ключу сообщества ВК это запрещает)
PARTNER_PASSWORD = os.getenv("PARTNER_PASSWORD", "")
PARTNER_SHARE_PCT = int(os.getenv("PARTNER_SHARE_PCT", "50"))     # доля партнёра от выручки, %
DEEPSEEK_BALANCE_ALERT_USD = float(os.getenv("DEEPSEEK_BALANCE_ALERT_USD", "3"))
WEB_DAILY_LIMIT = int(os.getenv("WEB_DAILY_LIMIT", "12"))   # генераций в сутки на одно устройство
WEB_DATA_DIR = os.getenv("WEB_DATA_DIR", "web_data")        # база и данные веба (отдельно от бота)
