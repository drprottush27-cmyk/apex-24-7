import os
import sys

# Ensure src is in path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.telegram.notifier import TelegramNotifier

def main():
    env_path = '/srv/apex/.env'

    print("\n" + "="*60)
    print(" AEGIS ALPHA: TELEGRAM SETUP & LIVE TEST")
    print("="*60)

    print("\nPlease enter the credentials you just generated.")
    token = input("1. Enter your Telegram Bot Token: ").strip()
    chat_id = input("2. Enter your Telegram Chat ID: ").strip()

    if not token or not chat_id:
        print("[-] Error: Token and Chat ID are required.")
        sys.exit(1)

    # Read existing .env to preserve Notion keys
    lines = []
    if os.path.exists(env_path):
        with open(env_path, 'r') as f:
            lines = f.readlines()

    # Update or append Telegram keys safely
    telegram_token_found = False
    telegram_chat_found = False

    with open(env_path, 'w') as f:
        for line in lines:
            if line.startswith('AEGIS_TELEGRAM_TOKEN='):
                f.write(f'AEGIS_TELEGRAM_TOKEN="{token}"\n')
                telegram_token_found = True
            elif line.startswith('AEGIS_TELEGRAM_CHAT_ID='):
                f.write(f'AEGIS_TELEGRAM_CHAT_ID="{chat_id}"\n')
                telegram_chat_found = True
            else:
                f.write(line)
                
        if not telegram_token_found:
            f.write(f'AEGIS_TELEGRAM_TOKEN="{token}"\n')
        if not telegram_chat_found:
            f.write(f'AEGIS_TELEGRAM_CHAT_ID="{chat_id}"\n')

    print("\n[*] .env file successfully updated.")
    print("[*] Testing live Telegram connection...")

    notifier = TelegramNotifier(token, chat_id)
    msg = (
        "🟢 <b>AEGIS ALPHA ALERT PIPELINE ONLINE</b>\n\n"
        "Your VPS has successfully connected to this chat.\n"
        "You will now receive high-quality signal alerts here."
    )

    if notifier.send_alert(msg):
        print("\n[+] SUCCESS! Check your phone. You should have just received a message.")
        print("[*] Restarting the background daemon to apply the new keys...")
        os.system("systemctl restart aegis.service")
        print("[+] Daemon restarted successfully.")
    else:
        print("\n[-] FAILED to send message. Please verify your Token and Chat ID.")
        print("    Did you remember to send a message to the bot first to initialize the chat?")

    print("="*60 + "\n")

if __name__ == "__main__":
    main()

