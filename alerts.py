"""
Тревоги админу в Telegram (config.ADMIN_TG_IDS) — раз в час проверка, каждая тревога не чаще раза в сутки:
- подборов ИИ за сегодня уже 80% от общего дневного потолка (наплыв людей или кто-то накручивает);
- на балансе DeepSeek осталось меньше DEEPSEEK_BALANCE_ALERT_USD — пора пополнить, иначе подборы встанут.
"""
import logging
import time

import httpx

import accounts
import config
import usage

logger = logging.getLogger(__name__)


async def deepseek_balance() -> float | None:
    """Остаток на счёте DeepSeek в долларах (или None, если узнать не удалось)."""
    if not config.DEEPSEEK_API_KEY:
        return None
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get("https://api.deepseek.com/user/balance",
                                 headers={"Authorization": f"Bearer {config.DEEPSEEK_API_KEY}"})
            r.raise_for_status()
            infos = r.json().get("balance_infos") or []
        usd = [float(i.get("total_balance", 0)) for i in infos if i.get("currency") == "USD"]
        return usd[0] if usd else None
    except Exception as e:  # noqa: BLE001
        logger.warning("Не удалось узнать баланс DeepSeek: %s", e)
        return None


def _once_today(name: str) -> bool:
    key = f"alert:{name}:{time.strftime('%Y-%m-%d')}"
    if accounts.sess_get(key):
        return False
    accounts.sess_set(key, {"ts": time.time()})
    return True


async def _send(bot, text: str):
    for admin in config.ADMIN_TG_IDS:
        try:
            await bot.send_message(admin, text, parse_mode=None)
        except Exception:  # noqa: BLE001
            logger.warning("Не удалось отправить тревогу админу %s", admin)


async def check(bot) -> None:
    if not config.ADMIN_TG_IDS:
        return
    import plans
    cap = int(plans.cfg("DAILY_GEN_CAP"))
    used = usage.total_today()
    if cap > 0 and used >= cap * 0.8 and _once_today("cap"):
        await _send(bot, f"⚠️ Подборов ИИ сегодня уже {used} из {cap} (80% дневного потолка). Если это не наплыв "
                         "новых людей — возможно, кто-то накручивает. Потолок меняется в .env: DAILY_GEN_CAP.")
    bal = await deepseek_balance()
    if bal is not None and bal < config.DEEPSEEK_BALANCE_ALERT_USD and _once_today("balance"):
        await _send(bot, f"💸 На балансе DeepSeek осталось ${bal:.2f}. Пополни на platform.deepseek.com, "
                         "иначе подборы перестанут работать.")
