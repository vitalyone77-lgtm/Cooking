#!/bin/sh
# Все тесты по очереди на копии проекта в /tmp/ct. Печатает провалы, без настоящих публикаций и платежей.
cd /tmp/ct || exit 1
for t in test_lists test_social test_social2 test_social_images test_v2_bot test_missing test_reminder test_bot_timers test_bot_book test_diary test_payanyway test_book test_v2_web test_v2_admin test_help test_privacy test_vk_login test_web_missing test_abuse test_limits test_promo test_launch test_cards_pages test_vision test_double test_bot_pdf test_meal_order test_extras test_friends test_stars test_audit test_qty test_foodsearch_js test_buttons test_share test_gen; do
  out=$(PYTHONIOENCODING=utf-8 timeout 200 /root/Cooking/venv/bin/python /tmp/tests/$t.py 2>&1); rc=$?
  n=$(printf '%s\n' "$out" | grep -c '^PASS')
  f=$(printf '%s\n' "$out" | grep -E '^FAIL|Traceback|Error:' | cut -c1-260 | head -8)
  echo "== $t rc=$rc pass=$n"; [ -n "$f" ] && printf '%s\n' "$f"
done
