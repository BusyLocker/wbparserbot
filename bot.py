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
wbauid: str = os.environ.get("wbauid")

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
    #it's a scheduler, which staring every hour to parse the products and notify users
    while True:
        current_hour = datetime.now(pytz.timezone('Europe/Moscow')).hour
        target_hour = current_hour + 1  # because hours are from 1 to 24

        logger.info(f"[{datetime.now()}] Проверка для часа: {target_hour}")

        # recieve data of all users
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

        # Start checking
        for user in users_to_check:
            thread = threading.Thread(target=check_and_notify, args=(user['id'],))
            thread.start()

        # waiting one hour
        sleep(3600)


def check_and_notify(user_id):
    #checking prices and, after all, notifying all users in telegram.
    # checking tariff
    if is_tariff_expired(user_id):
        return
    urls = []
    names = []
    # receiving users urls from db
    result = supabase.table('url_list') \
        .select('name, url') \
        .eq('id', user_id) \
        .execute()

    if not result.data:
        return

    for item in result.data:
        urls.append(item["url"])
        names.append(item["name"])

    # parsing
    final_dict = get_prices(urls,names)

    #preparing a message
    message00 = "📋 Результаты проверки цен:\n" + "=" * 30 + "\n"
    for i, (name, priceorsize) in enumerate(final_dict.items(), 1):
        message00 += f"{i}. 📦 {name}\n"
        if not isinstance(priceorsize, tuple):
            message00 += f"   💰 {priceorsize}₽\n"
        else:
            message00 += f"   💰 {priceorsize[0]}₽\n"
            message00 += f" Размерные линейки {priceorsize[1]}\n"
        message00 += "-" * 30 + "\n"

    # sending notification
    try:
        bot.send_message(chat_id=user_id, text=message00)
        logger.info(f"Уведомление отправлено пользователю {user_id}")
    except Exception as e:
        logger.error(f"Ошибка отправки {user_id}: {e}")


def del_list(update: Update,context: CallbackContext):
    #there we are checking products>0 and moving a step
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
    #func for output the list of urls
    user_id = update.effective_user.id
    result = supabase.table('url_list') \
        .select('name, url') \
        .eq('id', user_id) \
        .execute()

    # preparing message
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
    #if we want to users don't be confused by same names of urls when they about to delete one of them, we are checking that by this func
    result = supabase.table("url_list") \
        .select(column) \
        .eq('id', update.effective_user.id) \
        .eq(column, value) \
        .execute()
    return len(result.data) > 0

def can_add_url(update: Update):
    #check can users add urls or that forbidden by limit of they tariff, or they just don't own one
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
    #checking existing of user
    try:
        result = supabase.table('users').select('id').eq('id', idi).execute()
        return len(result.data) > 0
    except:
        return False

def user_iscool(idi):
    #checking: we should put user in main menu or to the start
    try:
        result = supabase.table('items').select('timeofdelivery').eq('id', idi).execute()
        return len(result.data) > 0
    except:
        return False


def is_tariff_expired(idi):
    #check exp. date of users tariff
    result = supabase.table('users') \
        .select('ended_at') \
        .eq('id', idi) \
        .execute()

    if not result.data or not result.data[0].get('ended_at'):
        return False

    ended_at = result.data[0]['ended_at']
    ended_date = datetime.fromisoformat(ended_at).date()

    # time in Moscow
    now_date = datetime.now(pytz.timezone('Europe/Moscow')).date()

    # if user inactive for 30 days, we delete all of his urls from db
    check_date = ended_date + timedelta(days=30)
    if now_date > check_date:
        supabase.table('url_list').delete().eq('user_id', idi).execute()

    return datetime.now(pytz.timezone('Europe/Moscow')).date() > ended_date

def test_connection():
    #testing connection to the supabase
    try:
        result = supabase.table('users').select('*').limit(1).execute()
        logger.info("✅ Supabase подключён!")
        return True
    except Exception as e:
        logger.critical(f"❌ Ошибка: {e}")
        return False

