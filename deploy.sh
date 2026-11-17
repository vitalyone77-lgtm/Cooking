#!/usr/bin/env bash
# Безопасное обновление на сервере (запускается из update_cooking.bat или вручную: bash /root/Cooking/deploy.sh).
# git pull → проверка, что код вообще запускается → перезапуск бота и сайта → проверка через 20 секунд.
# Если что-то не так — автоматический откат на прошлую версию, чтобы бот не лежал.
set -u
cd /root/Cooking || exit 1

PREV=$(git rev-parse HEAD)
git pull -q || { echo "[ОШИБКА] git pull не удался — ничего не меняю"; exit 1; }
NEW=$(git rev-parse HEAD)
venv/bin/pip install -q -r requirements.txt || echo "[ВНИМАНИЕ] pip install завершился с ошибкой"

rollback() {
    echo "=== ОТКАТ на прошлую версию ${PREV:0:7} ==="
    git reset -q --hard "$PREV"
    systemctl restart Cooking cooking-web
    sleep 5
    systemctl is-active Cooking cooking-web
}

# 0) Копия баз ДО того, как новая версия их тронет (импорт ниже уже применяет новые таблицы, а откат возвращает только код).
#    Автоматически базу не откатываем — потерялись бы записи, сделанные после; копия лежит для ручного восстановления.
venv/bin/python -c "import backup, pathlib; f, e = backup.run(pathlib.Path('/root/backups/predeploy')); print('копия перед выкладкой:', f, e or '')"     || echo "[ВНИМАНИЕ] копия перед выкладкой не сделана"

# 1) Код импортируется (ловит опечатки и неверные импорты до перезапуска)
if ! venv/bin/python -c "import bot, reminders, web_api" ; then
    echo "[ОШИБКА] новая версия не запускается (ошибка выше)"
    rollback
    exit 1
fi

# 2) Перезапуск и проверка, что службы не падают по кругу
systemctl restart Cooking cooking-web
sleep 3
R0=$(systemctl show Cooking -p NRestarts --value)
W0=$(systemctl show cooking-web -p NRestarts --value)
sleep 20
R1=$(systemctl show Cooking -p NRestarts --value)
W1=$(systemctl show cooking-web -p NRestarts --value)

ok=1
systemctl is-active -q Cooking || { echo "[ОШИБКА] бот не запущен"; ok=0; }
systemctl is-active -q cooking-web || { echo "[ОШИБКА] сайт не запущен"; ok=0; }
[ "$R0" = "$R1" ] || { echo "[ОШИБКА] бот перезапускается по кругу"; ok=0; }
[ "$W0" = "$W1" ] || { echo "[ОШИБКА] сайт перезапускается по кругу"; ok=0; }
curl -fsS -o /dev/null http://127.0.0.1:8101/api/health || { echo "[ОШИБКА] сайт не отвечает"; ok=0; }

if [ "$ok" = 1 ]; then
    echo "=== ГОТОВО: работает версия ${NEW:0:7} ==="
    exit 0
fi
journalctl -u Cooking -u cooking-web -n 25 --no-pager
rollback
exit 1
