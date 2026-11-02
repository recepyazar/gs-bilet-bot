"""Çalıştırma:  python -m gsbot          → botu başlatır
               python -m gsbot anahtar  → GS_SECRET_KEY için yeni anahtar üretir
"""

import logging
import sys


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "anahtar":
        from cryptography.fernet import Fernet
        print(Fernet.generate_key().decode())
        print("\n↑ Bunu .env içinde GS_SECRET_KEY olarak sakla. Kaybedersen kayıtlı bilgiler çözülemez.",
              file=sys.stderr)
        return

    logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
    # httpx her isteğin URL'sini (bot token'ı dahil) INFO'da basar — sustur
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)

    from .bot import build_app
    from .config import Config

    app = build_app(Config.from_env())
    app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
