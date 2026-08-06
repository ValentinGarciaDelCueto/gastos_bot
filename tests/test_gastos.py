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

    def delete_rows(self, start_index, end_index=None):
        end = start_index if end_index is None else end_index
        del self.rows[start_index - 1 : end]

    def update_cell(self, row, col, value):
        fila = self.rows[row - 1]
        fila.extend([""] * (col - len(fila)))
        fila[col - 1] = str(value)


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


# ---------------------------------------------------------------------------
# /credito: parseo, suma de meses y carga de cuotas
# ---------------------------------------------------------------------------
def test_parse_credito_ejemplo_del_readme():
    assert main.parse_credito("/credito 32400 coderhouse curso 6 meses") == (
        "coderhouse curso",
        32400,
        6,
    )


def test_parse_credito_sin_el_comando_adelante():
    assert main.parse_credito("32400 coderhouse 6 meses") == (
        "coderhouse",
        32400,
        6,
    )


def test_parse_credito_acepta_cuotas_y_singular():
    assert main.parse_credito("/credito 5000 tele 3 cuotas")[2] == 3
    assert main.parse_credito("/credito 5000 tele 1 cuota")[2] == 1
    assert main.parse_credito("/credito 5000 tele 1 mes")[2] == 1


def test_parse_credito_orden_invertido():
    # Igual que un gasto normal: el monto puede ir después de la descripción.
    assert main.parse_credito("/credito zapatillas 15000 12 meses") == (
        "zapatillas",
        15000,
        12,
    )


def test_parse_credito_monto_con_formato_argentino():
    assert main.parse_credito("/credito 32.400 curso 6 meses")[1] == 32400
    assert main.parse_credito("/credito 10k curso 6 meses")[1] == 10000


def test_parse_credito_sin_cuotas_devuelve_none():
    assert main.parse_credito("/credito 32400 coderhouse") is None


def test_parse_credito_sin_monto_devuelve_none():
    assert main.parse_credito("/credito coderhouse 6 meses") is None


def test_parse_credito_rechaza_cantidades_absurdas():
    assert main.parse_credito("/credito 100 algo 0 meses") is None
    assert main.parse_credito("/credito 100 algo 6000 meses") is None


def test_sumar_meses_caso_normal():
    assert main.sumar_meses(datetime(2026, 7, 29), 1) == datetime(2026, 8, 29)


def test_sumar_meses_cruza_de_anio():
    assert main.sumar_meses(datetime(2026, 11, 15), 3) == datetime(2027, 2, 15)


def test_sumar_meses_dia_que_no_existe_cae_al_ultimo():
    # 31/01 + 1 mes no es 31/02: cae al último día de febrero.
    assert main.sumar_meses(datetime(2026, 1, 31), 1) == datetime(2026, 2, 28)
    # 2028 es bisiesto.
    assert main.sumar_meses(datetime(2028, 1, 31), 1) == datetime(2028, 2, 29)
    assert main.sumar_meses(datetime(2026, 3, 31), 1) == datetime(2026, 4, 30)


def test_record_credito_carga_una_fila_por_mes():
    ws = FakeWorksheet([main.HEADERS])
    now = datetime(2026, 7, 29, 14, 30)

    filas, total_dia, cid = main.record_credito(ws, "coderhouse curso", 32400, 6, now)

    assert len(filas) == 6
    assert len(ws.rows) == 7  # encabezado + 6 cuotas
    assert cid == 1
    # La primera cuota es hoy y es la única que suma al total del día.
    assert ws.rows[1] == ["29/07/2026", "14:30", "coderhouse curso (1/6)", "32400", "1"]
    assert total_dia == 32400
    # El resto queda agendado mes a mes.
    assert ws.rows[2][0] == "29/08/2026"
    assert ws.rows[6] == ["29/12/2026", "14:30", "coderhouse curso (6/6)", "32400", "1"]


