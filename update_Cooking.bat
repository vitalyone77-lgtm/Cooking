@echo off
setlocal

rem === Обновление Cooking: компьютер -> GitHub -> сервер (1 клик) ===

set PROJECT_DIR=C:\Users\user\Desktop\ai_learning\Cooking
set SERVER=root@201.50.117.2
set REMOTE_DIR=~/Cooking
set SERVICE=Cooking

echo ===============================================
echo   Обновление: %SERVICE%
echo ===============================================
echo.

cd /d "%PROJECT_DIR%"
if errorlevel 1 (
    echo [ОШИБКА] Не нашёл папку проекта: %PROJECT_DIR%
    pause
    exit /b 1
)

git rev-parse --is-inside-work-tree >nul 2>&1
if errorlevel 1 (
    echo [ОШИБКА] Это не git-репозиторий: %PROJECT_DIR%
    pause
    exit /b 1
)

echo --- Что изменилось ---
git status
echo.

set /p CHANGES=Есть изменения для отправки? (Enter = да, n = отменить):
if /i "%CHANGES%"=="n" (
    echo Отменено.
    pause
    exit /b 0
)

set /p MSG=Комментарий к изменениям (commit message):
if "%MSG%"=="" set MSG=обновление

echo.
echo --- Добавляю и коммичу ---
git add .
git commit -m "%MSG%"

echo.
echo --- Отправляю на GitHub ---
git push
if errorlevel 1 (
    echo Обычный push не сработал, пробую "git push origin HEAD:main"...
    git push origin HEAD:main
    if errorlevel 1 (
        echo [ОШИБКА] Не удалось отправить на GitHub. Останавливаюсь, на сервер не пойду.
        pause
        exit /b 1
    )
)

echo.
echo --- Подключаюсь к серверу и обновляю бота ---
ssh %SERVER% "bash /root/Cooking/deploy.sh"

echo.
echo ===============================================
echo   Проверь в выводе выше: "ГОТОВО" - обновлено; "ОТКАТ" - была ошибка, работает прошлая версия.
echo   Потом открой бота в Telegram и проверь.
echo ===============================================
echo.
pause
