from telegram import ReplyKeyboardMarkup, KeyboardButton, Update, ReplyKeyboardRemove, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackQueryHandler, PreCheckoutQueryHandler, CallbackContext
from time import *
from logic import *
import logging
import os
from supabase import create_client, Client
from dotenv import load_dotenv
import json
import re
import requests
from datetime import datetime, timedelta
import pytz
import schedule
import threading



load_dotenv()
YOUR_ADMIN_ID: int = int(os.environ.get("YOUR_ADMIN_ID"))
url: str = os.environ.get("SUPABASE_URL")
key: str = os.environ.get("SUPABASE_KEY")
Token: str = os.environ.get("Token")
Provider_Token: str = os.environ.get("Provider_Token")
Log_Level: str = os.environ.get(("Log_Level"))
supabase: Client = create_client(url, key)

logging.basicConfig(
    level=Log_Level,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


ExpTimeOfDeliv = [1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24]

PRICES = {
    "econom": [LabeledPrice(label="Тариф Эконом", amount=79000)],
    "standart": [LabeledPrice(label="Тариф Стандарт", amount=139000)],
    "pro": [LabeledPrice(label="Тариф Про", amount=199000)]
}

TARIFF_NAMES = {
    "econom": "🚀 Эконом",
    "standart": "💼 Стандарт",
    "pro": "🏆 Про",
    "trial": "🔥 Пробный период"
}


def run_scheduler():
    """Запускает проверку цен по расписанию"""
    while True:
        current_hour = datetime.now(pytz.timezone('Europe/Moscow')).hour
        target_hour = current_hour + 1  # так как у тебя часы 1-24

        logger.info(f"[{datetime.now()}] Проверка для часа: {target_hour}")

        # Получаем ВСЕХ пользователей
        users = supabase.table('items') \
            .select('id, timeofdelivery') \
            .execute()

        if not users.data:
            logger.info("Нет пользователей")
            sleep(60)
            continue

        # Фильтруем вручную
        users_to_check = []
        for user in users.data:
            delivery_hours = user.get('timeofdelivery')
            if delivery_hours and target_hour in delivery_hours:
                users_to_check.append(user)
                logger.debug(f"✅ Пользователь {user['id']} - часы: {delivery_hours}")

        logger.info(f"Найдено: {len(users_to_check)} пользователей")

        # Запускаем проверку
        for user in users_to_check:
            thread = threading.Thread(target=check_and_notify, args=(user['id'],))
            thread.start()

        # Ждём час
        sleep(3600)


def check_and_notify(user_id):
    """
    Проверяет цены для пользователя и отправляет уведомление
    """
    # Проверяем тариф
    if is_tariff_expired(user_id):
        return
    urls = []
    names = []
    # Получаем товары пользователя
    result = supabase.table('url_list') \
        .select('name, url') \
        .eq('id', user_id) \
        .execute()

    if not result.data:
        return

    for item in result.data:
        urls.append(item["url"])
        names.append(item["name"])

    # Парсим цены
    final_dict = get_prices(urls,names)
    message00 = "📋 Результаты проверки цен:\n" + "=" * 30 + "\n"
    for i, (name, priceorsize) in enumerate(final_dict.items(), 1):
        message00 += f"{i}. 📦 {name}\n"
        if not isinstance(priceorsize, tuple):
            message00 += f"   💰 {priceorsize}₽\n"
        else:
            message00 += f"   💰 {priceorsize[0]}₽\n"
            message00 += f" Размерные линейки {priceorsize[1]}\n"
        message00 += "-" * 30 + "\n"

    # Отправляем уведомление
    try:
        bot.send_message(chat_id=user_id, text=message00)
        logger.debug(f"Уведомление отправлено пользователю {user_id}")
    except Exception as e:
        logger.error(f"Ошибка отправки {user_id}: {e}")


def del_list(update: Update,context: CallbackContext):
    user_id = update.effective_user.id
    result = supabase.table('url_list') \
        .select('name, url') \
        .eq('id', user_id) \
        .execute()

    if not result.data:
        update.message.reply_text("📭 Нет товаров\n")
    else:
        update.message.reply_text("Отправьте название вашего товара как в списке:", reply_markup=ReplyKeyboardRemove())
        context.user_data["step"] = "waiting_for_del"


def give_list(update: Update):
    user_id = update.effective_user.id
    result = supabase.table('url_list') \
        .select('name, url') \
        .eq('id', user_id) \
        .execute()

    # Создаём message заранее
    message = "📋 Ваши товары:\n" + "=" * 30 + "\n"

    if not result.data:
        message += "📭 Нет товаров\n"
    else:
        for i, item in enumerate(result.data, 1):
            name = item.get('name', 'Без названия')
            url = item.get('url')
            message += f"{i}. 📦 {name}\n"
            message += f"   🔗 {url}\n"
            message += "-" * 30 + "\n"
    update.message.reply_text(message)

def is_not_unique(column, value, update: Update):
    result = supabase.table("url_list") \
        .select(column) \
        .eq('id', update.effective_user.id) \
        .eq(column, value) \
        .execute()
    return len(result.data) > 0

def can_add_url(update: Update):
    user_id = update.effective_user.id
    result = supabase.table('url_list').select('id').eq('id', user_id).execute()
    products_count = len(result.data)
    result = supabase.table('users') \
        .select('tariff') \
        .eq('id', user_id) \
        .execute()

    if result.data:
        tariff = result.data[0]['tariff']
    if tariff == "pro":
        if products_count >= 50:
            update.message.reply_text(
                "❌ Превышен лимит на товары. В будущем планируется улучшенный тариф. Будем рады, если вы напишете в поддержку и дадите нам обратную связь!")
            return False
        return True

    elif tariff == "standart":
        if products_count >= 20:
            update.message.reply_text(
                "❌ Превышен лимит на товары. Вы можете приобрести тариф Про для увеличения лимита!")
            return False
        return True

    elif tariff == "econom":
        if products_count >= 10:
            update.message.reply_text(
                "❌ Превышен лимит на товары. Вы можете приобрести тариф Стандарт или Про для увеличения лимита!")
            return False
        return True

    elif tariff == "trial":
        if products_count >= 1:
            update.message.reply_text(
                "❌ Превышен лимит на товары. Для того чтобы отслеживать больше товаров - вы можете приобрести один из наших тарифов!")
            return False
        return True


def user_exists(idi):
    try:
        result = supabase.table('users').select('id').eq('id', idi).execute()
        return len(result.data) > 0
    except:
        return False

def user_iscool(idi):
    try:
        result = supabase.table('items').select('timeofdelivery').eq('id', idi).execute()
        return len(result.data) > 0
    except:
        return False


def is_tariff_expired(idi):
    """Возвращает True, если тариф истёк"""
    result = supabase.table('users') \
        .select('ended_at') \
        .eq('id', idi) \
        .execute()

    if not result.data or not result.data[0].get('ended_at'):
        return False

    ended_at = result.data[0]['ended_at']
    ended_date = datetime.fromisoformat(ended_at).date()

    # Текущая дата в Москве
    now_date = datetime.now(pytz.timezone('Europe/Moscow')).date()

    # Дата через 30 дней после окончания
    check_date = ended_date + timedelta(days=30)
    if now_date > check_date:
        supabase.table('url_list').delete().eq('user_id', idi).execute()

    return datetime.now(pytz.timezone('Europe/Moscow')).date() > ended_date

def test_connection():
    try:
        result = supabase.table('users').select('*').limit(1).execute()
        logger.info("✅ Supabase подключён!")
        return True
    except Exception as e:
        logger.critical(f"❌ Ошибка: {e}")
        return False

test_connection()

def starting_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("🛠Тарифы🛠")],
        [KeyboardButton("️ℹ️️️️Информацияℹ️")]
    ], resize_keyboard=True)

