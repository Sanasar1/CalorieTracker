import os
import logging
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Updater,
    CommandHandler,
    MessageHandler,
    Filters,
    CallbackContext,
    ConversationHandler,
)
import requests

# Загрузка переменных окружения
load_dotenv()

# Настройка логгирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)

# Состояния для ConversationHandler
WEIGHT, HEIGHT, AGE, ACTIVITY, CITY, CALORIE_GOAL = range(6)

# Временное хранилище данных пользователей
users = {}

# Константы для расчета норм
WATER_BASE_ML_PER_KG = 30
WATER_ACTIVITY_ML_PER_30MIN = 500
WATER_BASE_ML = 500
WATER_ABOVE_WEATHER_LIMIT = 1000 
CALORIES_ACTIVITY_FACTOR = 1.5  # Множитель для уровня активности

# Типы тренировок и их MET (Metabolic Equivalent of Task)
WORKOUT_MET = {
    'бег': 8,
    'ходьба': 3,
    'плавание': 6,
    'велосипед': 7,
    'тренажерный зал': 5,
}

# Обработчики команд
def start(update: Update, context: CallbackContext) -> None:
    update.message.reply_text(
        "Привет! Я бот для отслеживания воды, калорий и тренировок.\n"
        "Начни с команды /set_profile чтобы настроить профиль."
    )

def set_profile(update: Update, context: CallbackContext) -> int:
    update.message.reply_text("Введите ваш вес (в кг):")
    return WEIGHT

def weight_handler(update: Update, context: CallbackContext) -> int:
    user_id = update.message.from_user.id
    users[user_id] = {'weight': float(update.message.text)}
    update.message.reply_text("Введите ваш рост (в см):")
    return HEIGHT

def height_handler(update: Update, context: CallbackContext) -> int:
    user_id = update.message.from_user.id
    users[user_id]['height'] = float(update.message.text)
    update.message.reply_text("Введите ваш возраст:")
    return AGE

def age_handler(update: Update, context: CallbackContext) -> int:
    user_id = update.message.from_user.id
    users[user_id]['age'] = int(update.message.text)
    update.message.reply_text("Сколько минут активности в день?")
    return ACTIVITY

def activity_handler(update: Update, context: CallbackContext) -> int:
    user_id = update.message.from_user.id
    users[user_id]['activity'] = int(update.message.text)
    update.message.reply_text("В каком городе вы находитесь?")
    return CITY

def get_weather(city: str) -> float:
    api_key = os.getenv('OPENWEATHERMAP_API_KEY')
    url = f"http://api.openweathermap.org/data/2.5/weather?q={city}&appid={api_key}&units=metric"
    response = requests.get(url)
    if response.status_code == 200:
        return response.json()['main']['temp']
    else:
        return 20  # Дефолтная температура при ошибке

def city_handler(update: Update, context: CallbackContext) -> int:
    user_id = update.message.from_user.id
    city = update.message.text
    users[user_id]['city'] = city
    temp = get_weather(city)
    
    # Расчет нормы воды
    weight = users[user_id]['weight']
    water_goal = weight * WATER_BASE_ML_PER_KG
    water_goal += (users[user_id]['activity'] / 30) * WATER_ACTIVITY_ML_PER_30MIN
    water_goal += WATER_BASE_ML
    if temp > 25:
        water_goal -= WATER_ABOVE_WEATHER_LIMIT
    
    # Расчет нормы калорий
    calorie_goal = 10 * weight + 6.25 * users[user_id]['height'] - 5 * users[user_id]['age'] + 5
    calorie_goal *= CALORIES_ACTIVITY_FACTOR
    
    users[user_id]['water_goal'] = round(water_goal)
    users[user_id]['calorie_goal'] = round(calorie_goal)
    users[user_id].update({
        'logged_water': 0,
        'logged_calories': 0,
        'burned_calories': 0,
    })
    
    update.message.reply_text(
        f"Профиль сохранён!\n"
        f"Норма воды: {round(water_goal)} мл/день\n"
        f"Норма калорий: {round(calorie_goal)} ккал/день\n"
        "Можете изменить цель по калориям сейчас или пропустить (/skip):"
    )
    return CALORIE_GOAL

def skip_calorie_goal(update: Update, context: CallbackContext) -> int:
    return ConversationHandler.END

def calorie_goal_handler(update: Update, context: CallbackContext) -> int:
    user_id = update.message.from_user.id
    users[user_id]['calorie_goal'] = int(update.message.text)
    update.message.reply_text("Цель по калориям обновлена!")
    return ConversationHandler.END

def log_water(update: Update, context: CallbackContext) -> None:
    user_id = update.message.from_user.id
    if user_id not in users:
        update.message.reply_text("Сначала настройте профиль /set_profile")
        return
    
    try:
        amount = float(context.args[0])
        users[user_id]['logged_water'] += amount
        remaining = users[user_id]['water_goal'] - users[user_id]['logged_water']
        update.message.reply_text(f"✅ Добавлено {amount} мл воды. Осталось: {max(remaining, 0)} мл")
    except (IndexError, ValueError):
        update.message.reply_text("Используйте: /log_water <количество_мл>")

