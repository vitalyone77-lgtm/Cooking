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

# Tavily (tavily.com) — основной поиск в интернете, платный API без скрейпинга и без
# рейтлимитов, в отличие от DuckDuckGo (который используется как резерв, если ключ
# не задан или Tavily недоступен).
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")

# Напоминание "что приготовить завтра"
REMINDER_ENABLED = os.getenv("REMINDER_ENABLED", "true").lower() == "true"
REMINDER_HOUR = int(os.getenv("REMINDER_HOUR", "14"))
REMINDER_MINUTE = int(os.getenv("REMINDER_MINUTE", "0"))
REMINDER_TIMEZONE = os.getenv("REMINDER_TIMEZONE", "Europe/Moscow")
