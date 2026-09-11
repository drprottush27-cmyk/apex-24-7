import json
import urllib.request
import urllib.parse
import logging

class TelegramNotifier:
    """Outbound alerting system using the official Telegram Bot API."""
    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.base_url = f"https://api.telegram.org/bot{self.bot_token}"

    def send_alert(self, message: str) -> bool:
        # Fail-closed / Mock mode if credentials are not configured
        if not self.bot_token or not self.chat_id or self.bot_token == "DUMMY_TOKEN":
            logging.info(f"[Mock Telegram Alert] {message.replace(chr(10), ' | ')}")
            return True

        url = f"{self.base_url}/sendMessage"
        data = urllib.parse.urlencode({
            'chat_id': self.chat_id, 
            'text': message,
            'parse_mode': 'HTML'
        }).encode('utf-8')
        
        try:
            req = urllib.request.Request(url, data=data)
            with urllib.request.urlopen(req, timeout=5) as response:
                return response.getcode() == 200
        except Exception as e:
            logging.error(f"Telegram API Error: {str(e)}")
            return False
