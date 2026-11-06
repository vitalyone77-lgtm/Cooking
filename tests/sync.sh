#!/bin/sh
# Копия проекта в /tmp/ct на сервере и тесты в /tmp/tests. Затем: ssh root@201.50.117.2 "sh /tmp/tests/run_all.sh"
cd "$(dirname "$0")/.." || exit 1
tar --force-local --exclude=venv --exclude=.venv --exclude=.git --exclude=.env --exclude='*.zip' --exclude='__pycache__' --exclude='*.db' --exclude=cooking_web_update --exclude='Claude outputs' -cf ../ct.tar . || exit 1
scp -q ../ct.tar root@201.50.117.2:/tmp/ct.tar && rm ../ct.tar
ssh -n -o BatchMode=yes root@201.50.117.2 "mkdir -p /tmp/tests"
scp -q tests/*.py tests/run_all.sh root@201.50.117.2:/tmp/tests/
ssh -n -o BatchMode=yes root@201.50.117.2 "rm -rf /tmp/ct && mkdir /tmp/ct && tar -xf /tmp/ct.tar -C /tmp/ct && cp /root/Cooking/.env /tmp/ct/ && echo synced"