def test_record_credito_no_ensucia_el_mes_actual():
    ws = FakeWorksheet([main.HEADERS])
    now = datetime(2026, 7, 29, 14, 30)

    main.record_credito(ws, "curso", 10000, 3, now)

    assert main.month_total(ws, "07/2026") == 10000  # solo la cuota de julio
    assert main.month_total(ws, "08/2026") == 10000
    assert main.month_total(ws, "09/2026") == 10000


def test_build_credito_confirmation():
    ws = FakeWorksheet([main.HEADERS])
    now = datetime(2026, 7, 29, 14, 30)
    filas, total_dia, cid = main.record_credito(ws, "coderhouse", 32400, 6, now)

    texto = main.build_credito_confirmation("coderhouse", 32400, filas, total_dia, cid)

    assert "coderhouse" in texto
    assert "6 cuotas de $32.400" in texto
    assert "$194.400" in texto  # total
    assert "29/07/2026" in texto and "29/12/2026" in texto
    assert "/borrarcredito 1" in texto  # cómo darlo de baja


# ---------------------------------------------------------------------------
# Ids de crédito: /creditos y /borrarcredito
# ---------------------------------------------------------------------------
def test_los_creditos_reciben_ids_correlativos():
    ws = FakeWorksheet([main.HEADERS])
    now = datetime(2026, 7, 29, 14, 30)

    _, _, primero = main.record_credito(ws, "coderhouse", 32400, 6, now)
    _, _, segundo = main.record_credito(ws, "netflix", 50000, 3, now)

    assert (primero, segundo) == (1, 2)


def test_borrar_el_ultimo_credito_libera_su_id():
    # Comportamiento asumido de "el mayor + 1": el número del último crédito
    # vuelve a quedar disponible cuando lo borrás.
    ws = FakeWorksheet([main.HEADERS])
    now = datetime(2026, 7, 29, 14, 30)

    main.record_credito(ws, "coderhouse", 32400, 6, now)
    _, _, segundo = main.record_credito(ws, "netflix", 50000, 3, now)
    main.delete_credito(ws, segundo)
    _, _, tercero = main.record_credito(ws, "gimnasio", 20000, 12, now)

    assert tercero == 2


def test_borrar_uno_del_medio_no_libera_su_id():
    ws = FakeWorksheet([main.HEADERS])
    now = datetime(2026, 7, 29, 14, 30)

    _, _, primero = main.record_credito(ws, "coderhouse", 32400, 6, now)
    main.record_credito(ws, "netflix", 50000, 3, now)
    main.delete_credito(ws, primero)
    _, _, tercero = main.record_credito(ws, "gimnasio", 20000, 12, now)

    assert tercero == 3


def test_los_gastos_sueltos_no_tienen_id():
    ws = FakeWorksheet([main.HEADERS])
    now = datetime(2026, 7, 29, 14, 30)
    main.record_expenses(ws, [("combi", 10000)], now)

    assert main.credito_id_de_fila(ws.rows[1]) is None
    # Y el primer crédito arranca igual en 1.
    assert main.next_credito_id(ws) == 1


def test_delete_credito_borra_solo_ese_credito():
    ws = FakeWorksheet([main.HEADERS])
    now = datetime(2026, 7, 29, 14, 30)
    main.record_credito(ws, "coderhouse", 32400, 6, now)
    main.record_credito(ws, "netflix", 50000, 3, now)
    main.record_expenses(ws, [("combi", 10000)], now)

    borradas = main.delete_credito(ws, 1)

    assert len(borradas) == 6
    assert all("coderhouse" in b[2] for b in borradas)
    # Quedan las 3 cuotas de netflix, el gasto suelto y el encabezado.
    assert len(ws.rows) == 5
    assert {main.credito_id_de_fila(r) for r in ws.rows[1:]} == {2, None}


