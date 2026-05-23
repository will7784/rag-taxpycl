"""
Taxpy Writer — Punto de entrada.
Solo arranca el bot de Telegram.
"""

from telegram_mvp_bot import WriterTelegramBot
import config

if __name__ == "__main__":
    if not config.TELEGRAM_BOT_TOKEN:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN no configurado. "
            "Revisa tu archivo .env o variables de entorno."
        )
    if not config.OPENAI_API_KEY:
        raise RuntimeError(
            "OPENAI_API_KEY no configurado. "
            "Se requiere para redactar contenido con GPT-4o."
        )
    bot = WriterTelegramBot(config.TELEGRAM_BOT_TOKEN)
    bot.run()
