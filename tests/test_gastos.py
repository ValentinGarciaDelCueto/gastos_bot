"""
Tests del bot de gastos. Corren sin credenciales: la lógica pura no toca
Telegram ni Google, y la parte de planilla se prueba con una hoja falsa
en memoria (FakeWorksheet) que imita la interfaz de gspread.

    pytest -q
"""

from datetime import datetime

import main


# ---------------------------------------------------------------------------
# Hoja falsa: imita lo justo de gspread que usa el bot.
# get_all_values() devuelve strings, igual que la API real.
# ---------------------------------------------------------------------------
class FakeWorksheet:
    def __init__(self, rows=None):
        self.rows = [[str(c) for c in r] for r in (rows or [])]

    def get_all_values(self):
        return [list(r) for r in self.rows]

    def append_row(self, row, value_input_option=None):
        self.rows.append([str(c) for c in row])

    def append_rows(self, rows, value_input_option=None):
        for r in rows:
            self.rows.append([str(c) for c in r])


# ---------------------------------------------------------------------------
# parse_amount
# ---------------------------------------------------------------------------
def test_parse_amount_entero():
    assert main.parse_amount("3000") == 3000


def test_parse_amount_miles_con_punto():
    assert main.parse_amount("10.000") == 10000


def test_parse_amount_decimal_con_coma():
    assert main.parse_amount("10,50") == 10.5


def test_parse_amount_miles_y_decimal():
    assert main.parse_amount("10.000,50") == 10000.5


def test_parse_amount_k():
    assert main.parse_amount("10k") == 10000
    assert main.parse_amount("1.5k") == 1500


def test_parse_amount_con_signo_peso():
    assert main.parse_amount("$3000") == 3000


def test_parse_amount_no_numero():
    assert main.parse_amount("combi") is None
    assert main.parse_amount("") is None


# ---------------------------------------------------------------------------
# parse_line
# ---------------------------------------------------------------------------
def test_parse_line_desc_y_monto():
    assert main.parse_line("combi 10000") == ("combi", 10000)


def test_parse_line_orden_invertido():
    assert main.parse_line("10000 combi") == ("combi", 10000)


def test_parse_line_descripcion_multipalabra():
    assert main.parse_line("cafe con leche 2500") == ("cafe con leche", 2500)


def test_parse_line_sin_descripcion():
    assert main.parse_line("5000") == ("Sin descripción", 5000)


def test_parse_line_sin_monto():
    assert main.parse_line("hola que tal") is None


def test_parse_line_vacia():
    assert main.parse_line("   ") is None


# ---------------------------------------------------------------------------
# parse_message (varios gastos)
# ---------------------------------------------------------------------------
def test_parse_message_por_coma():
    assert main.parse_message("snacks 3000, sube 5000") == [
        ("snacks", 3000),
        ("sube", 5000),
    ]


def test_parse_message_por_linea():
    assert main.parse_message("combi 10000\nsnacks 3000") == [
        ("combi", 10000),
        ("snacks", 3000),
    ]


def test_parse_message_ignora_basura():
    assert main.parse_message("hola\ncombi 10000\nnada aca") == [
        ("combi", 10000),
    ]


def test_parse_message_vacio():
    assert main.parse_message("cualquier cosa sin numeros") == []


# ---------------------------------------------------------------------------
# format_money
# ---------------------------------------------------------------------------
def test_format_money():
    assert main.format_money(3000) == "$3.000"
    assert main.format_money(1234567) == "$1.234.567"
    assert main.format_money(0) == "$0"


# ---------------------------------------------------------------------------
# ensure_headers
# ---------------------------------------------------------------------------
def test_ensure_headers_hoja_vacia():
    ws = FakeWorksheet()
    main.ensure_headers(ws)
    assert ws.rows == [main.HEADERS]


def test_ensure_headers_no_duplica():
    ws = FakeWorksheet([main.HEADERS, ["22/07/2026", "10:00", "combi", "10000"]])
    main.ensure_headers(ws)
    assert ws.rows[0] == [str(c) for c in main.HEADERS]
    assert len(ws.rows) == 2


# ---------------------------------------------------------------------------
# record_expenses + day_total + month_total (integración con hoja falsa)
# ---------------------------------------------------------------------------
def test_record_expenses_agrega_y_totaliza():
    ws = FakeWorksheet([main.HEADERS])
    now = datetime(2026, 7, 22, 14, 30)
    items = [("combi", 10000), ("snacks", 3000)]

    fecha, total = main.record_expenses(ws, items, now)

    assert fecha == "22/07/2026"
    assert total == 13000
    assert len(ws.rows) == 3  # encabezado + 2 gastos
    assert ws.rows[1] == ["22/07/2026", "14:30", "combi", "10000"]


def test_day_total_filtra_por_fecha():
    ws = FakeWorksheet(
        [
            main.HEADERS,
            ["22/07/2026", "10:00", "combi", "10000"],
            ["21/07/2026", "10:00", "viejo", "99999"],
            ["22/07/2026", "12:00", "snacks", "3000"],
        ]
    )
    assert main.day_total(ws, "22/07/2026") == 13000
    assert main.day_total(ws, "21/07/2026") == 99999
    assert main.day_total(ws, "01/01/2026") == 0


def test_month_total_filtra_por_mes():
    ws = FakeWorksheet(
        [
            main.HEADERS,
            ["22/07/2026", "10:00", "combi", "10000"],
            ["05/07/2026", "10:00", "otro", "5000"],
            ["30/06/2026", "10:00", "mes pasado", "88888"],
        ]
    )
    assert main.month_total(ws, "07/2026") == 15000
    assert main.month_total(ws, "06/2026") == 88888


# ---------------------------------------------------------------------------
# build_confirmation
# ---------------------------------------------------------------------------
def test_build_confirmation_un_gasto():
    msg = main.build_confirmation([("combi", 10000)], 10000)
    assert "✅ combi: $10.000" in msg
    assert "📊 Total hoy: $10.000" in msg
    assert "Subtotal" not in msg  # un solo gasto no muestra subtotal


def test_build_confirmation_varios_gastos():
    msg = main.build_confirmation([("combi", 10000), ("snacks", 3000)], 13000)
    assert "✅ combi: $10.000" in msg
    assert "✅ snacks: $3.000" in msg
    assert "Subtotal: $13.000" in msg
    assert "📊 Total hoy: $13.000" in msg
