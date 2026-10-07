# Запуск веб-версии: пошаговая инструкция

Цель: чтобы по адресу **https://menu.ymayaka.ru** всегда открывался «Кухонный помощник» (рецепты, меню на день,
план питания, PDF, установка на главный экран) — независимо от Telegram и от Тильды.

**Статус: ✅ ВСЁ ЗАПУЩЕНО 01.10.2026.** Сайт https://menu.ymayaka.ru работает: поддомен указывает на сервер, служба `cooking-web` (порт 8101)
запущена, nginx настроен, сертификат Let's Encrypt выпущен (до 30.12.2026, продлевается автоматически), админка закрыта паролем из `.env`.
Ниже — та же инструкция как справочник (повторить на новом сервере, восстановить после сбоя или понять, что где настроено).
Важно: **с компьютера в сети с фильтром FortiGate сайт может не открываться или показывать ошибку сертификата** — проверяй с телефона (мобильная сеть).

Как устроено в двух словах: на сервере будет работать вторая служба `cooking-web` (порт 8101, наружу не торчит);
nginx принимает запросы на `menu.ymayaka.ru` по HTTPS и передаёт их этой службе. Бот (`Cooking`) работает отдельно, как раньше.

> Что уже занято на сервере: порт **8081** — `fitness_bot`, **8091** — `quest.pump-um.ru`, **3000** — pm2-приложение.
> Поэтому веб берёт **8101**. nginx и certbot уже установлены, порты 80/443 открыты.
> Папка проекта на сервере — `/root/Cooking` (с большой C: Linux различает регистр), окружение — `venv`.

---

## Шаг 0. Что понадобится
- Компьютер с проектом и файл `update_cooking.bat` (двойной клик запускает обновление).
- Доступ к серверу по SSH: в PowerShell или Git Bash команда `ssh root@201.50.117.2` (пароль не спрашивает, если ключ уже настроен — так же работает `update_cooking.bat`).
- Придумать **пароль для админки** (20+ символов, только буквы и цифры) и записать себе.

## Шаг 1. Поддомен — ГОТОВО ✅
Ничего делать не нужно. Для справки, как проверить: в PowerShell `nslookup menu.ymayaka.ru` должен показать `201.50.117.2`.

## Шаг 2. Подготовить код на компьютере
1. Открой `.env.example` — в самом верху есть чек-лист «ЧТО ТЕБЕ НУЖНО ДОБАВИТЬ».
2. (Желательно) в своём локальном `.env` замени `GROQ_MODEL=openai/gpt-oss-120b` на `GROQ_MODEL=llama-3.3-70b-versatile` — старая модель иногда отвечает пустым текстом. То же сделаем на сервере в шаге 4.
3. Посмотри, что уйдёт в git: в папке проекта выполни `git status`. В список попадут **все** изменения в папке, в том числе сделанные другими сессиями (папка `stores/`, переписанное меню бота и т.п.) — это нормально, если ты хочешь выложить всё. Секреты (`.env`) и рабочие данные (`*.json`, `web_data/`) в git не попадают — они в `.gitignore`.

## Шаг 3. Отправить код на сервер
1. Двойной клик по `update_cooking.bat`.
2. На вопрос «Есть изменения для отправки?» — Enter. Комментарий: например, `веб-версия`.
3. Скрипт: делает коммит → отправляет на GitHub → заходит на сервер → `git pull` → `pip install -r requirements.txt` (поставит fastapi, uvicorn, reportlab…) → перезапускает бота.
4. Ожидаемый результат в конце: строка `Active: active (running)` у `Cooking.service`.
   Если `pip install` долго «молчит» — это нормально, ждёт сеть, не закрывай окно.

Что делать, если ошибка:
- `[ОШИБКА] Не удалось отправить на GitHub` — скрипт остановится, на сервер не пойдёт; пришли мне текст ошибки.
- `git pull` на сервере ругается на «локальные изменения» — пришли вывод, не удаляй файлы сам.

## Шаг 4. Настроить сервер (по SSH)
Открой PowerShell (или Git Bash) и войди: `ssh root@201.50.117.2`. Дальше команды копируй целиком, по одной.

