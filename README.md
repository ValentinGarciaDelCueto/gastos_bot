# Bot de Gastos 💸

Bot de Telegram que registra tus gastos en Google Sheets. Le mandás un mensaje
con la descripción y el monto, y lo carga solo en una planilla prolija.

```
combi 10000
snacks 3000, sube 5000
```

El bot detecta el número como monto y el resto como descripción (funciona en
cualquier orden), te confirma la carga y te dice el total del día.

---

## Setup (una sola vez)

Son 4 pasos. Después de esto te olvidás para siempre.

### 1. Crear el bot en Telegram

1. En Telegram, buscá a **@BotFather**.
2. Mandale `/newbot` y seguí los pasos (nombre y username).
3. Te va a dar un **token** tipo `123456:ABC-DEF...`. Guardalo.
4. Para saber tu **user id**, hablale a **@userinfobot** — te devuelve tu ID
   numérico. Guardalo (sirve para que solo vos puedas cargar gastos).

### 2. Crear la planilla de Google Sheets

1. Creá una planilla nueva en Google Sheets.
2. De la URL, copiá el **ID**: en
   `docs.google.com/spreadsheets/d/`**`ESTO_ES_EL_ID`**`/edit`
3. Dejala vacía — el bot le pone los encabezados solo la primera vez.

### 3. Crear la Service Account de Google (para que el bot escriba)

