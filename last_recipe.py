"""
Последний показанный пользователю рецепт — хранится отдельно от анкеты (FSM),
чтобы кнопка "⭐ В избранное" работала даже после того, как анкета уже была
сброшена (например, пользователь нажал "Подобрать ещё рецепт" и вернулся
к старому сообщению). Живёт в памяти процесса — этого достаточно для одного бота.
"""
_last_recipes: dict[int, dict] = {}


def set_last_recipe(chat_id: int, title: str, text: str, cuisine: str) -> None:
    _last_recipes[chat_id] = {"title": title, "text": text, "cuisine": cuisine}


def get_last_recipe(chat_id: int) -> dict | None:
    return _last_recipes.get(chat_id)