### 4.1. Перейти в папку проекта и проверить, что код приехал
```bash
cd /root/Cooking
git log --oneline -1
ls web_api.py web/index.html web/icons
```
Ожидаем: твой комментарий из шага 3 и три найденных пути без ошибок.

### 4.2. Добавить три строки в `.env`
```bash
cd /root/Cooking
echo "" >> .env
echo "WEB_APP_URL=https://menu.ymayaka.ru" >> .env
echo "WEB_SECRET=$(venv/bin/python -c 'import secrets;print(secrets.token_urlsafe(32))')" >> .env
echo "ADMIN_PASSWORD=ВСТАВЬ_СВОЙ_ПАРОЛЬ_20_СИМВОЛОВ" >> .env
```
Замени `ВСТАВЬ_СВОЙ_ПАРОЛЬ_20_СИМВОЛОВ` на придуманный пароль (без пробелов и знаков `$ # " '`).
(Первая строка `echo ""` защищает от склейки с последней строкой файла.)

Проверка (покажет только названия, без значений):
```bash
grep -oE '^(WEB_APP_URL|WEB_SECRET|ADMIN_PASSWORD)=' .env
```
Ожидаем ровно три строки. Если какая-то появилась дважды — открой `nano .env`, оставь одну (Ctrl+O, Enter, Ctrl+X).

### 4.3. Поправить резервную модель Groq (по желанию, 10 секунд)
```bash
sed -i 's|^GROQ_MODEL=.*|GROQ_MODEL=llama-3.3-70b-versatile|' .env
grep '^GROQ_MODEL=' .env
```

### 4.4. Создать службу веб-версии
```bash
cat > /etc/systemd/system/cooking-web.service <<'EOF'
[Unit]
Description=Cooking web
After=network.target

[Service]
WorkingDirectory=/root/Cooking
ExecStart=/root/Cooking/venv/bin/uvicorn web_api:app --host 127.0.0.1 --port 8101
Restart=always

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now cooking-web
sleep 3
systemctl status cooking-web --no-pager | head -5
curl -s http://127.0.0.1:8101/api/health
```
Ожидаем: `Active: active (running)` и ответ `{"ok":true}`.
Если нет: `journalctl -u cooking-web -n 40 --no-pager` — пришли мне вывод. Частые причины: не поставились зависимости (`/root/Cooking/venv/bin/pip install -r /root/Cooking/requirements.txt`), опечатка в `.env`.

### 4.5. Подключить nginx (приём запросов на menu.ymayaka.ru)
```bash
cat > /etc/nginx/sites-available/cooking-web <<'EOF'
server {
    server_name menu.ymayaka.ru;
    client_max_body_size 3m;
    location / {
        proxy_pass http://127.0.0.1:8101;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
EOF
ln -s /etc/nginx/sites-available/cooking-web /etc/nginx/sites-enabled/cooking-web
nginx -t && systemctl reload nginx
curl -s -o /dev/null -w "HTTP-код: %{http_code}\n" http://menu.ymayaka.ru/api/health
```
Ожидаем: `syntax is ok`, `test is successful` и `HTTP-код: 200`.
Другие сайты (quest.pump-um.ru и остальные) эти команды не затрагивают — добавляется только новый файл.

### 4.6. Выпустить сертификат HTTPS
```bash
certbot --nginx -d menu.ymayaka.ru
```
Certbot задаст вопрос про перенаправление — введи **2** (перенаправлять на https). Email не спросит, если аккаунт уже есть (он есть — сертификаты для других поддоменов уже выпущены).
Проверка:
```bash
curl -s -o /dev/null -w "HTTP-код: %{http_code}\n" https://menu.ymayaka.ru/api/health
```
Ожидаем `200`. Если certbot пишет про ошибку проверки домена — проверь DNS (шаг 1) и повтори команду через 10 минут.

### 4.7. Перезапустить обе службы и выйти
```bash
systemctl restart Cooking cooking-web
systemctl is-active Cooking cooking-web
exit
```
Ожидаем две строки `active`.

## Шаг 5. Проверить в браузере и на телефоне
1. Открой **https://menu.ymayaka.ru** — должна быть страница «Что приготовим?» с зелёной карточкой «На главный экран».
2. Нажми «Подобрать» (выбери «Рецепт», любые параметры). Через 20–60 секунд появится рецепт; под ним «📄 Скачать PDF» — файл должен скачаться, внутри русский текст.
3. Открой `https://menu.ymayaka.ru/admin` → введи `ADMIN_PASSWORD` → увидишь статистику (1 устройство — это ты) и форму для рекламных баннеров.
4. В Telegram отправь боту `/app` — должен прийти адрес сайта.

