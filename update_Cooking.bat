@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul

REM ==== Настройки — поправь, если у тебя другие значения ====
set PROJECT_DIR=C:\Users\user\Desktop\ai_learning\Cooking
set SSH_HOST=root@201.50.117.2
set REMOTE_DIR=~/Cooking
set SERVICE=cooking

echo ============================================
echo   Обновление бота "Cooking"
echo ============================================
echo.

cd /d "%PROJECT_DIR%" || (echo Не найдена папка проекта: %PROJECT_DIR% & pause & exit /b 1)

echo --- Что изменилось ---
git status --short
echo.

set /p COMMIT_MSG="Комментарий к изменениям (Enter = 'update'): "
if "%COMMIT_MSG%"=="" set COMMIT_MSG=update

echo.
echo --- Коммитим и отправляем на GitHub ---
git add .
git commit -m "%COMMIT_MSG%"
if errorlevel 1 (
    echo (нечего коммитить, либо ошибка коммита — продолжаю пуш на всякий случай)
)
git push origin main
if errorlevel 1 (
    echo ОШИБКА при push на GitHub. Останавливаюсь.
    pause
    exit /b 1
)

echo.
echo --- Обновляем и перезапускаем на сервере ---
ssh %SSH_HOST% "cd %REMOTE_DIR% && git pull && ( git diff --name-only HEAD@{1} HEAD | grep -q requirements.txt && source venv/bin/activate && pip install -r requirements.txt && deactivate || true ) && systemctl restart %SERVICE% && sleep 2 && systemctl status %SERVICE% --no-pager -l"

echo.
echo ============================================
echo   Готово. Смотри статус выше: должно быть
echo   "active (running)".
echo ============================================
pause