"""«🎯 Под меня»: рецепт из книги подстраивается под человека — его исключения, цель по КБЖУ и пожелания словами.

Функция подписки (PREMIUM_FEATURES: "tailor"). Попытка списывается как «замена продуктов» (kind "missing"):
по сути это то же — переделать готовый рецепт. Логика общая для бота и сайта.
"""
import accounts
import plans
import profile_ctx
from ai import call_llm_with_fallback
from prompts import SYSTEM_PROMPT, season_line
from shopping import extract_shopping_terms

FEATURE = "tailor"

RULES = (
    "Подстрой этот рецепт под человека. Сохрани суть блюда, где это возможно.\n"
    "- Исключённые продукты не используй нигде (в том числе в соусах и посыпках): замени ближайшим аналогом, пересчитай граммовки.\n"
    "- Если есть цель по калориям или белкам/жирам/углеводам на день — подгони состав и размер порции так, чтобы одна порция "
    "была разумной частью дневной нормы (обычно около трети), и пересчитай КБЖУ.\n"
    "- Пожелания словами выполни, если они не противоречат исключениям.\n"
    "Первой строкой ответа коротко напиши, что изменено: «🎯 Под вас: курица вместо свинины, порция 450 ккал». "
    "Дальше — весь рецепт СТРОГО по формату из системного промпта, включая служебную строку ПРОДУКТЫ_СПИСОК в конце."
)


def profile_bits(uid) -> dict:
    """Что известно о человеке: исключения (если включены и есть в тарифе) и цель."""
    g = accounts.goals_get(uid) if accounts.consent_get(uid) else {"never": "", "feats": []}
    never = g.get("never", "") if ("never" in g.get("feats", []) and plans.has_feature(uid, "memory")) else ""
    return {"never": never, "goal": accounts.goal_text(uid) if accounts.consent_get(uid) else ""}


def summary(uid) -> str:
    b = profile_bits(uid)
    parts = []
    if b["never"]:
        parts.append("без: " + b["never"])
    if b["goal"]:
        parts.append("цель на день: " + b["goal"])
    return "; ".join(parts)


async def tailor_recipe(uid, recipe_text: str, wishes: str = "") -> tuple[str, list[str]]:
    """-> (текст, продукты для поиска). ValueError — если нечего учитывать."""
    b = profile_bits(uid)
    wishes = " ".join((wishes or "").split())[:300]
    if not (b["never"] or b["goal"] or wishes):
        raise ValueError("Напишите, что изменить: например «без лука, поменьше калорий, на 4 порции»")
    profile_ctx.set_user(uid)                     # исключения профиля — ещё и проверка ответа
    lines = []
    if b["never"]:
        lines.append("ИСКЛЮЧИТЬ: " + b["never"])
    if b["goal"]:
        lines.append("ЦЕЛЬ НА ДЕНЬ: " + b["goal"])
    if wishes:
        lines.append("ПОЖЕЛАНИЯ: " + wishes)
    user = "\n".join(lines) + "\n\n" + RULES + "\n" + season_line() + "\nРЕЦЕПТ:\n" + recipe_text
    raw = await call_llm_with_fallback([{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}])
    text, terms = extract_shopping_terms(raw)
    if not text.strip():
        raise RuntimeError("ИИ вернул пустой ответ")
    return text, terms
