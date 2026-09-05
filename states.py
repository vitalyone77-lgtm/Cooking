"""
Состояния анкеты (FSM) — пошаговый сбор предпочтений пользователя
перед подбором рецепта.
"""
from aiogram.fsm.state import State, StatesGroup


class RecipeForm(StatesGroup):
    cuisine = State()          # тип кухни
    preferred = State()        # предпочитаемые продукты (свободный текст)
    excluded = State()         # исключить продукты (свободный текст)
    time = State()             # время на готовку
    servings = State()         # количество порций
    appliance = State()        # режим приготовления
    confirm = State()          # подтверждение перед поиском
