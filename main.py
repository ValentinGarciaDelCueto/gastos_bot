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
import calendar
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
# La columna "Crédito" queda vacía en los gastos sueltos: solo la usan las
# cuotas, para saber cuáles pertenecen a la misma compra.
HEADERS = ["Fecha", "Hora", "Descripción", "Monto", "Crédito"]
CREDITO_COL = 4  # índice de "Crédito" dentro de la fila
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


def _load_credentials_json() -> str:
    """
    Devuelve el JSON de la service account como texto. Dos formas:
      - GOOGLE_CREDENTIALS_FILE=creds.json  -> lee ese archivo (lo más fácil).
      - GOOGLE_CREDENTIALS={...}             -> el JSON pegado en una línea.
    Si está seteado el archivo, tiene prioridad.
    """
    creds_file = os.environ.get("GOOGLE_CREDENTIALS_FILE", "").strip()
    raw = os.environ.get("GOOGLE_CREDENTIALS", "").strip()

    if creds_file:
        if not os.path.isfile(creds_file):
            raise SystemExit(
                f"GOOGLE_CREDENTIALS_FILE apunta a un archivo que no existe: "
                f"{creds_file!r}. Guardá el .json en la carpeta del proyecto."
            )
        with open(creds_file, encoding="utf-8") as f:
            raw = f.read().strip()

    if not raw:
        raise SystemExit(
            "Falta la credencial de Google. Elegí una opción:\n"
            "  1) guardá el .json como creds.json y poné "
            "GOOGLE_CREDENTIALS_FILE=creds.json\n"
            "  2) pegá el JSON entero en GOOGLE_CREDENTIALS."
        )

    try:
        json.loads(raw)
    except json.JSONDecodeError as e:
        raise SystemExit(
            "La credencial de Google no es un JSON válido. Tiene que ser el "
            "CONTENIDO del .json (empieza con '{' y tiene \"private_key\"), "
            "NO el email de la service account. Detalle: " + str(e)
        )
    return raw


def load_config() -> Config:
    """
    Lee y valida las variables de entorno. Si falta algo, corta con un
    mensaje claro en vez de un traceback críptico. Se llama en main(), así
    que importar este módulo (por ejemplo en los tests) no requiere entorno.
    """
    faltantes = [
        v
        for v in ("TELEGRAM_TOKEN", "SHEET_ID")
        if not os.environ.get(v)
    ]
    if faltantes:
        raise SystemExit(
            "Faltan variables de entorno: "
            + ", ".join(faltantes)
            + ".\nRevisá el README (sección Setup) o el archivo .env.example."
        )

    raw_creds = _load_credentials_json()

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
    """
    Si la planilla está vacía, le pone los encabezados. Si ya tiene datos
    pero le falta alguna columna (viene de una versión anterior, sin
    "Crédito"), la agrega sin tocar los gastos que ya estaban cargados.
    """
    valores = ws.get_all_values()
    if not valores:
        ws.append_row(HEADERS)
        return

    actuales = valores[0]
    for i in range(len(actuales), len(HEADERS)):
        ws.update_cell(1, i + 1, HEADERS[i])


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


# Cuántas cuotas admitimos como máximo. Es un tope de sentido común: evita
# que un tipeo ("6000 meses") llene la planilla de filas.
MAX_CUOTAS = 120

_CUOTAS_RE = re.compile(
    r"\b(\d{1,4})\s*(?:meses|mes|cuotas|cuota)\b", re.IGNORECASE
)


