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
STORE_URL_KUPER = os.getenv("STORE_URL_KUPER", "https://kuper.ru/search?keywords={q}")

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
WEB_APP_URL = os.getenv("WEB_APP_URL", "")             # например https://menu.pump-um.ru
# УСТАРЕЛО (схема «страница на Тильде + API») — нигде не используется, можно удалить из .env
WEB_ALLOWED_ORIGINS = [
    o.strip() for o in os.getenv("WEB_ALLOWED_ORIGINS", WEB_APP_URL).split(",") if o.strip()
]

# Автономная веб-версия (web_api.py отдаёт и страницу, и API)
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")            # пароль входа в /admin
WEB_DAILY_LIMIT = int(os.getenv("WEB_DAILY_LIMIT", "12"))   # генераций в сутки на одно устройство
WEB_DATA_DIR = os.getenv("WEB_DATA_DIR", "web_data")        # база и данные веба (отдельно от бота)
