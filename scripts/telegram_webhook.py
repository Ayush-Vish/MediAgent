"""Register your own Telegram bot after deployment; explicitly run by owner."""
import httpx

from app.config import Settings


def main():
    settings = Settings()
    if not settings.telegram_bot_token or not settings.public_url.startswith('https://'):
        raise SystemExit('Set TELEGRAM_BOT_TOKEN, webhook/hash secrets and HTTPS PUBLIC_URL first.')
    result = httpx.post(f'https://api.telegram.org/bot{settings.telegram_bot_token}/setWebhook',
        json={'url': settings.public_url.rstrip('/') + '/webhooks/telegram',
              'secret_token': settings.telegram_webhook_secret, 'allowed_updates': ['message'],
              'max_connections': 1}, timeout=20)
    if result.status_code != 200 or not result.json().get('ok'):
        raise SystemExit('Webhook registration failed. Check the token and public HTTPS URL.')
    print('Telegram webhook registered. Only private text chats are handled.')


if __name__ == '__main__':
    main()