test_connection()

#below - keyboard block

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
        [InlineKeyboardButton("У меня уже есть тариф или пробный.", callback_data="trial")],
    ]
    return InlineKeyboardMarkup(keyboard)

#above - keyboard block


def update_token(update: Update, context: CallbackContext):
    #we should update cookies every ~12 hours
    user_id = update.effective_user.id
    if user_id != YOUR_ADMIN_ID:
        return

    if not context.args:
        update.message.reply_text("Использование: /update_token <токен>")
        return

    token = context.args[0]
    sb = create_client(url, key)
    sb.table("wb_cookies").update({
        "cookies": {"x_wbaas_token": token, "_wbauid": wbauid},
        "updated_at": datetime.utcnow().isoformat(),
    }).eq("id", 1).execute()

    invalidate_cookies_cache()
    update.message.reply_text("✅ Токен обновлён!")

def start(update: Update, context: CallbackContext):
    #doing after /start
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
                "🎯 Бот для ценового мониторинга Wildberries\n"
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

        #giving trial for user immediately
        supabase.table('users').insert({
            'id': update.effective_user.id,
            'tariff': 'trial',
            'ended_at': (datetime.now(pytz.timezone('Europe/Moscow')).date() + timedelta(days=14)).strftime("%Y-%m-%d")
        }).execute()


        context.user_data["step"] = "starting"

def button_handler(update: Update, context: CallbackContext):
    #handle button press
    query = update.callback_query
    query.answer()

    query.edit_message_reply_markup(reply_markup=None)

    tariff = query.data

    if tariff == "trial":

        if not is_tariff_expired(update.effective_user.id):
            query.message.reply_text(
                "Перейдём к началу работы. Отправьте в какой час по Московскому часовому поясу вам будет удобно получать уведомления (от 1 до 24). Если вам достаточно, отправьте слово\"Всё\"",reply_markup=ReplyKeyboardRemove()
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
    #prepairing a payment

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
    #after payment

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
        "Перейдём к началу работы. Отправьте в какой час по Московскому часовому поясу вам будет удобно получать уведомления (от 1 до 24). Если вам достаточно, отправьте слово\"Всё\"",reply_markup=ReplyKeyboardRemove()
    )
    context.user_data["step"] = "start_work"


