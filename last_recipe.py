"""
Последний показанный пользователю рецепт — хранится отдельно от анкеты (FSM),
чтобы кнопка "⭐ В избранное" работала даже после того, как анкета уже была
сброшена, и чтобы в «Создать» можно было заново открыть последний рецепт.
Лежит в базе (accounts.sess) — переживает перезапуск бота.
"""
import accounts


def set_last_recipe(chat_id: int, title: str, text: str, cuisine: str) -> None:
    key = f"last:{chat_id}"
    accounts.sess_set(key, {"title": title, "text": text, "cuisine": cuisine})
    accounts.sess_mark_forever(key)


def get_last_recipe(chat_id: int) -> dict | None:
    r = accounts.sess_get(f"last:{chat_id}")
    return r if r and r.get("text") else None
