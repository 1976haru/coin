# utils/notifier.py
import telegram
import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

try:
    bot = telegram.Bot(token=TELEGRAM_TOKEN)
except Exception as e:
    print(f"텔레그램 봇 연결 실패: {e}")
    bot = None

def send_message(message):
    """텔레그램으로 메시지를 전송합니다."""
    if bot:
        try:
            bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message)
            print(f"텔레그램 메시지 전송: {message}")
        except Exception as e:
            print(f"텔레그램 메시지 전송 실패: {e}")