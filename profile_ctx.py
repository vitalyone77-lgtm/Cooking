"""
Профиль пользователя (аллергии, «никогда не использовать») для текущего запроса.

Бот (middleware) и сайт (перед запуском задачи) кладут профиль в contextvar, а ai.call_llm_with_fallback —
единственная точка, через которую идут ВСЕ генерации (рецепт, меню на день, план, день плана, замены,
напоминания), — дописывает его в запрос и проверяет ответ. Так ни один путь не забудет про аллергию.
asyncio-задачи копируют контекст при создании, поэтому профиль доезжает и до фоновых задач.
"""
import contextvars
import re

current = contextvars.ContextVar("cooking_profile", default=None)


def set_for(uid) -> None:
    """Профиль — функция подписки «memory» (пока подписка выключена — у всех)."""
    import accounts
    import plans
    try:
        p = accounts.get_profile(uid) if plans.has_feature(uid, "memory") else None
    except Exception:  # noqa: BLE001 — сбой базы не должен ломать подбор
        p = None
    current.set(p if p and (p.get("allergies") or p.get("never")) else None)


def _items(p: dict) -> list[str]:
    out = []
    for key in ("allergies", "never"):
        out += [x.strip() for x in (p.get(key) or "").split(",") if x.strip()]
    return out


def prompt_block() -> str:
    p = current.get()
    if not p:
        return ""
    lines = ["\n\nПРОФИЛЬ ПОЛЬЗОВАТЕЛЯ — ЭТО ВАЖНЕЕ ЛЮБЫХ ДРУГИХ ПРАВИЛ И ПОЖЕЛАНИЙ:"]
    if p.get("allergies"):
        lines.append(f"- АЛЛЕРГИЯ (опасно для здоровья!): {p['allergies']}. Не используй эти продукты и всё, что их "
                     "содержит (соусы, заправки, посыпки, готовые смеси, замены «по вкусу»), ни в одном блюде.")
    if p.get("never"):
        lines.append(f"- Никогда не использовать (не ест): {p['never']}. Ни в одном блюде, даже как добавку.")
    return "\n".join(lines)


def _pattern(word: str) -> re.Pattern:
    """«молоко» -> молок + до 4 букв окончания: молоко/молока/молоком, но не «молотый»; «орехи» -> орехов/ореховая."""
    w = word.lower().replace("ё", "е")
    if len(w) >= 4 and w[-1] in "аеиоуыэюяйь":
        w = w[:-1]
    return re.compile(r"(?<![а-я])(?<!без )(?<!не )" + re.escape(w) + r"[а-я]{0,4}(?![а-я])")


def hits(text: str) -> list[str]:
    """Продукты из профиля, которые всё же встретились в ответе (кроме «без орехов», «не добавляй молоко»
    и строк вида «исключено: …»)."""
    p = current.get()
    if not p or not text:
        return []
    t = "\n".join(ln for ln in text.lower().replace("ё", "е").splitlines()
                  if not any(x in ln for x in ("исключ", "аллерг", "профил")))
    found = []
    for item in _items(p):
        first = item.split()[0]
        if len(first) >= 3 and _pattern(first).search(t):
            found.append(item)
    return found
