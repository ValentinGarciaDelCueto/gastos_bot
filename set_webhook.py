"""
Corré esto UNA VEZ (local o en la consola de PythonAnywhere) después de
desplegar webhook_app.py, para avisarle a Telegram a qué URL mandar los
mensajes. Necesita TELEGRAM_TOKEN y WEBHOOK_SECRET en el entorno (o .env).

Uso:
    python set_webhook.py https://tu-usuario.pythonanywhere.com
"""

import os
import sys

import requests

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


def main():
    if len(sys.argv) != 2:
        raise SystemExit("Uso: python set_webhook.py https://tu-usuario.pythonanywhere.com")

    base_url = sys.argv[1].rstrip("/")
    token = os.environ.get("TELEGRAM_TOKEN", "").strip()
    secret = os.environ.get("WEBHOOK_SECRET", "").strip()

    if not token or not secret:
        raise SystemExit("Faltan TELEGRAM_TOKEN y/o WEBHOOK_SECRET en el entorno.")

    webhook_url = f"{base_url}/webhook/{secret}"
    resp = requests.post(
        f"https://api.telegram.org/bot{token}/setWebhook",
        json={"url": webhook_url},
        timeout=10,
    )
    resp.raise_for_status()
    print(resp.json())
    print(f"\nWebhook seteado a: {webhook_url}")


if __name__ == "__main__":
    main()
