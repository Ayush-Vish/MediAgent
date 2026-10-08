"""Create local configuration once, without overwriting existing settings."""
import secrets

from app.config import ROOT


def main():
    target = ROOT / '.env'
    if target.exists():
        print('Existing .env preserved. Staff access uses its ADMIN_TOKEN value.')
        return
    content = (ROOT / '.env.example').read_text(encoding='utf-8')
    content = content.replace('ADMIN_TOKEN=\n', 'ADMIN_TOKEN=' + secrets.token_urlsafe(32) + '\n', 1)
    with target.open('x', encoding='utf-8') as config:
        config.write(content)
    print('Created local .env with a random staff token. Open .env to copy ADMIN_TOKEN for /staff.')
    print('Start: .venv\\Scripts\\python.exe -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000 --workers 1 --no-access-log')


if __name__ == '__main__':
    main()
