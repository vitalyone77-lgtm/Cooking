"""
Что исключить из блюд в ТЕКУЩЕМ запросе (поле «Исключить» анкеты) — для усиленной подсказки ИИ и проверки ответа.

Ничего о человеке в базе не хранится: список приходит вместе с запросом. Сборщики промптов (prompts.py, missing.py)
кладут его в contextvar перед обращением к ИИ, а ai.call_llm_with_fallback — единственная точка, через которую идут
ВСЕ генерации, — дописывает его в запрос и проверяет ответ. asyncio-задачи копируют контекст при создании.
"""
import contextvars
import re

current = contextvars.ContextVar("cooking_avoid", default=None)


def _split(text: str) -> list[str]:
    out = []
    for x in re.split(r"[,;\n]+", (text or "").lower()):
        x = x.strip(" .-")
        if x and x not in out and x not in ("нет", "ничего"):
            out.append(x[:40])
    return out


def set_avoid(text) -> None:
    """Список из поля «Исключить» (через запятую). Пусто — проверка не нужна."""
    items = _split(text)
    current.set({"never": ", ".join(items)[:500]} if items else None)


def _items(p: dict) -> list[str]:
    return _split(p.get("never") or "")


def prompt_block() -> str:
    p = current.get()
    if not p:
        return ""
    return ("\n\nПРОДУКТЫ, КОТОРЫЕ ПОЛЬЗОВАТЕЛЬ ПРОСИЛ ИСКЛЮЧИТЬ — ЭТО ВАЖНЕЕ ЛЮБЫХ ДРУГИХ ПРАВИЛ И ПОЖЕЛАНИЙ: "
            f"{p['never']}. Не используй их и всё, что их содержит (соусы, заправки, посыпки, готовые смеси, замены "
            "«по вкусу»), ни в одном блюде, даже как добавку.")


def _pattern(word: str) -> re.Pattern:
    """«молоко» -> молок + до 4 букв окончания: молоко/молока/молоком, но не «молотый»; «орехи» -> орехов/ореховая."""
    w = word.lower().replace("ё", "е")
    if len(w) >= 4 and w[-1] in "аеиоуыэюяйь":
        w = w[:-1]
    return re.compile(r"(?<![а-я])(?<!без )(?<!не )" + re.escape(w) + r"[а-я]{0,4}(?![а-я])")


def hits(text: str) -> list[str]:
    """Исключённые продукты, которые всё же встретились в ответе (кроме «без орехов», «не добавляй молоко»
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