## Шаг 6. Поставить как приложение на телефон
- **Android (Chrome):** открой сайт → в карточке «На главный экран» нажми «Установить». Если вместо этого «Как?» — меню браузера «⋮» → «Добавить на главный экран».
- **iPhone:** только из **Safari** (из Telegram нельзя: сначала «⋯» → «Открыть в Safari») → «Поделиться» (квадрат со стрелкой) → «На экран Домой» → «Добавить».
- Иконка (кастрюля с паром) появится на экране, откроется без адресной строки. Сохранённое избранное доступно и без интернета.

## Шаг 7. Тильда
**Вариант А (рекомендую, любой тариф): ссылка.** В Тильде добавь кнопку (или пункт меню) → поле «Ссылка»: `https://menu.ymayaka.ru` → включи «Открывать в новом окне» → опубликуй страницу.

**Вариант Б (платный тариф Тильды): встроить на страницу.** Библиотека блоков → «Другое» → «T123 HTML-код» → «Контент» → вставь:
```html
<iframe src="https://menu.ymayaka.ru/" title="Кухонный помощник"
  style="width:100%;height:900px;border:0;border-radius:12px"
  allow="screen-wake-lock; clipboard-write" loading="lazy"></iframe>
```
Что нужно знать про вариант Б:
- Внутри встроенного окна нельзя «установить на главный экран» — оно само предложит «Открыть приложение» отдельно.
- Избранное и меню, созданные во встроенном окне, **не появятся** в обычном сайте/приложении (браузеры хранят данные встроенных страниц отдельно). Для постоянного пользования — вариант А.
- В nginx не добавляй заголовки `X-Frame-Options` и `Content-Security-Policy: frame-ancestors` — они запретят встраивание.

## Шаг 8. Дальше, при обновлениях
- Любое изменение кода: двойной клик `update_cooking.bat` — он теперь сам перезапускает и бота, и веб (если служба `cooking-web` уже создана).
- Резервная копия данных веб-пользователей: папка `/root/Cooking/web_data/` (база `web.db`, избранное, меню). Скопировать на компьютер: `scp -r root@201.50.117.2:/root/Cooking/web_data ./backup_web_data`.
- Смотреть журнал: `journalctl -u cooking-web -n 50 --no-pager` (веб), `journalctl -u Cooking -n 50 --no-pager` (бот). Имена служб с учётом регистра.

## Если что-то не работает
| Симптом | Что проверить |
|---|---|
| Сайт не открывается, «502 Bad Gateway» | `systemctl status cooking-web --no-pager`; порт в nginx должен быть 8101 |
| «Подключение не защищено» | не выпущен сертификат — шаг 4.6; DNS — шаг 1 |
| Страница открылась, но «Подобрать» даёт ошибку | ключи ИИ в `.env` (`journalctl -u cooking-web`), лимиты `WEB_LIMIT_*` |
| «На сегодня лимитов больше нет» | лимиты на устройство: 3 блюда и 1 меню на день в сутки, 1 план на 3 дня; меняются в `.env` (`WEB_LIMIT_RECIPE`, `WEB_LIMIT_DAYMENU`, `WEB_LIMIT_WEEK`), затем `systemctl restart cooking-web` |
| Админка не пускает | пароль из `ADMIN_PASSWORD`; после 5 неверных попыток — пауза 10 минут |
| PDF не скачивается, открывается окно печати | на сервере нет шрифта — проверь `ls /usr/share/fonts/truetype/dejavu` (должен быть `DejaVuSans.ttf`), или задай `PDF_FONT` в `.env` |
| После `update_cooking.bat` бот не отвечает | `journalctl -u Cooking -n 50 --no-pager`; откат на версию, которая работала до этого обновления: `cd /root/Cooking && git reset --hard 6bd9cbb && systemctl restart Cooking` (ещё глубже — тег `before-week-menu`: состояние до плана питания) |

Полное описание проекта (архитектура, файлы, как всё работает, что осталось сделать) — в `PROJECT.md`.
