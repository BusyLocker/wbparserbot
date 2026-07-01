from telegram import ReplyKeyboardMarkup, KeyboardButton, Update, ReplyKeyboardRemove, InlineKeyboardButton, \
    InlineKeyboardMarkup, LabeledPrice
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackQueryHandler, \
    PreCheckoutQueryHandler, CallbackContext
from time import *
import logging
import os
from supabase import create_client, Client
from dotenv import load_dotenv
import json
import re
import requests
from datetime import datetime, timedelta
import schedule
import threading
import asyncio
from typing import Optional
import httpx
import random
#for the cookies
load_dotenv()
url: str = os.environ.get("SUPABASE_URL")
key: str = os.environ.get("SUPABASE_KEY")
Log_Level: str = os.environ.get(("Log_Level"))

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)

BASE_URL = "https://www.wildberries.ru/__internal/u-card/cards/v4/detail"

PARAMS_BASE = {
    "appType": "1",
    "curr": "rub",
    "dest": "1259570991",
    "spp": "30",
    "hide_vflags": "4294967296",
    "hide_dtype": "15",
    "lang": "ru",
    "ab_testing": "false",
}

# caching cookies
_cookies_cache: dict = {}
_headers_cache: dict = {}


def load_cookies_from_supabase() -> tuple[dict, dict]:
    #actually, loading cookies from supabase
    sb = create_client(url, key)
    result = sb.table("wb_cookies").select("cookies, headers").eq("id", 1).single().execute()
    cookies = result.data.get("cookies", {})
    headers = result.data.get("headers", {})
    return cookies, headers


def get_cookies() -> tuple[dict, dict]:
    #getting cookies from cache
    global _cookies_cache, _headers_cache
    if not _cookies_cache:
        logger.info("🔄 Загружаю куки из Supabase...")
        _cookies_cache, _headers_cache = load_cookies_from_supabase()
    return _cookies_cache, _headers_cache


def invalidate_cookies_cache():
    #I think there is no need to describe that
    global _cookies_cache, _headers_cache
    _cookies_cache = {}
    _headers_cache = {}


def _extract_nm_id(url) -> Optional[int]:
    #extract nm_id of product
    match = re.search(r"/catalog/(\d+)/", url)
    return int(match.group(1)) if match else None


async def _fetch(client: httpx.AsyncClient, nm_id: int, semaphore: asyncio.Semaphore) -> dict:
    #go to the wildberries and parse
    async with semaphore:
        cookies, extra_headers = get_cookies()

        params = {**PARAMS_BASE, "nm": str(nm_id)}
        headers = {
            "accept": "*/*",
            "accept-language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "referer": f"https://www.wildberries.ru/catalog/{nm_id}/detail.aspx",
            **extra_headers,  # deviceid, x-spa-version и др. из Supabase
        }

        for attempt in range(3):
            try:
                await asyncio.sleep(random.uniform(1.5, 3.0))
                resp = await client.get(
                    BASE_URL, params=params, headers=headers,
                    cookies=cookies, timeout=15,
                )

                if resp.status_code == 429:
                    wait = 15 * (attempt + 1)
                    logger.warning(f"⏳ [{nm_id}] Лимит запросов, жду {wait} сек...")
                    await asyncio.sleep(wait)
                    continue

                if resp.status_code in (403, 498):
                    logger.warning(f"🔄 [{nm_id}] Токен невалиден, жду обновления куков...")
                    invalidate_cookies_cache()
                    await asyncio.sleep(5 * (attempt + 1))  # don't be banned, wait.
                    cookies, extra_headers = get_cookies()
                    continue

                resp.raise_for_status()
                return resp.json()

            except Exception as e:
                logger.warning(f"⚠️ [{nm_id}] Попытка {attempt + 1}/3 не удалась: {type(e).__name__}: {e}")
                if attempt < 2:
                    await asyncio.sleep(2)

        logger.warning(f"❌ [{nm_id}] Все попытки исчерпаны, пропускаю")
        return {}


def _print_result(data, nm_id: int, url: str):
    #return result of parse
    products = data.get("products", [])
    if not products:
        logger.warning("❌ Товар не найден")
        return

    p = products[0]
    sizes = p.get("sizes", [])

    variants = []
    if len(sizes) > 1:
        for size in sizes:
            pd_ = size.get("price", {})
            stocks = size.get("stocks", [])
            in_stock = any(s.get("qty", 0) > 0 for s in stocks)
            if in_stock:
                variants.append((size.get("name") or "").strip())

        if variants:
            pd_ = sizes[-1].get("price", {})
            total = pd_.get("product", 0) // 100
            return total, variants
        else:
            return "Товара нет в наличии"
    else:
        size = sizes[0]
        pd_ = size.get("price", {})
        stocks = size.get("stocks", [])
        in_stock = any(s.get("qty", 0) > 0 for s in stocks)
        if in_stock:
            total = pd_.get("product", 0) // 100
            return total
        else:
            return "Товара нет в наличии"


async def _run(urls, name_list, output_dict):
    semaphore = asyncio.Semaphore(3)  # don't be banned x2
    async with httpx.AsyncClient() as client:
        tasks, valid = [], []
        now = -1
        all = []
        for url, name in zip(urls, name_list):
            now += 1
            nm_id = _extract_nm_id(url)
            if not nm_id:
                logger.warning(f"❌ Не удалось распознать: {url}")
                all.append(now)
                continue
            valid.append((url, nm_id))
            tasks.append(_fetch(client, nm_id, semaphore))

        for a in all:
            del name_list[a]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        for (url, nm_id), result, name in zip(valid, results, name_list):
            if isinstance(result, Exception):
                logger.debug(f"\n🔗 {url}")
                logger.error(f"❌ Ошибка: {result}")
            else:
                output = _print_result(result, nm_id, url)
                output_dict[name] = output
        return output_dict


def get_prices(url_list, name_list):
    #request to parse
    output_dict = {}
    asyncio.run(_run(url_list, name_list, output_dict))
    return output_dict