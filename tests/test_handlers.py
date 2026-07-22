"""
Tests de los handlers async, sin red. Preseteamos main._worksheet con una
hoja falsa (así get_worksheet no intenta autenticar) y simulamos el Update
y el Context de Telegram con objetos mínimos. Prueba el flujo real:
autorización -> parseo -> asyncio.to_thread -> respuesta.
"""

import asyncio

import pytest

import main


# --- Fakes mínimos -------------------------------------------------------
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


class FakeMessage:
    def __init__(self, text):
        self.text = text
        self.replies = []

    async def reply_text(self, texto):
        self.replies.append(texto)


class FakeUser:
    def __init__(self, uid):
        self.id = uid


class FakeUpdate:
    def __init__(self, text, uid):
        self.message = FakeMessage(text)
        self.effective_user = FakeUser(uid)


class FakeContext:
    def __init__(self, config):
        self.bot_data = {"config": config}


def _config(allowed_user_id=0):
    return main.Config(
        telegram_token="t",
        sheet_id="s",
        google_credentials="{}",
        allowed_user_id=allowed_user_id,
    )


@pytest.fixture
def hoja():
    """Instala una hoja falsa en el cache y la limpia al terminar."""
    ws = FakeWorksheet([main.HEADERS])
    main._worksheet = ws
    yield ws
    main._worksheet = None


# --- Tests ---------------------------------------------------------------
def test_handle_message_guarda_y_confirma(hoja):
    update = FakeUpdate("combi 10000, snacks 3000", uid=111)
    asyncio.run(main.handle_message(update, FakeContext(_config())))

    assert len(hoja.rows) == 3  # encabezado + 2 gastos
    reply = update.message.replies[0]
    assert "✅ combi: $10.000" in reply
    assert "Subtotal: $13.000" in reply
    assert "📊 Total hoy: $13.000" in reply


def test_handle_message_sin_gastos_no_escribe(hoja):
    update = FakeUpdate("hola que tal", uid=111)
    asyncio.run(main.handle_message(update, FakeContext(_config())))

    assert len(hoja.rows) == 1  # solo el encabezado
    assert "No pude leer" in update.message.replies[0]


def test_handle_message_usuario_no_autorizado(hoja):
    # allowed_user_id=999 pero el usuario es 111 -> ni responde ni escribe.
    update = FakeUpdate("combi 10000", uid=111)
    asyncio.run(main.handle_message(update, FakeContext(_config(999))))

    assert update.message.replies == []
    assert len(hoja.rows) == 1


def test_total_hoy_responde_total(hoja):
    now = main.datetime.now(main.TZ)
    fecha = now.strftime("%d/%m/%Y")
    hoja.rows.append([fecha, "10:00", "combi", "10000"])

    update = FakeUpdate("/hoy", uid=111)
    asyncio.run(main.total_hoy(update, FakeContext(_config())))

    assert f"Total de hoy ({fecha})" in update.message.replies[0]
    assert "$10.000" in update.message.replies[0]


def test_error_de_planilla_avisa_al_usuario(hoja):
    # Rompemos get_all_values para simular una falla de red de Sheets.
    def boom():
        raise RuntimeError("sheets caido")

    hoja.get_all_values = boom
    update = FakeUpdate("combi 10000", uid=111)
    asyncio.run(main.handle_message(update, FakeContext(_config())))

    assert "No pude guardar" in update.message.replies[0]