1. Andá a [console.cloud.google.com](https://console.cloud.google.com) y creá
   un proyecto (o usá uno existente).
2. Habilitá **Google Sheets API** y **Google Drive API** (buscalas en
   "APIs y servicios" → "Habilitar APIs").
3. En "Credenciales" → "Crear credenciales" → **Cuenta de servicio**.
4. Una vez creada, entrá a la cuenta de servicio → pestaña "Claves" →
   "Agregar clave" → **JSON**. Se descarga un archivo `.json`.
5. Abrí ese JSON, buscá el campo `"client_email"` (algo como
   `bot@proyecto.iam.gserviceaccount.com`) y **compartí tu planilla con ese
   email como Editor** (botón Compartir en Google Sheets). Este paso es el que
   más se olvida: sin esto el bot no puede escribir.

### 4. Desplegar en Railway

1. Subí esta carpeta a un repo de GitHub.
2. Entrá a [railway.app](https://railway.app), "New Project" → "Deploy from
   GitHub repo" → elegí tu repo.
3. En la pestaña **Variables**, cargá estas variables de entorno:

   | Variable              | Valor                                             |
   |-----------------------|---------------------------------------------------|
   | `TELEGRAM_TOKEN`      | El token de BotFather                             |
   | `SHEET_ID`            | El ID de tu planilla                              |
   | `ALLOWED_USER_ID`     | Tu user id de Telegram                            |
   | `GOOGLE_CREDENTIALS`  | **Todo el contenido del JSON**, pegado tal cual   |

   > Para `GOOGLE_CREDENTIALS`: abrí el `.json`, copiá todo el contenido
   > (desde `{` hasta `}`) y pegalo como valor. En una sola línea está bien.

4. Railway detecta el `requirements.txt` y el `Procfile` solos y arranca el
   bot como un *worker* (`worker: python main.py`). No hace falta que
   configures nada más.

Listo. El bot queda corriendo 24/7. Empezá a mandarle gastos. 🎉

### 4-bis. Alternativa gratis: PythonAnywhere (rama `gratis`)

Railway es de pago (~$5/mes). Esta rama corre gratis en PythonAnywhere, con
webhook en vez de polling: en vez de que el bot esté todo el rato
preguntándole a Telegram si hay mensajes nuevos, Telegram le avisa por HTTP
solo cuando llega uno.

1. Creá cuenta gratis en [pythonanywhere.com](https://www.pythonanywhere.com).

2. **Traé el código**: Dashboard → "Consoles" → "Bash", y ahí:

   ```bash
   git clone https://github.com/ValentinGarciaDelCueto/gastos_bot.git
   cd gastos_bot
   git checkout gratis
   ```

3. **Web app**: Dashboard → "Web" → "Add a new web app" → Flask → Python 3.10.
   Anotá qué versión de Python elegiste, la vas a necesitar en el paso 4.

   Se crea un WSGI file de ejemplo. Abrilo (link "WSGI configuration file",
   en la misma pestaña "Web") y reemplazá TODO su contenido por:

   ```python
   import sys
   path = "/home/TU_USUARIO/gastos_bot"
   if path not in sys.path:
       sys.path.append(path)

   from webhook_app import app as application
   ```

   > Ojo con las mayúsculas de `TU_USUARIO`: Linux distingue, y si no coincide
   > exacto con tu usuario vas a ver `ModuleNotFoundError: No module named
   > 'webhook_app'` en el error log.

4. **Dependencias**, con la misma versión de Python que elegiste arriba:

   ```bash
   pip3.10 install --user -r ~/gastos_bot/requirements.txt
   ```

   > Tiene que ser `pip3.10` (o la versión que hayas elegido), no `pip` a
   > secas: si instalás para otra versión, la web app no las encuentra y el
   > error log dice `No module named 'gspread'`.

5. **Credenciales**, las dos en la carpeta `~/gastos_bot`:

   - Subí tu `creds.json` desde la pestaña "Files" → "Upload a file".
   - Creá el `.env` desde la consola (`nano ~/gastos_bot/.env`) con las mismas
     variables que Railway (`TELEGRAM_TOKEN`, `SHEET_ID`, `ALLOWED_USER_ID`,
     `GOOGLE_CREDENTIALS_FILE=creds.json`), más:

   | Variable         | Valor                                                |
   |------------------|-------------------------------------------------------|
   | `WEBHOOK_SECRET` | Texto random largo (`python3 -c "import secrets; print(secrets.token_urlsafe(24))"`) |

6. Reload de la web app (botón verde "Reload" en la pestaña "Web").

7. Desde la consola Bash, avisale a Telegram dónde está tu bot (una sola vez):

   ```bash
   python3 set_webhook.py https://TU_USUARIO.pythonanywhere.com
   ```

Listo, corre gratis. Ojo: el plan free de PythonAnywhere apaga la web app si
no entrás a loguearte por 1 mes — te avisa por mail una semana antes. Cuando
entres, apretá el botón "Run until 1 month from today" (pestaña Web) para
extenderla otro mes.

#### Si algo no anda

El error log de la pestaña "Web" te dice qué pasó. Para ver cómo lo está
viendo Telegram (útil cuando el bot no contesta nada):

```bash
curl "https://api.telegram.org/bot<TU_TOKEN>/getWebhookInfo"
```

- `"pending_update_count"` alto y creciendo → el bot está devolviendo error y
  Telegram reintenta. Mirá `"last_error_message"`.
- `403 FORBIDDEN` → el `WEBHOOK_SECRET` del `.env` no coincide con el de la
  URL registrada, o la web app no está leyendo el `.env`.
- `500` → revisá el traceback completo en el error log.

Una advertencia sobre el plan free: **no deja salir hacia `api.telegram.org`**
(el proxy contesta `503 Service Unavailable`). Por eso `webhook_app.py`
contesta dentro de la respuesta del webhook en vez de llamar a la Bot API. Si
agregás alguna función que necesite hablar con Telegram por su cuenta (mandar
un recordatorio, por ejemplo), no va a andar en el plan free.

---

## Comandos

- Mandar gastos: `combi 10000` (o varios juntos)
- `/hoy` — total gastado hoy
- `/mes` — total gastado en el mes
- `/credito` — compra en cuotas (ver abajo)
- `/creditos` — lista los créditos cargados
- `/borrarcredito <n>` — da de baja un crédito entero
- `/borraranterior` — borra el último gasto cargado
- `/start` — instrucciones

### Compras en cuotas

```
/credito 32400 coderhouse curso 6 meses
```

El monto es el de **cada cuota**, no el total. Eso carga 6 filas de $32.400,
una por mes, con la descripción numerada (`coderhouse curso (1/6)`,
`(2/6)`, …). La primera cuota es de hoy y las otras quedan con fecha futura,
así que aparecen solas en el `/mes` que les toca.

Acepta `meses`, `mes`, `cuotas` o `cuota`, y el monto puede ir antes o después
de la descripción — igual que un gasto normal.

Cada crédito recibe un número, que va en la columna **Crédito** de la planilla
y te sirve para darlo de baja después:

```
/creditos              → #1 coderhouse curso · 6 cuotas de $32.400
/borrarcredito 1       → borra las 6 cuotas de una
```

> El número del último crédito vuelve a quedar libre si lo borrás, así que
> mirá `/creditos` antes de borrar en vez de fiarte de un mensaje viejo.

La columna `Crédito` queda vacía en los gastos sueltos. Si tu planilla es
anterior a esta versión, el bot le agrega la columna solo, sin tocar lo que
ya tenías cargado.

## Formatos de monto que entiende

`3000` · `3.000` · `10.000,50` · `10k` · `1.5k` · `$3000`

> Convención argentina: el `.` separa miles y la `,` es decimal.

---

## Correr local (para probar antes de desplegar)

1. Instalá las dependencias:

   ```bash
   pip install -r requirements.txt
   ```

2. Copiá `.env.example` a `.env` y completá tus valores (el bot carga el
   `.env` solo al arrancar):

   ```bash
   cp .env.example .env
   ```

3. Arrancá el bot:

   ```bash
   python main.py
   ```

Si falta alguna variable, el bot te avisa exactamente cuál en vez de tirar
un error críptico.

## Tests (no necesitan credenciales)

La lógica de parseo y de la planilla se prueba con una hoja falsa en memoria,
así que corren sin tokens ni Google:

```bash
pip install -r requirements-dev.txt
pytest -q
```