def parse_credito(text: str):
    """
    De '/credito 32400 coderhouse curso 6 meses' saca
    ('coderhouse curso', 32400, 6): descripción, monto de CADA cuota y
    cantidad de cuotas.

    La cantidad de cuotas es el número pegado a "meses"/"cuotas"; lo saco
    del texto y el resto lo parsea parse_line(), así que el monto y la
    descripción pueden venir en cualquier orden, igual que en un gasto
    normal. Devuelve None si falta algo.
    """
    text = text.strip()
    if text.startswith("/"):
        _, _, text = text.partition(" ")  # saco el "/credito"

    m = _CUOTAS_RE.search(text)
    if not m:
        return None

    cuotas = int(m.group(1))
    if not 1 <= cuotas <= MAX_CUOTAS:
        return None

    resto = text[: m.start()] + " " + text[m.end():]
    parsed = parse_line(resto)
    if not parsed:
        return None

    descripcion, monto = parsed
    return descripcion, monto, cuotas


def sumar_meses(fecha: datetime, meses: int) -> datetime:
    """
    Suma meses cuidando los días que no existen: 31/01 + 1 mes cae en el
    28/02 (o 29 si es bisiesto), no explota ni se pasa a marzo.
    """
    indice = fecha.month - 1 + meses
    anio = fecha.year + indice // 12
    mes = indice % 12 + 1
    dia = min(fecha.day, calendar.monthrange(anio, mes)[1])
    return fecha.replace(year=anio, month=mes, day=dia)


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


def credito_id_de_fila(fila):
    """El id de crédito de una fila, o None si es un gasto suelto."""
    if len(fila) <= CREDITO_COL:
        return None
    valor = str(fila[CREDITO_COL]).strip()
    return int(valor) if valor.isdigit() else None


def next_credito_id(ws) -> int:
    """
    El id que le toca al próximo crédito: el mayor que haya en la planilla + 1.

    Si borrás el último crédito, su número vuelve a quedar libre y el que
    cargues después lo reusa. No llevamos un contador aparte porque habría
    que guardarlo en algún lado y el /creditos siempre muestra los vigentes.
    """
    ids = [
        cid
        for cid in (credito_id_de_fila(r) for r in ws.get_all_values()[1:])
        if cid is not None
    ]
    return max(ids, default=0) + 1


def record_credito(ws, descripcion: str, monto, cuotas: int, now: datetime):
    """
    Carga las cuotas de una compra en cuotas: una fila por mes, arrancando
    hoy. Las cuotas futuras quedan con fecha futura, así que no ensucian el
    /hoy ni el /mes actuales — aparecen solas cuando llega su mes.

    Todas las filas comparten un id, que es lo que después le permite a
    /borrarcredito borrar la compra entera de una.

    Devuelve (filas_escritas, total_del_dia, id_del_credito).
    """
    credito_id = next_credito_id(ws)
    hora = now.strftime("%H:%M")
    filas = []
    for i in range(cuotas):
        fecha = sumar_meses(now, i)
        filas.append(
            [
                fecha.strftime("%d/%m/%Y"),
                hora,
                f"{descripcion} ({i + 1}/{cuotas})",
                monto,
                credito_id,
            ]
        )

    ws.append_rows(filas, value_input_option="USER_ENTERED")
    return filas, day_total(ws, now.strftime("%d/%m/%Y")), credito_id


def _bloques_contiguos(indices):
    """[3, 4, 5, 9, 10] -> [(3, 5), (9, 10)]"""
    bloques = []
    for i in indices:
        if bloques and i == bloques[-1][1] + 1:
            bloques[-1][1] = i
        else:
            bloques.append([i, i])
    return [tuple(b) for b in bloques]


def delete_credito(ws, credito_id: int):
    """
    Borra todas las cuotas de un crédito. Devuelve las filas borradas (lista
    vacía si ese id no existe).
    """
    rows = ws.get_all_values()
    indices = [
        i
        for i in range(2, len(rows) + 1)  # salteo el encabezado
        if credito_id_de_fila(rows[i - 1]) == credito_id
    ]
    if not indices:
        return []

    borradas = [rows[i - 1] for i in indices]
    # De abajo hacia arriba y de a bloques: cada borrado corre hacia arriba
    # las filas que quedaron debajo, y las cuotas suelen ser contiguas, así
    # que normalmente esto es una sola llamada a la API.
    for inicio, fin in reversed(_bloques_contiguos(indices)):
        ws.delete_rows(inicio, fin)
    return borradas


