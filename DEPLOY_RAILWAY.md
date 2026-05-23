# Deploy en Railway — Taxpy Writer

## 1. Preparar autenticación de NotebookLM (en tu PC local)

Railway no tiene navegador, así que debes obtener las credenciales de NotebookLM en tu máquina local primero.

```bash
# En tu PC local (donde ya tienes notebooklm-py instalado)
notebooklm login
```

Esto abrirá un navegador para autenticarte con Google.

Luego copia el contenido del archivo de credenciales:

```bash
# Windows (PowerShell)
Get-Content $env:USERPROFILE\.notebooklm\profiles\default\storage_state.json -Raw

# macOS / Linux
cat ~/.notebooklm/profiles/default/storage_state.json
```

Copia TODO el contenido JSON (es un string largo).

---

## 2. Crear proyecto en Railway

1. Ve a [railway.app](https://railway.app) y crea un nuevo proyecto.
2. Elige "Deploy from GitHub repo" y selecciona este repositorio.
3. Railway detectará automáticamente el `railway.toml` y `start.sh`.

---

## 3. Configurar variables de entorno

En el dashboard de Railway, ve a **Variables** y agrega:

| Variable | Valor | Obligatorio |
|----------|-------|-------------|
| `TELEGRAM_BOT_TOKEN` | Tu token de BotFather | ✅ Sí |
| `OPENAI_API_KEY` | Tu API key de OpenAI | ✅ Sí |
| `OPENAI_MODEL` | `gpt-4o` | ✅ Sí |
| `NOTEBOOKLM_NOTEBOOK_NAME` | Nombre exacto de tu cuaderno en NotebookLM | ✅ Sí |
| `NOTEBOOKLM_AUTH_JSON` | Contenido de `storage_state.json` (paso 1) | ✅ Sí |
| `GOOGLE_API_KEY` | Tu API key de Google AI Studio | ❌ No (solo si quieres voz después) |
| `WRITER_MAX_TOKENS` | `4000` | ❌ No |
| `WRITER_TEMPERATURE` | `0.5` | ❌ No |

> **Importante**: `NOTEBOOKLM_AUTH_JSON` debe ser el contenido completo del JSON en una sola línea. Railway acepta strings largos.

---

## 4. Deploy

Railway construirá e iniciará el bot automáticamente usando `start.sh`.

El bot usará **polling** de Telegram, lo que mantiene el proceso activo 24/7.

---

## 5. Verificar que funciona

Envía un mensaje a tu bot en Telegram:

```
/manual artículo 21 LIR
```

El bot debería responder:
1. "🔍 Investigando..."
2. "✍️ Escribiendo tu manual..."
3. El contenido completo partido en mensajes
4. Botones para descargar `.md` o `.docx`

---

## 6. Mantener la sesión de NotebookLM viva

Las cookies de Google expiran cada 1–2 semanas. Si el bot empieza a fallar con errores de autenticación:

1. Ejecuta `notebooklm login` de nuevo en tu PC local
2. Copia el nuevo `storage_state.json`
3. Actualiza la variable `NOTEBOOKLM_AUTH_JSON` en Railway
4. Railway reiniciará el servicio automáticamente

---

## Troubleshooting

### El bot no responde
- Verifica que `TELEGRAM_BOT_TOKEN` sea correcto
- Revisa los logs en Railway (pestaña "Deployments" → "View Logs")

### "Unauthorized" o errores de NotebookLM
- La sesión expiró. Repite el paso 1 y actualiza `NOTEBOOKLM_AUTH_JSON`
- Verifica que `NOTEBOOKLM_NOTEBOOK_NAME` coincida exactamente con el nombre en NotebookLM

### OpenAI errors
- Verifica que `OPENAI_API_KEY` tenga saldo disponible
- Revisa que no haya rate limiting en tu cuenta
