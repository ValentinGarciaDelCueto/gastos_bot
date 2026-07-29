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
2. **Web app**: Dashboard → "Web" → "Add a new web app" → Flask → Python 3.10+.
   Se crea un WSGI file de ejemplo; reemplazá su contenido por:

   ```python
   import sys
   path = "/home/TU_USUARIO/Bot-Gastos"  # carpeta donde subiste el repo
   if path not in sys.path:
       sys.path.append(path)

   from webhook_app import app as application
   ```

3. Subí el código: Dashboard → "Consoles" → Bash, y ahí:

   ```bash
   git clone https://github.com/ValentinGarciaDelCueto/Gastos-Bot.git
   cd Gastos-Bot
   git checkout gratis
   pip install --user -r requirements.txt
   ```

4. **Variables de entorno**: en la pestaña "Web", sección "Environment
   variables" (o subí un `.env` al lado de `webhook_app.py` — se carga solo).
   Mismas variables que Railway (`TELEGRAM_TOKEN`, `SHEET_ID`,
   `ALLOWED_USER_ID`, `GOOGLE_CREDENTIALS` o `GOOGLE_CREDENTIALS_FILE`), más:

   | Variable         | Valor                                                |
   |------------------|-------------------------------------------------------|
   | `WEBHOOK_SECRET` | Texto random largo (`python -c "import secrets; print(secrets.token_urlsafe(24))"`) |

5. Reload de la web app (botón verde "Reload" en la pestaña "Web").
6. Desde la consola Bash, avisale a Telegram dónde está tu bot (una sola vez):

   ```bash
   python set_webhook.py https://TU_USUARIO.pythonanywhere.com
   ```

Listo, corre gratis. Ojo: el plan free de PythonAnywhere apaga la web app si
no entrás a loguearte por 1 mes — te avisa por mail una semana antes. Cuando
entres, apretá el botón "Run until 1 month from today" (pestaña Web) para
extenderla otro mes.

---

## Comandos

- Mandar gastos: `combi 10000` (o varios juntos)
- `/hoy` — total gastado hoy
- `/mes` — total gastado en el mes
- `/borraranterior` — borra el último gasto cargado
- `/start` — instrucciones

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