def test_delete_credito_con_filas_no_contiguas():
    # Entre cuota y cuota puede haber gastos sueltos cargados después.
    ws = FakeWorksheet(
        [
            main.HEADERS,
            ["29/07/2026", "10:00", "curso (1/2)", "1000", "1"],
            ["29/07/2026", "11:00", "combi", "500", ""],
            ["29/08/2026", "10:00", "curso (2/2)", "1000", "1"],
        ]
    )

    borradas = main.delete_credito(ws, 1)

    assert len(borradas) == 2
    assert len(ws.rows) == 2
    assert ws.rows[1][2] == "combi"


def test_delete_credito_id_inexistente():
    ws = FakeWorksheet([main.HEADERS])
    main.record_credito(ws, "coderhouse", 32400, 6, datetime(2026, 7, 29, 14, 30))

    assert main.delete_credito(ws, 99) == []
    assert len(ws.rows) == 7  # no tocó nada


def test_list_creditos():
    ws = FakeWorksheet([main.HEADERS])
    now = datetime(2026, 7, 29, 14, 30)
    main.record_credito(ws, "coderhouse curso", 32400, 6, now)
    main.record_credito(ws, "netflix", 50000, 3, now)
    main.record_expenses(ws, [("combi", 10000)], now)

    lista = main.list_creditos(ws)

    assert lista == [
        {"id": 1, "descripcion": "coderhouse curso", "cuotas": 6, "monto": 32400},
        {"id": 2, "descripcion": "netflix", "cuotas": 3, "monto": 50000},
    ]


def test_list_creditos_sin_ninguno():
    ws = FakeWorksheet([main.HEADERS])
    assert main.list_creditos(ws) == []
    assert "No tenés créditos" in main.build_creditos_list([])


def test_parse_credito_id():
    assert main.parse_credito_id("/borrarcredito 2") == 2
    assert main.parse_credito_id("/borrarcredito 13") == 13
    assert main.parse_credito_id("/borrarcredito") is None
    assert main.parse_credito_id("/borrarcredito todos") is None


def test_build_borrado_credito():
    ws = FakeWorksheet([main.HEADERS])
    main.record_credito(ws, "coderhouse", 32400, 6, datetime(2026, 7, 29, 14, 30))
    borradas = main.delete_credito(ws, 1)

    texto = main.build_borrado_credito(1, borradas)

    assert "coderhouse" in texto
    assert "(1/6)" not in texto  # sin el sufijo de cuota
    assert "6 cuotas" in texto
    assert "$194.400" in texto


def test_build_borrado_credito_inexistente():
    assert "No encontré" in main.build_borrado_credito(9, [])


def test_ensure_headers_agrega_la_columna_credito_a_planillas_viejas():
    # Planilla creada antes de que existieran los créditos.
    ws = FakeWorksheet(
        [
            ["Fecha", "Hora", "Descripción", "Monto"],
            ["29/07/2026", "10:00", "combi", "10000"],
        ]
    )

    main.ensure_headers(ws)

    assert ws.rows[0] == main.HEADERS
    assert ws.rows[1] == ["29/07/2026", "10:00", "combi", "10000"]  # intacto


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


def test_delete_last_expense_borra_ultima_fila():
    ws = FakeWorksheet(
        [
            main.HEADERS,
            ["22/07/2026", "10:00", "combi", "10000"],
            ["22/07/2026", "12:00", "snacks", "3000"],
        ]
    )
    fila = main.delete_last_expense(ws)
    assert fila == ["22/07/2026", "12:00", "snacks", "3000"]
    assert ws.rows == [main.HEADERS, ["22/07/2026", "10:00", "combi", "10000"]]


def test_delete_last_expense_solo_header_devuelve_none():
    ws = FakeWorksheet([main.HEADERS])
    assert main.delete_last_expense(ws) is None
    assert ws.rows == [main.HEADERS]


def test_delete_last_expense_hoja_vacia_devuelve_none():
    ws = FakeWorksheet([])
    assert main.delete_last_expense(ws) is None
    assert ws.rows == []


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