def get_food_calories(query: str) -> float:
    url = f"https://world.openfoodfacts.org/cgi/search.pl?search_terms={query}&search_simple=1&json=1"
    response = requests.get(url).json()
    if response['products']:
        return response['products'][0].get('nutriments', {}).get('energy-kcal_100g', 0)
    return 0

def log_food(update: Update, context: CallbackContext) -> None:
    user_id = update.message.from_user.id
    if user_id not in users:
        update.message.reply_text("Сначала настройте профиль /set_profile")
        return
    
    try:
        product = ' '.join(context.args)
        kcal_per_100g = get_food_calories(product)
        if kcal_per_100g <= 0:
            update.message.reply_text("❌ Продукт не найден")
            return
        
        context.user_data['current_product'] = {'name': product, 'kcal': kcal_per_100g}
        update.message.reply_text(f"{product} — {kcal_per_100g} ккал/100г. Сколько грамм вы съели?")
    except:
        update.message.reply_text("Используйте: /log_food <название_продукта>")

def handle_grams(update: Update, context: CallbackContext) -> None:
    user_id = update.message.from_user.id
    try:
        grams = float(update.message.text)
        product = context.user_data['current_product']
        calories = (product['kcal'] * grams) / 100
        users[user_id]['logged_calories'] += calories
        update.message.reply_text(f"✅ Записано: {round(calories, 1)} ккал")
        del context.user_data['current_product']
    except:
        update.message.reply_text("Ошибка. Попробуйте снова.")

def log_workout(update: Update, context: CallbackContext) -> None:
    user_id = update.message.from_user.id
    if user_id not in users:
        update.message.reply_text("Сначала настройте профиль /set_profile")
        return
    
    try:
        workout_type = context.args[0].lower()
        duration = int(context.args[1])
        met = WORKOUT_MET.get(workout_type, 3)
        calories_burned = (met * users[user_id]['weight'] * duration) / 60
        water_ml = (duration // 30) * 200
        
        users[user_id]['burned_calories'] += calories_burned
        users[user_id]['water_goal'] += water_ml
        
        update.message.reply_text(
            f"🏋️ {workout_type.capitalize()} {duration} мин — {round(calories_burned)} ккал сожжено.\n"
            f"Дополнительно выпейте {water_ml} мл воды."
        )
    except:
        update.message.reply_text("Используйте: /log_workout <тип_тренировки> <минуты>")

def check_progress(update: Update, context: CallbackContext) -> None:
    user_id = update.message.from_user.id
    if user_id not in users:
        update.message.reply_text("Сначала настройте профиль /set_profile")
        return
    
    user = users[user_id]
    water_remaining = user['water_goal'] - user['logged_water']
    calories_remaining = user['calorie_goal'] - user['logged_calories'] + user['burned_calories']
    
    message = (
        "📊 Прогресс:\n"
        f"Вода:\n- Выпито: {user['logged_water']} мл из {user['water_goal']} мл\n"
        f"- Осталось: {max(water_remaining, 0)} мл\n\n"
        f"Калории:\n- Потреблено: {round(user['logged_calories'])} ккал из {user['calorie_goal']} ккал\n"
        f"- Сожжено: {round(user['burned_calories'])} ккал\n"
        f"- Баланс: {round(calories_remaining)} ккал"
    )
    update.message.reply_text(message)

def main() -> None:
    updater = Updater(os.getenv('TELEGRAM_BOT_TOKEN'))
    dispatcher = updater.dispatcher

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('set_profile', set_profile)],
        states={
            WEIGHT: [MessageHandler(Filters.text & ~Filters.command, weight_handler)],
            HEIGHT: [MessageHandler(Filters.text & ~Filters.command, height_handler)],
            AGE: [MessageHandler(Filters.text & ~Filters.command, age_handler)],
            ACTIVITY: [MessageHandler(Filters.text & ~Filters.command, activity_handler)],
            CITY: [MessageHandler(Filters.text & ~Filters.command, city_handler)],
            CALORIE_GOAL: [
                MessageHandler(Filters.text & ~Filters.command, calorie_goal_handler),
                CommandHandler('skip', skip_calorie_goal),
            ],
        },
        fallbacks=[],
    )

    dispatcher.add_handler(CommandHandler('start', start))
    dispatcher.add_handler(conv_handler)
    dispatcher.add_handler(CommandHandler('log_water', log_water))
    dispatcher.add_handler(CommandHandler('log_food', log_food))
    dispatcher.add_handler(CommandHandler('log_workout', log_workout))
    dispatcher.add_handler(CommandHandler('check_progress', check_progress))
    dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_grams))

    updater.start_polling()
    updater.idle()

if __name__ == '__main__':
    main()