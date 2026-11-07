"""
«Получить бесплатно»: тариф за приглашённых друзей. Деньгами ничего не платим, только дни тарифа.
Друг пришёл по ссылке, вошёл и получил первый рецепт: неделя ему и пригласившему. Просто за вход не даём: новый аккаунт создать ничего не стоит.
Друг оплатил подписку: неделя пригласившего становится месяцем. Возврат оплаты откатывает эту прибавку.
Недель за приход не больше MAX_WEEKS за 30 дней, месяцы за оплату без ограничений: за них уже заплачены настоящие деньги.

Оплата и вход случаются и в процессе сайта, а писать людям может только бот, поэтому всё считает задача бота раз в 10 минут: сверяет факты с таблицей
friend_rewards. Повторный запуск ничего не начислит дважды.
"""
import logging
import secrets
import time

import accounts
import config
import plans
import usage

logger = logging.getLogger(__name__)

PREFIX = "fr_"
WEEK = 7
MONTH = 30
FRIEND_DAYS = 7
MAX_WEEKS = 5
WINDOW = 30 * 86400


def code_of(uid) -> str:
    row = accounts._q("SELECT code FROM friend_codes WHERE uid=? ORDER BY ts LIMIT 1", (str(uid),), one=True)
    if row:
        return row[0]
    code = PREFIX + secrets.token_urlsafe(9)
    accounts._q("INSERT INTO friend_codes(code, uid, ts) VALUES(?,?,?)", (code, str(uid), time.time()))
    return code


def links(uid) -> dict:
    code = code_of(uid)
    site = (config.WEB_APP_URL or "https://menu.ymayaka.ru").rstrip("/")
    return {"site": f"{site}/?ref={code}", "bot": f"https://t.me/{config.BOT_USERNAME}?start=ref_{code}"}


def stats(uid) -> dict:
    """Только числа: кого именно привёл человек, не показываем."""
    uid = str(uid)
    now = time.time()
    r = accounts._q("SELECT COUNT(*), COALESCE(SUM(paid), 0), COALESCE(SUM(days), 0) FROM friend_rewards WHERE inviter=?", (uid,), one=True)
    used = accounts._q("SELECT COUNT(*) FROM friend_rewards WHERE inviter=? AND base>0 AND ts>?", (uid, now - WINDOW), one=True)[0]
    return {"friends": r[0], "paid": r[1], "days": r[2], "weeks_left": max(0, MAX_WEEKS - used)}


def rules() -> list[str]:
    return [f"Друг открыл вашу ссылку, вошёл и получил первый рецепт: вам и ему по {WEEK} дней тарифа «{plans.PREMIUM_NAME}».",
            f"Друг оформил подписку: ваша неделя превращается в месяц ({MONTH} дней).",
            f"Недель за приход не больше {MAX_WEEKS} за 30 дней, месяцы за оплату без ограничений. Считаются только новые люди.",
            "Дни прибавляются к текущему сроку. Деньгами бонусы не выплачиваются."]


SHARE_TEXT = "Попробуй Кухонного помощника: рецепт из твоих продуктов за минуту, с калориями и списком покупок. По этой ссылке тебе неделя тарифа в подарок."


def _used(uid: str) -> bool:
    """Получил ли человек хотя бы один рецепт или меню: под своим аккаунтом или до входа, на привязанном устройстве."""
    ids = [uid] + [f"w{r[0]}" for r in accounts._q("SELECT dev_id FROM links WHERE tg_id=?", (int(uid),), all_=True)] if uid.isdigit() else [uid]
    return any(usage._count("SELECT COUNT(*) FROM gens WHERE uid=?", (i,)) for i in ids)


def _paid(uid: str) -> bool:
    """Настоящая оплата: не тест, не возврат, не служебный платёж на 10 ₽."""
    return bool(accounts._q("SELECT 1 FROM payments WHERE uid=? AND currency IN ('RUB', 'XTR') AND plan<>'check' LIMIT 1", (uid,), one=True))


def _until_text(uid) -> str:
    return time.strftime("%d.%m", time.localtime(accounts.premium_until(uid)))


def process() -> list[tuple[str, str]]:
    """Сверить приглашённых с фактами и начислить. -> [(кому, текст)]. Пока подписка выключена, дни не нужны: ничего не делаем, начислим после включения."""
    if not plans.paywall():
        return []
    out: list[tuple[str, str]] = []
    now = time.time()
    rows = accounts._q("SELECT r.uid, c.uid, w.base, w.days, w.paid FROM refs r JOIN friend_codes c ON c.code=r.ref LEFT JOIN friend_rewards w ON w.friend=r.uid "
                       "WHERE r.uid NOT LIKE 'w%' AND r.uid<>c.uid", all_=True)
    for friend, inviter, base, days, was_paid in rows:
        paid = _paid(friend)
        if days is None:
            if not paid and not _used(friend):
                continue
            taken = accounts._q("SELECT COUNT(*) FROM friend_rewards WHERE inviter=? AND base>0 AND ts>?", (inviter, now - WINDOW), one=True)[0]
            base = WEEK if taken < MAX_WEEKS else 0
            accounts._q("INSERT OR IGNORE INTO friend_rewards(friend, inviter, base, days, paid, ts) VALUES(?,?,?,?,0,?)", (friend, inviter, base, base, now))
            if base:
                accounts.add_bonus_days(inviter, base)
                out.append((inviter, f"🎁 По вашей ссылке пришёл друг: +{base} дней тарифа «{plans.PREMIUM_NAME}», он действует до {_until_text(inviter)}."))
            else:
                out.append((inviter, f"🎁 По вашей ссылке пришёл друг. За 30 дней уже начислено {MAX_WEEKS} недель, поэтому эта не добавлена. Если друг оформит подписку, вы получите {MONTH} дней."))
            if not paid:
                accounts.add_bonus_days(friend, FRIEND_DAYS)
                out.append((friend, f"🎁 Вас пригласил друг: +{FRIEND_DAYS} дней тарифа «{plans.PREMIUM_NAME}» в подарок, он действует до {_until_text(friend)}."))
            days, was_paid = base, 0
        if paid and not was_paid:
            add = MONTH - days
            accounts._q("UPDATE friend_rewards SET days=?, paid=1 WHERE friend=?", (MONTH, friend))
            accounts.add_bonus_days(inviter, add)
            out.append((inviter, f"🎉 Ваш друг оформил подписку, спасибо! Начислен месяц: +{add} дней тарифа «{plans.PREMIUM_NAME}», он действует до {_until_text(inviter)}."))
        elif was_paid and not paid:                # оплату вернули: прибавка за неё больше не положена
            accounts._q("UPDATE friend_rewards SET days=base, paid=0 WHERE friend=?", (friend,))
            accounts.add_bonus_days(inviter, -(MONTH - base))
    return out


async def job(bot) -> None:
    try:
        notes = process()
    except Exception:  # noqa: BLE001 — следующий запуск через 10 минут досчитает
        logger.exception("бонусы за друзей")
        return
    for uid, text in notes:
        if not uid.isdigit() or int(uid) >= accounts.VK_OFFSET:      # ВКонтакте не пишем: человек увидит дни на сайте
            continue
        try:
            await bot.send_message(int(uid), text, parse_mode=None)
            usage.event("friend_note", uid)
        except Exception as e:  # noqa: BLE001 — человек мог заблокировать бота
            logger.info("Сообщение о бонусе %s не ушло: %r", uid, e)
