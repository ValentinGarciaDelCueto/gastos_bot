"""
Bot de Telegram para registrar gastos en Google Sheets.

Uso: le mandás al bot mensajes con formato "descripción monto".
Podés mandar varios de una, uno por línea o separados por coma:

    combi 10000
    snacks 3000, sube 5000

El bot detecta el número como monto y el resto como descripción,
sin importar el orden ("10000 combi" también funciona).
Responde con una confirmación y el total gastado en el día.

Comandos: /start, /hoy (total del día), /mes (total del mes).
"""

import os
import re
import json
import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

# Carga opcional de un archivo .env cuando corrés local. En producción
# (Railway) las variables ya vienen del entorno y esto no hace nada.
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

import gspread
from google.oauth2.service_account import Credentials
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------
TZ = ZoneInfo("America/Argentina/Buenos_Aires")
HEADERS = ["Fecha", "Hora", "Descripción", "Monto"]
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuración (se lee del entorno al arrancar — ver README)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Config:
    telegram_token: str
    sheet_id: str
    google_credentials: str
    # Tu user id de Telegram. Solo vos podés cargar gastos. 0 = cualquiera.
    allowed_user_id: int = 0


def load_config() -> Config:
    """
    Lee y valida las variables de entorno. Si falta algo, corta con un
    mensaje claro en vez de un traceback críptico. Se llama en main(), así
    que importar este módulo (por ejemplo en los tests) no requiere entorno.
    """
    faltantes = [
        v
        for v in ("TELEGRAM_TOKEN", "SHEET_ID", "GOOGLE_CREDENTIALS")
        if not os.environ.get(v)
    ]
    if faltantes:
        raise SystemExit(
            "Faltan variables de entorno: "
            + ", ".join(faltantes)
            + ".\nRevisá el README (sección Setup) o el archivo .env.example."
        )

    raw_creds = os.environ["GOOGLE_CREDENTIALS"]
    try:
        json.loads(raw_creds)
    except json.JSONDecodeError as e:
        raise SystemExit(
            "GOOGLE_CREDENTIALS no es un JSON válido. Pegá el contenido "
            f"completo del archivo de la service account. Detalle: {e}"
        )

    raw_uid = os.environ.get("ALLOWED_USER_ID", "0").strip() or "0"
    try:
        allowed_uid = int(raw_uid)
    except ValueError:
        raise SystemExit(
            f"ALLOWED_USER_ID debe ser un número entero (recibí {raw_uid!r})."
        )

    return Config(
        telegram_token=os.environ["TELEGRAM_TOKEN"],
        sheet_id=os.environ["SHEET_ID"],
        google_credentials=raw_creds,
        allowed_user_id=allowed_uid,
    )


# ---------------------------------------------------------------------------
# Google Sheets (la hoja se cachea: se autentica una sola vez)
# ---------------------------------------------------------------------------
_worksheet = None


def get_worksheet(config: Config):
    """Conecta con la planilla (una vez) y devuelve la primera hoja."""
    global _worksheet
    if _worksheet is not None:
        return _worksheet

    creds_dict = json.loads(config.google_credentials)
    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    client = gspread.authorize(creds)
    ws = client.open_by_key(config.sheet_id).sheet1
    ensure_headers(ws)
    _worksheet = ws
    return ws


def ensure_headers(ws) -> None:
    """Si la planilla está vacía, le pone los encabezados."""
    if not ws.get_all_values():
        ws.append_row(HEADERS)


# ---------------------------------------------------------------------------
# Parseo de mensajes (lógica pura, sin dependencias de Telegram ni Sheets)
# ---------------------------------------------------------------------------
def parse_amount(token: str):
    """
    Convierte un texto en número, con convención argentina:
    el "." separa miles y la "," es decimal. Soporta:
      3000  |  3.000  |  10.000,50  |  10k  |  1.5k  |  $3000
    Devuelve un int/float o None si no es un número.
    """
    t = token.lower().strip().lstrip("$").strip()

    # Formato "10k" / "1.5k"
    m = re.fullmatch(r"(\d+(?:[.,]\d+)?)k", t)
    if m:
        return float(m.group(1).replace(",", ".")) * 1000

    # Quito separadores de miles (.) y normalizo el decimal (,) a punto.
    # "10.000" -> 10000 ; "10,50" -> 10.50 ; "10.000,50" -> 10000.50
    cleaned = t.replace(".", "").replace(",", ".")
    if re.fullmatch(r"\d+(?:\.\d+)?", cleaned):
        num = float(cleaned)
        return int(num) if num.is_integer() else num
    return None


def parse_line(line: str):
    """
    De una línea tipo 'combi 10000' saca (descripción, monto).
    El monto es el ÚLTIMO token que parsea como número; el resto es
    la descripción. Devuelve None si no encuentra un monto.
    """
    line = line.strip()
    if not line:
        return None

    tokens = line.split()
    amount = None
    amount_idx = None
    # Recorro de atrás hacia adelante buscando el monto.
    for i in range(len(tokens) - 1, -1, -1):
        val = parse_amount(tokens[i])
        if val is not None:
            amount = val
            amount_idx = i
            break

    if amount is None:
        return None

    desc_tokens = tokens[:amount_idx] + tokens[amount_idx + 1:]
    description = " ".join(desc_tokens).strip() or "Sin descripción"
    return description, amount