_SUFIJO_CUOTA_RE = re.compile(r"\s*\(\d+\s*/\s*\d+\)\s*$")


def list_creditos(ws):
    """
    Resumen de cada crédito cargado, ordenado por id:
    [{"id": 1, "descripcion": "coderhouse curso", "cuotas": 6, "monto": 32400}]
    """
    creditos = {}
    for fila in ws.get_all_values()[1:]:
        cid = credito_id_de_fila(fila)
        if cid is None or len(fila) < 4:
            continue
        info = creditos.setdefault(
            cid,
            {
                "id": cid,
                "descripcion": _SUFIJO_CUOTA_RE.sub("", fila[2]).strip(),
                "cuotas": 0,
                "monto": parse_amount(fila[3]) or 0,
            },
        )
        info["cuotas"] += 1
    return [creditos[cid] for cid in sorted(creditos)]


def build_credito_confirmation(descripcion, monto, filas, total_dia, credito_id) -> str:
    """Texto que ve el usuario después de cargar un crédito."""
    cuotas = len(filas)
    return (
        f"💳 {descripcion}  (crédito #{credito_id})\n"
        f"{cuotas} cuotas de {format_money(monto)}"
        f" · Total {format_money(monto * cuotas)}\n"
        f"Primera: {filas[0][0]} · Última: {filas[-1][0]}\n"
        f"📊 Total hoy: {format_money(total_dia)}\n"
        f"Para darlo de baja: /borrarcredito {credito_id}"
    )


def parse_credito_id(text: str):
    """De '/borrarcredito 2' saca 2. None si no hay un número."""
    text = text.strip()
    if text.startswith("/"):
        _, _, text = text.partition(" ")
    m = re.search(r"\d+", text)
    return int(m.group()) if m else None


def build_borrado_credito(credito_id: int, borradas) -> str:
    """Texto de confirmación de /borrarcredito."""
    if not borradas:
        return (
            f"No encontré el crédito #{credito_id}. 🤔\n"
            "Mirá cuáles tenés con /creditos"
        )
    descripcion = _SUFIJO_CUOTA_RE.sub("", borradas[0][2]).strip()
    monto = parse_amount(borradas[0][3]) or 0
    return (
        f"🗑️ Crédito #{credito_id} dado de baja: {descripcion}\n"
        f"Borré {len(borradas)} cuotas"
        f" · {format_money(monto * len(borradas))} en total"
    )


def build_creditos_list(creditos) -> str:
    """Listado de /creditos."""
    if not creditos:
        return (
            "No tenés créditos cargados.\n"
            "Se cargan con: /credito 32400 coderhouse curso 6 meses"
        )

    lineas = ["💳 Créditos cargados:", ""]
    for c in creditos:
        total = c["monto"] * c["cuotas"]
        lineas.append(f"#{c['id']} — {c['descripcion']}")
        lineas.append(
            f"   {c['cuotas']} cuotas de {format_money(c['monto'])}"
            f" · Total {format_money(total)}"
        )
    lineas.append("")
    lineas.append("Para dar de baja uno: /borrarcredito <número>")
    return "\n".join(lineas)


def delete_last_expense(ws):
    """Borra la última fila cargada. Devuelve la fila borrada o None si no hay nada."""
    rows = ws.get_all_values()
    last_row_idx = len(rows)
    if last_row_idx <= 1:  # solo header (o vacío)
        return None
    fila = rows[-1]
    ws.delete_rows(last_row_idx)
    return fila


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
        "Si comprás en cuotas:\n"
        "  /credito 32400 coderhouse curso 6 meses\n\n"
        "Comandos: /hoy (total del día) · /mes (total del mes) · "
        "/creditos (los que tenés en cuotas) · /borrarcredito (da de baja "
        "uno) · /borraranterior (borra el último gasto cargado)."
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


