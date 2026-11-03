#!/bin/bash
# Показывает, какие настройки заданы в .env. Сами значения НЕ выводит.
# Запуск на сервере:  cd /root/Cooking && bash check_env.sh
cd "$(dirname "$0")" || exit 1
show() {
  for k in "$@"; do
    if grep -qE "^$k=.+" .env 2>/dev/null; then echo "  $k: задан"; else echo "  $k: ПУСТО"; fi
  done
}
echo "Основное (нужно всегда):"
show BOT_TOKEN DEEPSEEK_API_KEY WEB_SECRET WEB_APP_URL ADMIN_PASSWORD ADMIN_TG_IDS
echo "Партнёр (необязательно):"
show PARTNER_PASSWORD
echo "Сообщество ВКонтакте и канал, «рецепт дня»:"
show VK_GROUP_ID VK_GROUP_TOKEN TG_CHANNEL_ID SOCIAL_AUTOPOST
echo "Запасные ИИ и голос:"
show GROQ_API_KEY OPENROUTER_API_KEY TAVILY_API_KEY
echo "Оплата PayAnyWay (понадобится на следующем шаге):"
show PAYANYWAY_MNT_ID PAYANYWAY_CODE
echo "Вход через VK ID (понадобится на следующем шаге):"
show VK_CLIENT_ID VK_CLIENT_SECRET
echo "Картинки (IMAGE_PROVIDER: card, free, leonardo или off):"
show IMAGE_PROVIDER SOCIAL_IMAGES VK_IMAGE_MODE LEONARDO_API_KEY
