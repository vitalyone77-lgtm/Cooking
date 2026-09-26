"""
Состояния анкеты (FSM) — пошаговый сбор предпочтений пользователя
перед подбором рецепта.
"""
from aiogram.fsm.state import State, StatesGroup


class RecipeForm(StatesGroup):
    cuisine = State()          # тип кухни
    macro_goal = State()       # цель по КБЖУ (только для ветки "Подобрать по КБЖУ")
    preferred = State()        # предпочитаемые продукты (свободный текст)
    excluded = State()         # исключить продукты (свободный текст)
    time = State()             # время на готовку
    servings = State()         # количество порций
    appliance = State()        # режим приготовления
    confirm = State()          # подтверждение перед поиском


class FavoritesForm(StatesGroup):
    search = State()           # ожидание текста поискового запроса по избранному


class DayMenuForm(StatesGroup):
    """Меню на день: несколько приёмов пищи одним запросом с общим списком покупок."""
    meals = State()             # выбор приёмов пищи (завтрак/обед/ужин/перекус/бранч)
    cuisine = State()
    macro_goal = State()        # цель по КБЖУ на ВЕСЬ день (только ветка "Подобрать по КБЖУ")
    preferred = State()
    excluded = State()
    time = State()
    servings = State()
    appliance = State()
    confirm = State()