CREDITO_AYUDA = (
    "Para cargar algo en cuotas mandame:\n"
    "  /credito 32400 coderhouse curso 6 meses\n\n"
    "El monto es el de CADA cuota. La primera se carga hoy y el resto "
    "quedan agendadas mes a mes."
)

BORRAR_CREDITO_AYUDA = (
    "Decime cuál borrar:\n"
    "  /borrarcredito 1\n\n"
    "Borra todas las cuotas de ese crédito. Para ver los números: /creditos"
)


async def credito(update: Update, context: ContextTypes.DEFAULT_TYPE):
    config: Config = context.bot_data["config"]
    if not _autorizado(config, update):
        return

    parsed = parse_credito(update.message.text or "")
    if not parsed:
        await update.message.reply_text(CREDITO_AYUDA)
        return

    descripcion, monto, cuotas = parsed
    try:
        ws = await asyncio.to_thread(get_worksheet, config)
        now = datetime.now(TZ)
        filas, total_dia, credito_id = await asyncio.to_thread(
            record_credito, ws, descripcion, monto, cuotas, now
        )
    except Exception:
        logger.exception("Error guardando las cuotas en la planilla")
        await update.message.reply_text(
            "⚠️ No pude guardar las cuotas. Probá de nuevo en un ratito."
        )
        return

    await update.message.reply_text(
        build_credito_confirmation(descripcion, monto, filas, total_dia, credito_id)
    )


async def creditos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    config: Config = context.bot_data["config"]
    if not _autorizado(config, update):
        return
    try:
        ws = await asyncio.to_thread(get_worksheet, config)
        lista = await asyncio.to_thread(list_creditos, ws)
    except Exception:
        logger.exception("Error leyendo la planilla")
        await update.message.reply_text(
            "⚠️ No pude leer la planilla. Probá de nuevo en un ratito."
        )
        return
    await update.message.reply_text(build_creditos_list(lista))


async def borrar_credito(update: Update, context: ContextTypes.DEFAULT_TYPE):
    config: Config = context.bot_data["config"]
    if not _autorizado(config, update):
        return

    credito_id = parse_credito_id(update.message.text or "")
    if credito_id is None:
        await update.message.reply_text(BORRAR_CREDITO_AYUDA)
        return

    try:
        ws = await asyncio.to_thread(get_worksheet, config)
        borradas = await asyncio.to_thread(delete_credito, ws, credito_id)
    except Exception:
        logger.exception("Error borrando el crédito de la planilla")
        await update.message.reply_text(
            "⚠️ No pude borrar el crédito. Probá de nuevo en un ratito."
        )
        return

    await update.message.reply_text(
        build_borrado_credito(credito_id, borradas)
    )


async def borrar_anterior(update: Update, context: ContextTypes.DEFAULT_TYPE):
    config: Config = context.bot_data["config"]
    if not _autorizado(config, update):
        return
    try:
        ws = await asyncio.to_thread(get_worksheet, config)
        fila = await asyncio.to_thread(delete_last_expense, ws)
    except Exception:
        logger.exception("Error borrando en la planilla")
        await update.message.reply_text(
            "⚠️ No pude borrar en la planilla. Probá de nuevo en un ratito."
        )
        return

    if fila is None:
        await update.message.reply_text("No hay nada para borrar. 🤷")
        return

    _, _, desc, monto = fila
    await update.message.reply_text(
        f"🗑️ Borrado: {desc} {format_money(parse_amount(monto) or 0)}"
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
    app.add_handler(CommandHandler("borraranterior", borrar_anterior))
    app.add_handler(CommandHandler("credito", credito))
    app.add_handler(CommandHandler("creditos", creditos))
    app.add_handler(CommandHandler("borrarcredito", borrar_credito))
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )

    logger.info("Bot corriendo...")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