def handle_choice(update: Update, context: CallbackContext):
    #bot structure

    current_step = context.user_data.get("step")

    #by moving on steps we can show what the task for bot at the moment

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
        # checking how many hours we got
        if "collected_hours" not in context.user_data:
            context.user_data["collected_hours"] = []

        # receiving user input
        try:
            hour = update.message.text

            if hour.lower() == "всё":
                hours = sorted(context.user_data["collected_hours"])
                # saving to supabase
                supabase.table('items').insert({
                    'id': update.effective_user.id,
                    'timeofdelivery': hours,
                }).execute()

                # preparing a message
                hours_str = ', '.join([f"{h - 1}:00" for h in hours])
                update.message.reply_text(
                    f"✅ Спасибо! Настройки сохранены!\n"
                    f"📦 Уведомления будут приходить в: {hours_str} (в течении этого(их) часа(ов))"
                )

                # deleting cached hours
                try:
                    del context.user_data["collected_hours"]
                    context.user_data["step"] = "main_menu"
                except:
                    #I know, it's bad, but there are only ONE the most possibly error
                    pass

                # showing main menu

                update.message.reply_text(
                    "Совет: Если бот перестал работать, пропишите /start. Ваши данные останутся.",
                    reply_markup=usual_keyboard()
                )

                update.message.reply_text(
                    "Главное меню:",
                    reply_markup=usual_keyboard()
                )


            if not (1 <= int(hour) <= 24):
                raise ValueError
        except ValueError:
            update.message.reply_text("❌ Введите час от 1 до 24 или слово \"Всё\"")
            return


        # adding hour in list
        context.user_data["collected_hours"].append(hour)

        result = supabase.table('users') \
            .select('tariff') \
            .eq('id', update.effective_user.id) \
            .execute()
        tariff = result.data[0].get("tariff")

        # how many hours we need, based on users tariff
        hours_needed = {
            'trial': 1,
            'econom': 2,
            'standart': 4,
            'pro': 6
        }.get(tariff, 1)

        collected = len(context.user_data["collected_hours"])

        # if we didn't receive needed amount of hours
        if collected < hours_needed:
            remaining = hours_needed - collected
            update.message.reply_text(
                f"✅ Добавлен час: {hour-1}:00\n"
                f"Осталось выбрать {remaining} час(ов)\n"
                f"Введите следующий час (1-24):"
            )
            return  # waiting next message

        # all descriptions of these acts you can watch in above block
        hours = sorted(context.user_data["collected_hours"])

        # Сохраняем в Supabase
        supabase.table('items').insert({
            'id': update.effective_user.id,
            'timeofdelivery': hours,
        }).execute()

        # Форматируем ответ
        hours_str = ', '.join([f"{h-1}:00" for h in hours])
        update.message.reply_text(
            f"✅ Спасибо! Настройки сохранены!\n"
            f"📦 Уведомления будут приходить в: {hours_str} (в течении этого(их) часа(ов))"
        )

        del context.user_data["collected_hours"]
        context.user_data["step"] = "main_menu"

        update.message.reply_text(
            "Совет: Если бот перестал работать, пропишите /start. Ваши данные останутся.",
            reply_markup=usual_keyboard()
        )

        update.message.reply_text(
            "Главное меню:",
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

                # switching step, bot is awaiting confirmation

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

    elif current_step == "confirm_reset":
        #awaiting reset confirmation

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


    elif current_step == "waiting_for_url":
        #when user wants to add url, bot will wait for that
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

        # caching
        context.user_data["pending_url"] = url

        # moving to waiting for name
        update.message.reply_text(
            "✏️ Теперь отправьте название для этого товара:\n"
            "(например: iPhone 15 Pro или Кактус в горшке)"
        )
        context.user_data["step"] = "waiting_for_name"

    elif current_step == "waiting_for_name":
        #waiting for name, actually
        name = update.message.text
        if len(name)>100:
            update.message.reply_text(
                "❌ Слишком длинное имя!\n"
                "Отправьте более короткое имя."
            )
            return
        # checking name for valid
        if is_not_unique("name", name,update) :
            update.message.reply_text(
                "❌ Название товара не должно повторяться!\n"
                "Отправьте уникальное имя."
            )
            return

        url = context.user_data.get("pending_url")

        # just adding the url
        supabase.table('url_list').insert({
            'id': update.effective_user.id,
            'url': url,
            'name': name,
        }).execute()

        result = supabase.table('url_list').select('id').eq('id', update.effective_user.id).execute()
        products_count = len(result.data)

        context.user_data["step"] = "main_menu"

        #reply

        update.message.reply_text(
            f"✅ Товар добавлен!\n"
            f"📦 Всего товаров: {products_count}\n"
            f"📝 Название: {name}", reply_markup=usual_keyboard()
        )

    elif current_step == "waiting_for_del":
        #when user wants to delete one of urls
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
    #launching the bot
    global bot
    updater = Updater(Token, use_context=True, request_kwargs={
        'read_timeout': 60,      # reading replies
        'connect_timeout': 60,   # connect
    })
    bot = updater.bot  # saving
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_choice))
    dp.add_handler(CallbackQueryHandler(button_handler))
    dp.add_handler(PreCheckoutQueryHandler(pre_checkout_handler))
    dp.add_handler(MessageHandler(Filters.successful_payment, successful_payment_handler))

    #making a thread for scheduler

    scheduler_thread = threading.Thread(target=run_scheduler, daemon=True)
    scheduler_thread.start()
    logger.info("Планировщик запущен!")  # checking launching of scheduler

    logger.info("🚀 Бот запущен!") #cheacking launching of bot
    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()