def starting_keyboard2():
    return ReplyKeyboardMarkup([
        [KeyboardButton("🛠Тарифы🛠")]
    ], resize_keyboard=True)

def usual_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("➕Добавить товар➕")],
        [KeyboardButton("📋Список товаров📋")],
        [KeyboardButton("🗑Удалить товар🗑")],
        [KeyboardButton("⚙️Перенастроить⚙️")]
    ], resize_keyboard=True)

def tariff_keyboard():
    keyboard = [
        [InlineKeyboardButton("🚀Эконом", callback_data="econom")],
        [InlineKeyboardButton("💼Стандарт", callback_data="standart")],
        [InlineKeyboardButton("🏆Про", callback_data="pro")],
        [InlineKeyboardButton("У меня уже есть тариф.", callback_data="trial")],
    ]
    return InlineKeyboardMarkup(keyboard)


def update_token(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    if user_id != YOUR_ADMIN_ID:
        return

    if not context.args:
        update.message.reply_text("Использование: /update_token <токен>")
        return

    token = context.args[0]
    sb = create_client(url, key)
    sb.table("wb_cookies").update({
        "cookies": {"x_wbaas_token": token, "_wbauid": "8652721901780603036"},
        "updated_at": datetime.utcnow().isoformat(),
    }).eq("id", 1).execute()

    invalidate_cookies_cache()
    update.message.reply_text("✅ Токен обновлён!")

def start(update: Update, context: CallbackContext):
    if user_exists(update.effective_user.id):
        if user_iscool(update.effective_user.id):
            context.user_data["step"] = "main_menu"
            update.message.reply_text(
                "С возвращением!",
            )
            # Показываем главное меню
            update.message.reply_text(
                "Главное меню:",
                reply_markup=usual_keyboard()
            )
        else:
            update.message.reply_text(
                "🎯 Бот для ценового мониторинга Яндекс Маркета\n"
                "Выберите тариф, отправьте ссылку на товар, и я начну следить за ценой!",
                reply_markup=starting_keyboard()
            )
            context.user_data["step"] = "starting"

    else:
        update.message.reply_text(
            "🎯 Бот для ценового мониторинга Wildberries\n"
            "Выберите тариф, отправьте ссылку на товар, и я начну следить за ценой!",
            reply_markup=starting_keyboard()
        )

        supabase.table('users').insert({
            'id': update.effective_user.id,
            'tariff': 'trial',
            'ended_at': (datetime.now(pytz.timezone('Europe/Moscow')).date() + timedelta(days=14)).strftime("%Y-%m-%d")
        }).execute()


        context.user_data["step"] = "starting"

def button_handler(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()

    tariff = query.data

    if tariff == "trial":

        if not is_tariff_expired(update.effective_user.id):
            query.message.reply_text(
                "Перейдём к началу работы. Отправьте в какой час по Московскому часовому поясу вам будет удобно получать уведомления (от 1 до 24)",reply_markup=ReplyKeyboardRemove()
            )
            context.user_data["step"] = "start_work"
        elif is_tariff_expired(update.effective_user.id):
            query.message.reply_text(
                "К сожалению, ваш тариф закончился.",
                reply_markup=starting_keyboard()
            )
            context.user_data["step"] = "starting"
        else:
            query.message.reply_text(
                "Что-то пошло не так. Если вы уверены, что ваш тариф не истёк, обратитесь в поддержку.",
                reply_markup=starting_keyboard()
            )
            context.user_data["step"] = "starting"

    else:
        send_invoice(update, context, tariff)

def send_invoice(update: Update, context: CallbackContext, tariff: str):
    query = update.callback_query
    title = f"Тариф {TARIFF_NAMES[tariff]}"
    description = f"Оплата тарифа {TARIFF_NAMES[tariff]}"
    payload = tariff
    currency = "RUB"
    prices = PRICES[tariff]
    query.message.reply_invoice(
        title=title,
        description=description,
        payload=payload,
        provider_token=Provider_Token,
        currency=currency,
        prices=prices,
        need_email=True,
        need_phone_number=False,
        need_shipping_address=False,
        is_flexible=False,
    )

def pre_checkout_handler(update: Update, context: CallbackContext):
    query = update.pre_checkout_query
    query.answer(ok=True)

def successful_payment_handler(update: Update, context: CallbackContext):
    payment = update.message.successful_payment
    tariff = payment.invoice_payload

    update.message.reply_text(
        f"✅ Оплата прошла успешно!\n"
        f"Тариф {TARIFF_NAMES[tariff]} активирован.\n"
        f"Спасибо за покупку! 🎉"
    )

    supabase.table('users').update({
        'id': update.effective_user.id,
        'tariff': tariff,
        'ended_at': (datetime.now(pytz.timezone('Europe/Moscow')).date() + timedelta(days=30)).strftime("%Y-%m-%d")
    }).execute()

    update.message.reply_text(
        "Перейдём к началу работы. Отправьте в какой час по Московскому часовому поясу вам будет удобно получать уведомления (от 1 до 24)",reply_markup=ReplyKeyboardRemove()
    )
    context.user_data["step"] = "start_work"


def handle_choice(update: Update, context: CallbackContext):
    current_step = context.user_data.get("step")
    if current_step == "starting":
        choice = update.message.text
        if choice == "️ℹ️️️️Информацияℹ️":
            update.message.reply_text(
                """Информация:
Этот бот предназначен для мониторинга цен на маркетплейсе. Все сведения программа получает из открытого доступа. 
Что умеет бот:
✅ Отслеживать цены на Wildberries
✅ Регулярно уведомлять об изменении цен

Как это работает:
1. Добавляете ссылку на товар
2. Бот регулярно проверяет цену
3. При изменении цены — уведомление в Telegram
4. Видите график изменения цен за неделю/месяц

⏰ Вы можете выбирать удобное вам время прихода уведомлений сами!

🔥 **У вас подключён пробный период на 14 дней!** 🔥
В него входит проверка одного товара 1 раз в день.

☎️ Поддержка: @PriceTrackerSupportBot

Важно! При оплате второго тарифа - первый аннулируется!""", reply_markup=starting_keyboard2())
            context.user_data["step"] = "starting2"

        elif choice == "🛠Тарифы🛠":
            update.message.reply_text("""Тарифы:
• 🚀Эконом: 10 товаров, до 2 уведомлений/день — 790₽/мес
• 💼Стандарт: 20 товаров, до 4 уведомлений/день — 1390₽/мес
• 🏆Про: 50 товаров, до 6 уведомлений/день — 1990₽/мес
""", reply_markup=tariff_keyboard())

            update.message.reply_text(
                "Если вы только хотите опробовать продукт - жмите \"🔥Пробный период🔥\", он у вас уже есть, если не прошло 14 дней с первого запуска.",
                reply_markup=ReplyKeyboardRemove())


    elif current_step == "starting2":
        choice = update.message.text
        if choice == "🛠Тарифы🛠":
            context.user_data["step"] = "wait"
            update.message.reply_text("""Тарифы:
• 🚀Эконом: 10 товаров, до 2 уведомлений/день — 790₽/мес
• 💼Стандарт: 20 товаров, до 4 уведомлений/день — 1390₽/мес
• 🏆Про: 50 товаров, до 6 уведомлений/день — 1990₽/мес
            """, reply_markup=tariff_keyboard())

            update.message.reply_text(
                "Если вы только хотите опробовать продукт - жмите \"🔥Пробный период🔥\", он у вас уже есть, если не прошло 14 дней с первого запуска.",
                reply_markup=ReplyKeyboardRemove())


    elif current_step == "start_work":
        # Проверяем, сколько часов уже собрали
        if "collected_hours" not in context.user_data:
            context.user_data["collected_hours"] = []

        # Получаем текущий ввод
        try:
            hour = int(update.message.text)
            if not (1 <= hour <= 24):
                raise ValueError
        except ValueError:
            update.message.reply_text("❌ Введите час от 1 до 24")
            return

        # Добавляем час в список
        context.user_data["collected_hours"].append(hour)

        result = supabase.table('users') \
            .select('tariff') \
            .eq('id', update.effective_user.id) \
            .execute()
        tariff = result.data[0].get("tariff")

        # Сколько часов нужно собрать
        hours_needed = {
            'trial': 1,
            'econom': 2,
            'standart': 4,
            'pro': 6
        }.get(tariff, 1)

        collected = len(context.user_data["collected_hours"])

        # Если ещё не все часы собраны
        if collected < hours_needed:
            remaining = hours_needed - collected
            update.message.reply_text(
                f"✅ Добавлен час: {hour-1}:00\n"
                f"Осталось выбрать {remaining} час(ов)\n"
                f"Введите следующий час (1-24):"
            )
            return  # Ждём следующее сообщение

        # Все часы собраны - сохраняем
        hours = sorted(context.user_data["collected_hours"])

        # Сохраняем в Supabase
        supabase.table('items').insert({
            'id': update.effective_user.id,
            'timeofdelivery': hours,  # Список часов [9, 14, 18]
        }).execute()

        # Форматируем ответ
        hours_str = ', '.join([f"{h-1}:00" for h in hours])
        update.message.reply_text(
            f"✅ Спасибо! Настройки сохранены!\n"
            f"📦 Уведомления будут приходить в: {hours_str} (в течении этого(их) часа(ов))"
        )

        # Очищаем временные данные и меняем step
        del context.user_data["collected_hours"]
        context.user_data["step"] = "main_menu"

        # Показываем главное меню

        update.message.reply_text(
            "Совет: Если бот перестал работать, пропишите /start. Ваши данные останутся.",
            reply_markup=usual_keyboard()
        )

        update.message.reply_text(
            "Главное меню:",
            reply_markup=usual_keyboard()
        )

    elif current_step == "confirm_reset":

        if update.message.text.lower() == "да":

            supabase.table('items').delete().eq('id', update.effective_user.id).execute()

            context.user_data["step"] = "starting"

            update.message.reply_text(

                "Возвращаю к началу...",

                reply_markup=starting_keyboard()

            )

        else:

            context.user_data["step"] = "main_menu"

            update.message.reply_text(

                "❌ Отменено. Возвращаю в главное меню.",

                reply_markup=usual_keyboard()

            )

    elif current_step == "main_menu":

        choice = update.message.text

        if choice == "⚙️Перенастроить⚙️":
            if not is_tariff_expired(update.effective_user.id):
                update.message.reply_text(
                    "Вы уверены? Напишите **Да**, если согласны.",

                    reply_markup=ReplyKeyboardRemove()

                )

                # Меняем шаг, чтобы бот ждал подтверждения

                context.user_data["step"] = "confirm_reset"
            elif is_tariff_expired(update.effective_user.id):
                supabase.table('items').delete().eq('id', update.effective_user.id).execute()
                update.message.reply_text(
                    "К сожалению, ваш тариф закончился.",
                    reply_markup=starting_keyboard()
                )
                context.user_data["step"] = "starting"

        elif choice == "🗑Удалить товар🗑":
            if not is_tariff_expired(update.effective_user.id):
                del_list(update,context)
            elif is_tariff_expired(update.effective_user.id):
                supabase.table('items').delete().eq('id', update.effective_user.id).execute()
                update.message.reply_text(
                    "К сожалению, ваш тариф закончился.",
                    reply_markup=starting_keyboard()
                )
                context.user_data["step"] = "starting"


        elif choice == "📋Список товаров📋":
            if not is_tariff_expired(update.effective_user.id):
                give_list(update)
            elif is_tariff_expired(update.effective_user.id):
                supabase.table('items').delete().eq('id', update.effective_user.id).execute()
                update.message.reply_text(
                    "К сожалению, ваш тариф закончился.",
                    reply_markup=starting_keyboard()
                )
                context.user_data["step"] = "starting"

        elif choice == "➕Добавить товар➕":
            if not is_tariff_expired(update.effective_user.id):
                if can_add_url(update):
                    update.message.reply_text(
                        "📎 Отправьте ссылку на товар с Wildberries:",
                    reply_markup=ReplyKeyboardRemove()
                    )
                    context.user_data["step"] = "waiting_for_url"
            elif is_tariff_expired(update.effective_user.id):
                supabase.table('items').delete().eq('id', update.effective_user.id).execute()
                update.message.reply_text(
                    "К сожалению, ваш тариф закончился.",
                    reply_markup=starting_keyboard()
                )
                context.user_data["step"] = "starting"

# Шаг 1: Ждём ссылку
    elif current_step == "waiting_for_url":
        url = update.message.text

        # Простая проверка, что это ссылка
        if not url.startswith(('https://www.wildberries.ru')):
            update.message.reply_text(
                "❌ Это не похоже на ссылку.\n"
                "Отправьте ссылку, начинающуюся с https://www.wildberries.ru"
            )
            return
        if is_not_unique("url", url,update):
            update.message.reply_text(
                "❌ Такая ссылка уже есть!\n"
                "Отправьте другую"
            )
            return

        # Сохраняем ссылку во временные данные
        context.user_data["pending_url"] = url

        # Просим название
        update.message.reply_text(
            "✏️ Теперь отправьте название для этого товара:\n"
            "(например: iPhone 15 Pro или Кактус в горшке)"
        )
        context.user_data["step"] = "waiting_for_name"

# Шаг 2: Ждём название и сохраняем
    elif current_step == "waiting_for_name":
        name = update.message.text
        if len(name)>100:
            update.message.reply_text(
                "❌ Слишком длинное имя!\n"
                "Отправьте более короткое имя."
            )
            return
        # Проверка того, что имя уникально
        if is_not_unique("name", name,update) :
            update.message.reply_text(
                "❌ Название товара не должно повторяться!\n"
                "Отправьте уникальное имя."
            )
            return

        url = context.user_data.get("pending_url")

        # ✅ Просто добавляем новую запись (не проверяя старые)
        supabase.table('url_list').insert({
            'id': update.effective_user.id,
            'url': url,
            'name': name,
        }).execute()

        result = supabase.table('url_list').select('id').eq('id', update.effective_user.id).execute()
        products_count = len(result.data)

        context.user_data["step"] = "main_menu"

        update.message.reply_text(
            f"✅ Товар добавлен!\n"
            f"📦 Всего товаров: {products_count}\n"
            f"📝 Название: {name}", reply_markup=usual_keyboard()
        )

    elif current_step == "waiting_for_del":
        name = update.message.text
        user_id = update.effective_user.id
        result = supabase.table('url_list') \
            .select('name, url') \
            .eq('id', user_id) \
            .execute()

        if not result.data:
            update.message.reply_text("📭 Товара с таким именем в списке нет!\n", reply_markup=usual_keyboard())
        else:
            supabase.table('url_list') \
                .delete() \
                .eq('id', user_id) \
                .eq('name', name) \
                .execute()
            update.message.reply_text("🗑Товар успешно удалён из списка!\n", reply_markup=usual_keyboard())

        context.user_data["step"] = "main_menu"

def main():
    global bot
    updater = Updater(Token, use_context=True, request_kwargs={
        'read_timeout': 60,      # Читать ответ
        'connect_timeout': 60,   # Подключиться
    })
    bot = updater.bot  # 👈 Сохраняем бота
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_choice))
    dp.add_handler(CallbackQueryHandler(button_handler))
    dp.add_handler(PreCheckoutQueryHandler(pre_checkout_handler))
    dp.add_handler(MessageHandler(Filters.successful_payment, successful_payment_handler))

    scheduler_thread = threading.Thread(target=run_scheduler, daemon=True)
    scheduler_thread.start()
    logger.info("Планировщик запущен!")  # Проверка, что запустился

    logger.info("🚀 Бот запущен!")
    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()