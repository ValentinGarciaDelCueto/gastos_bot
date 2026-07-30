"""
Versión gratuita del bot: Flask + webhook, para correr en el plan free
de PythonAnywhere (no tiene "always-on task", pero sí web apps gratis).

A diferencia de main.py (que hace polling con python-telegram-bot),
acá Telegram nos manda cada mensaje por POST a /webhook/<WEBHOOK_SECRET>.
No usamos el Application async de python-telegram-bot porque WSGI (como
corre PythonAnywhere) no mantiene un event loop vivo entre requests.

Todo el tráfico va en una sola dirección: Telegram nos habla y nosotros
contestamos en el cuerpo de esa misma respuesta (ver _responder). El bot
nunca abre una conexión hacia afuera, que es lo que el proxy del plan
free bloquea.

Deploy (ver README, sección "Versión gratis"):
  1. Subís este proyecto a PythonAnywhere, armás una Web app (Flask)
     apuntando a este archivo.
  2. Configurás las variables de entorno (mismas que la versión Railway,
     más WEBHOOK_SECRET).
  3. Corrés una vez set_webhook.py para avisarle a Telegram la URL.
"""

import os
from pathlib import Path

# El server WSGI arranca desde /var/www (no desde esta carpeta), así que el
# .env y el creds.json hay que ubicarlos a mano: si no, load_dotenv() no
# encuentra nada y el bot arranca sin token ni WEBHOOK_SECRET.
_BASE_DIR = Path(__file__).resolve().parent

try:
    from dotenv import load_dotenv

    load_dotenv(_BASE_DIR / ".env")
except ImportError:
    pass

# Mismo motivo: GOOGLE_CREDENTIALS_FILE=creds.json es relativo al proyecto,
# no al directorio desde el que arrancó el server.
_creds_file = os.environ.get("GOOGLE_CREDENTIALS_FILE", "").strip()
if _creds_file and not os.path.isabs(_creds_file):
    os.environ["GOOGLE_CREDENTIALS_FILE"] = str(_BASE_DIR / _creds_file)

from flask import Flask, abort, jsonify, request

from datetime import datetime

from main import (
    CREDITO_AYUDA,
    TZ,
    build_confirmation,
    build_credito_confirmation,
    day_total,
    delete_last_expense,
    format_money,
    get_worksheet,
    load_config,
    month_total,
    parse_amount,
    parse_credito,
    parse_message,
    record_credito,
    record_expenses,
)

app = Flask(__name__)
_config = None


def _get_config():
    global _config
    if _config is None:
        _config = load_config()
    return _config


def _responder(chat_id: int, text: str):
    """
    Contesta al usuario devolviendo la acción en el cuerpo de la respuesta,
    en vez de hacer un POST a api.telegram.org. Telegram acepta que el
    webhook conteste así ("hacé este método con estos parámetros"), y para
    nosotros es la diferencia entre andar y no andar: el proxy del plan free
    de PythonAnywhere no deja salir hacia api.telegram.org (503), pero esto
    viaja por la conexión que Telegram ya abrió hacia nosotros.

    Contra: solo se puede pedir UNA acción por update — nos alcanza, porque
    respondemos un único mensaje por gasto.
    """
    return jsonify({"method": "sendMessage", "chat_id": chat_id, "text": text})


def _autorizado(config, user_id) -> bool:
    if not config.allowed_user_id:
        return True
    return user_id == config.allowed_user_id


def _responder_total(ws, fecha_o_mes, es_mes: bool) -> str:
    try:
        total = month_total(ws, fecha_o_mes) if es_mes else day_total(ws, fecha_o_mes)
    except Exception:
        app.logger.exception("Error leyendo la planilla")
        return "⚠️ No pude leer la planilla. Probá de nuevo en un ratito."
    etiqueta = "🗓️ Total del mes" if es_mes else "📅 Total de hoy"
    return f"{etiqueta} ({fecha_o_mes}): {format_money(total)}"


def _handle_command(ws, texto: str) -> str:
    """
    `texto` es el mensaje completo, no solo el comando: /credito necesita
    los argumentos que vienen atrás.
    """
    comando = texto.split()[0].lower()

    if comando == "/start":
        return (
            "¡Hola! Soy tu bot de gastos. 💸\n\n"
            "Mandame tus gastos así:\n"
            "  combi 10000\n"
            "  snacks 3000\n\n"
            "Podés mandar varios juntos, uno por línea o separados por coma.\n"
            "Si comprás en cuotas:\n"
            "  /credito 32400 coderhouse curso 6 meses\n\n"
            "Comandos: /hoy (total del día) · /mes (total del mes) · "
            "/borraranterior (borra el último gasto cargado)."
        )
    now = datetime.now(TZ)
    if comando == "/hoy":
        return _responder_total(ws, now.strftime("%d/%m/%Y"), es_mes=False)
    if comando == "/mes":
        return _responder_total(ws, now.strftime("%m/%Y"), es_mes=True)
    if comando == "/borraranterior":
        try:
            fila = delete_last_expense(ws)
        except Exception:
            app.logger.exception("Error borrando en la planilla")
            return "⚠️ No pude borrar en la planilla. Probá de nuevo en un ratito."
        if fila is None:
            return "No hay nada para borrar. 🤷"
        _, _, desc, monto = fila
        return f"🗑️ Borrado: {desc} {format_money(parse_amount(monto) or 0)}"
    if comando == "/credito":
        parsed = parse_credito(texto)
        if not parsed:
            return CREDITO_AYUDA
        descripcion, monto, cuotas = parsed
        try:
            filas, total_dia = record_credito(ws, descripcion, monto, cuotas, now)
        except Exception:
            app.logger.exception("Error guardando las cuotas en la planilla")
            return "⚠️ No pude guardar las cuotas. Probá de nuevo en un ratito."
        return build_credito_confirmation(descripcion, monto, filas, total_dia)
    return None


def _handle_gasto(ws, texto: str) -> str:
    items = parse_message(texto)
    if not items:
        return "No pude leer ningún gasto. 🤔\nProbá con algo como: combi 10000"
    try:
        now = datetime.now(TZ)
        _, total_dia = record_expenses(ws, items, now)
    except Exception:
        app.logger.exception("Error guardando en la planilla")
        return "⚠️ No pude guardar en la planilla. Probá de nuevo en un ratito."
    return build_confirmation(items, total_dia)


@app.route("/webhook/<secret>", methods=["POST"])
def webhook(secret):
    expected = os.environ.get("WEBHOOK_SECRET", "").strip()
    if not expected or secret != expected:
        abort(403)

    update = request.get_json(force=True, silent=True) or {}
    message = update.get("message") or {}
    chat = message.get("chat") or {}
    sender = message.get("from") or {}
    texto = (message.get("text") or "").strip()
    chat_id = chat.get("id")

    if not texto or chat_id is None:
        return "ok"

    # De acá en adelante devolvemos 200 pase lo que pase: si contestamos con
    # un error, Telegram reintenta el MISMO update una y otra vez, y como el
    # gasto ya se guardó terminás con la misma fila repetida en la planilla.
    try:
        config = _get_config()
        if not _autorizado(config, sender.get("id")):
            return "ok"

        ws = get_worksheet(config)
        if texto.startswith("/"):
            respuesta = _handle_command(ws, texto)
        else:
            respuesta = _handle_gasto(ws, texto)

        if respuesta:
            return _responder(chat_id, respuesta)
    except Exception:
        app.logger.exception("Error procesando el update de Telegram")
    return "ok"


if __name__ == "__main__":
    app.run(debug=True)
