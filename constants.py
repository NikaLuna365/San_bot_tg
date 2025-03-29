# constants.py
from enum import Enum, auto
from telegram import ReplyKeyboardMarkup

class State(Enum):
    # Test States
    TEST_FIXED_1 = auto()
    TEST_FIXED_2 = auto()
    TEST_FIXED_3 = auto()
    TEST_FIXED_4 = auto()
    TEST_FIXED_5 = auto()
    TEST_FIXED_6 = auto()
    TEST_OPEN_1 = auto()
    TEST_OPEN_2 = auto()
    AFTER_TEST_CHOICE = auto() # Возможно, не используется? Проверить.
    GEMINI_CHAT_TEST = auto() # Переименовать от GEMINI_CHAT для ясности

    # Retrospective States
    RETRO_CHOICE = auto()
    RETRO_PERIOD_CHOICE = auto()
    RETRO_OPEN_1 = auto()
    RETRO_OPEN_2 = auto()
    RETRO_OPEN_3 = auto()
    RETRO_OPEN_4 = auto()
    GEMINI_CHAT_RETRO = auto() # Переименовать от RETRO_CHAT для ясности

    # Reminder States
    REMINDER_CHOICE = auto()
    REMINDER_DAILY_TIME = auto()
    REMINDER_DAILY_REMIND = auto()

    # Schedule Retrospective States
    RETRO_SCHEDULE_DAY_NEW = auto()
    RETRO_SCHEDULE_CURRENT = auto()
    RETRO_SCHEDULE_TARGET = auto()
    RETRO_SCHEDULE_MODE = auto()

    # Timezone State (понадобится для пункта 2)
    SET_TIMEZONE = auto()

# Тексты вопросов (можно оставить здесь или вынести в отдельный файл/словарь)
WEEKDAY_FIXED_QUESTIONS = { ... } # Ваш словарь
OPEN_QUESTIONS = [ ... ] # Ваш список
RETRO_OPEN_QUESTIONS = [ ... ] # Ваш список

# Клавиатуры (можно сделать функции для их генерации)
def build_fixed_keyboard() -> ReplyKeyboardMarkup:
    keyboard = [[str(i) for i in range(1, 8)], ["Главное меню"]]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)

MAIN_MENU_KEYBOARD = ReplyKeyboardMarkup(
    [["Тест", "Ретроспектива"], ["Напоминание", "Помощь"], ["Настроить часовой пояс"]], # Добавим кнопку для TЗ
    resize_keyboard=True,
    one_time_keyboard=False # Главное меню лучше делать не одноразовым
)

CANCEL_KEYBOARD = ReplyKeyboardMarkup([["Главное меню"]], resize_keyboard=True, one_time_keyboard=True)

# ... другие общие клавиатуры ...