def parse_message(text: str):
    """Un mensaje puede tener varios gastos (por línea o por coma)."""
    items = []
    for chunk in re.split(r"[\n,]+", text):
        parsed = parse_line(chunk)
        if parsed:
            items.append(parsed)
    return items


def format_money(n) -> str:
    """3000 -> $3.000  (formato argentino)"""
    return "$" + f"{n:,.0f}".replace(",", ".")


# ---------------------------------------------------------------------------
# Lógica de negocio sobre la planilla (sync, testeable con una hoja falsa)
# ---------------------------------------------------------------------------
def record_expenses(ws, items, now: datetime):
    """Agrega los gastos y devuelve (fecha, total_del_dia)."""
    fecha = now.strftime("%d/%m/%Y")
    hora = now.strftime("%H:%M")
    filas = [[fecha, hora, desc, monto] for desc, monto in items]
    ws.append_rows(filas, value_input_option="USER_ENTERED")
    return fecha, day_total(ws, fecha)


def day_total(ws, fecha: str):
    """Suma los montos cuyas filas son de la fecha dada (dd/mm/aaaa)."""
    rows = ws.get_all_values()[1:]  # salteo el encabezado
    return sum(
        parse_amount(r[3]) or 0
        for r in rows
        if len(r) >= 4 and r[0] == fecha
    )


def month_total(ws, mes_anio: str):
    """Suma los montos del mes dado (mm/aaaa)."""
    rows = ws.get_all_values()[1:]
    total = 0
    for r in rows:
        if len(r) >= 4 and r[0].count("/") == 2:
            if r[0].split("/", 1)[1] == mes_anio:
                total += parse_amount(r[3]) or 0
    return total


def build_confirmation(items, total_dia) -> str:
    """Arma el texto de confirmación que ve el usuario."""
    lineas = [f"✅ {desc}: {format_money(monto)}" for desc, monto in items]
    respuesta = "\n".join(lineas)
    if len(items) > 1:
        subtotal = sum(monto for _, monto in items)
        respuesta += f"\n\nSubtotal: {format_money(subtotal)}"
    respuesta += f"\n📊 Total hoy: {format_money(total_dia)}"
    return respuesta


# ---------------------------------------------------------------------------
# Handlers del bot (finos: parsean, delegan a la lógica, responden)
# ---------------------------------------------------------------------------
def _autorizado(config: Config, update: Update) -> bool:
    if not config.allowed_user_id:
        return True
    return bool(update.effective_user) and (
        update.effective_user.id == config.allowed_user_id
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "¡Hola! Soy tu bot de gastos. 💸\n\n"
        "Mandame tus gastos así:\n"
        "  combi 10000\n"
        "  snacks 3000\n\n"
        "Podés mandar varios juntos, uno por línea o separados por coma.\n"
        "Comandos: /hoy (total del día) · /mes (total del mes)."
    )


async def total_hoy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    config: Config = context.bot_data["config"]
    if not _autorizado(config, update):
        return
    try:
        ws = await asyncio.to_thread(get_worksheet, config)
        fecha = datetime.now(TZ).strftime("%d/%m/%Y")
        total = await asyncio.to_thread(day_total, ws, fecha)
    except Exception:
        logger.exception("Error leyendo la planilla")
        await update.message.reply_text(
            "⚠️ No pude leer la planilla. Probá de nuevo en un ratito."
        )
        return
    await update.message.reply_text(
        f"📅 Total de hoy ({fecha}): {format_money(total)}"
    )


async def total_mes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    config: Config = context.bot_data["config"]
    if not _autorizado(config, update):
        return
    try:
        ws = await asyncio.to_thread(get_worksheet, config)
        mes_anio = datetime.now(TZ).strftime("%m/%Y")
        total = await asyncio.to_thread(month_total, ws, mes_anio)
    except Exception:
        logger.exception("Error leyendo la planilla")
        await update.message.reply_text(
            "⚠️ No pude leer la planilla. Probá de nuevo en un ratito."
        )
        return
    await update.message.reply_text(
        f"🗓️ Total del mes ({mes_anio}): {format_money(total)}"
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    config: Config = context.bot_data["config"]
    # Seguridad: solo respondo a tu user id.
    if not _autorizado(config, update):
        return

    text = (update.message.text or "") if update.message else ""
    items = parse_message(text)

    if not items:
        await update.message.reply_text(
            "No pude leer ningún gasto. 🤔\n"
            "Probá con algo como: combi 10000"
        )
        return

    try:
        ws = await asyncio.to_thread(get_worksheet, config)
        now = datetime.now(TZ)
        _, total_dia = await asyncio.to_thread(record_expenses, ws, items, now)
    except Exception:
        logger.exception("Error guardando en la planilla")
        await update.message.reply_text(
            "⚠️ No pude guardar en la planilla. Probá de nuevo en un ratito."
        )
        return

    await update.message.reply_text(build_confirmation(items, total_dia))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    config = load_config()

    app = Application.builder().token(config.telegram_token).build()
    app.bot_data["config"] = config

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("hoy", total_hoy))
    app.add_handler(CommandHandler("mes", total_mes))
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )

    logger.info("Bot corriendo...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
