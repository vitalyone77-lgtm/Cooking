"""
Клиент официального (экспериментального) MCP-сервера ВкусВилла: поиск товаров с ценами и
создание ссылки-корзины.

ВАЖНО: писался без возможности проверить вживую (из среды разработки сервер недоступен), поэтому
разбор ответов сделан «терпимым» — ищем товары по нескольким возможным именам полей. Если на
твоём сервере формат другой, включи DEBUG-логи: сырой ответ пишется в лог, и поправить нужно
только функции _extract_products / _to_offer.

Протокол MCP «streamable HTTP»: initialize -> notifications/initialized -> tools/call.
Ответ может прийти как обычный JSON или как SSE-поток («data: {...}»).
"""
import json
import logging
import re

import httpx

import config
from .base import Offer, parse_pack_size_g

logger = logging.getLogger(__name__)

STORE_KEY = "vkusvill"
PROTOCOL_VERSION = "2025-03-26"


class VkusvillError(RuntimeError):
    pass


def _parse_rpc_body(resp: httpx.Response) -> dict:
    ctype = resp.headers.get("content-type", "")
    text = resp.text
    if "text/event-stream" in ctype or text.lstrip().startswith(("event:", "data:")):
        last = None
        for line in text.splitlines():
            if line.startswith("data:"):
                chunk = line[5:].strip()
                if chunk:
                    try:
                        last = json.loads(chunk)
                    except json.JSONDecodeError:
                        continue
        if last is None:
            raise VkusvillError("пустой SSE-ответ")
        return last
    if not text.strip():
        return {}
    return resp.json()


class VkusvillClient:
    def __init__(self, url: str):
        self.url = url
        self.session_id: str | None = None
        self._id = 0
        self._http = httpx.AsyncClient(timeout=20, follow_redirects=True)

    async def close(self):
        await self._http.aclose()

    def _headers(self) -> dict:
        h = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
        }
        if self.session_id:
            h["Mcp-Session-Id"] = self.session_id
        if config.VKUSVILL_MCP_API_KEY:
            h["Authorization"] = f"Bearer {config.VKUSVILL_MCP_API_KEY}"
        return h

    async def _rpc(self, method: str, params: dict | None = None, notify: bool = False) -> dict:
        payload = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        if not notify:
            self._id += 1
            payload["id"] = self._id
        resp = await self._http.post(self.url, headers=self._headers(), json=payload)
        resp.raise_for_status()
        sid = resp.headers.get("mcp-session-id")
        if sid:
            self.session_id = sid
        if notify:
            return {}
        body = _parse_rpc_body(resp)
        if "error" in body:
            raise VkusvillError(str(body["error"]))
        return body.get("result", {})

    async def start(self):
        await self._rpc("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "kitchen-bot", "version": "1.0"},
        })
        await self._rpc("notifications/initialized", notify=True)

    async def call_tool(self, name: str, arguments: dict):
        result = await self._rpc("tools/call", {"name": name, "arguments": arguments})
        if result.get("isError"):
            raise VkusvillError(f"{name}: {result}")
        if result.get("structuredContent") is not None:
            return result["structuredContent"]
        texts = [c.get("text", "") for c in result.get("content", []) if c.get("type") == "text"]
        joined = "\n".join(texts).strip()
        try:
            return json.loads(joined)
        except json.JSONDecodeError:
            return joined  # вернём как есть — вдруг это текст со ссылкой

    # ---- высокоуровневые операции ----

    async def search(self, query: str) -> list[Offer]:
        data = await self.call_tool("vkusvill_products_search", {"q": query})
        logger.debug("ВкусВилл search %r -> %s", query, str(data)[:1500])
        return [o for o in (_to_offer(p) for p in _extract_products(data)) if o]

    async def cart_link(self, items: list[tuple[str, int]]) -> str | None:
        """items: [(xml_id, количество)]. Возвращает ссылку на корзину или None."""
        items = items[:20]  # ограничение API
        products = [{"xml_id": pid, "q": min(int(q), 40)} for pid, q in items if pid]
        if not products:
            return None
        data = await self.call_tool("vkusvill_cart_link_create", {"products": products})
        return _find_url(data)


def _find_url(data) -> str | None:
    if isinstance(data, str):
        m = re.search(r"https?://\S+", data)
        return m.group(0).rstrip(").,") if m else None
    if isinstance(data, dict):
        for k in ("link", "url", "cart_link", "cart_url"):
            if isinstance(data.get(k), str) and data[k].startswith("http"):
                return data[k]
        for v in data.values():
            found = _find_url(v)
            if found:
                return found
    if isinstance(data, list):
        for v in data:
            found = _find_url(v)
            if found:
                return found
    return None


def _extract_products(data) -> list[dict]:
    """Находит в произвольном JSON список словарей, похожих на товары."""
    if isinstance(data, list):
        if data and all(isinstance(x, dict) for x in data) and any(
            ("name" in x or "title" in x) for x in data
        ):
            return data
        out = []
        for x in data:
            out.extend(_extract_products(x))
        return out
    if isinstance(data, dict):
        for key in ("products", "items", "data", "result", "results"):
            if key in data:
                found = _extract_products(data[key])
                if found:
                    return found
        out = []
        for v in data.values():
            if isinstance(v, (list, dict)):
                out.extend(_extract_products(v))
        return out
    return []


def _num(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        for k in ("value", "current", "price", "amount", "rub"):
            if k in value:
                return _num(value[k])
        return None
    m = re.search(r"\d+(?:[.,]\d+)?", str(value).replace(" ", "").replace(" ", ""))
    return float(m.group(0).replace(",", ".")) if m else None


def _to_offer(p: dict) -> Offer | None:
    name = str(p.get("name") or p.get("title") or "").strip()
    price = None
    for k in ("price", "current_price", "cost", "price_rub"):
        if k in p:
            price = _num(p[k])
            if price:
                break
    pid = str(p.get("xml_id") or p.get("id") or p.get("product_id") or "")
    if not name or not price or not pid:
        return None

    pack_g = parse_pack_size_g(name)
    approx = False
    if pack_g is None:
        # Иногда вес лежит в отдельном поле ("weight": "500 г", "unit": "г", "amount": 500)
        for k in ("weight", "volume", "unit_value", "pack", "size"):
            if k in p and p[k]:
                pack_g = parse_pack_size_g(str(p[k]))
                if pack_g:
                    break
    url = str(p.get("url") or p.get("link") or "")
    if url.startswith("/"):
        url = "https://vkusvill.ru" + url
    return Offer(store=STORE_KEY, name=name, price=price, pack_g=pack_g, url=url,
                 product_id=pid, approx=approx)
