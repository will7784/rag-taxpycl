# Deploy continuo Taxpy (Hostinger + GitHub)

## 1) Respuesta corta a tu duda

Si el bot corre en tu PC y apagas el equipo, se cae.
Para que quede 24/7, debes correrlo en un servidor (Hostinger VPS).

## 2) Arquitectura recomendada (MVP)

- VPS Hostinger (Ubuntu)
- Bot Telegram con `long polling` como servicio systemd (`taxpy-telegram`)
- Código en GitHub
- Auto deploy al hacer push a `main` usando GitHub Actions + SSH

## 3) Setup inicial en VPS (una sola vez)

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip
sudo mkdir -p /opt/taxpy
cd /opt/taxpy
git clone <URL_DE_TU_REPO_GITHUB> rag-documentos
cd rag-documentos
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp env.example .env
```

Editar `.env` con tus valores reales:

- `OPENAI_API_KEY`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_FREE_QUERIES_PER_MONTH=10`
- `TELEGRAM_PRO_PLAN_PRICE_USD=27`
- opcional beta cerrada:
  - `TELEGRAM_REQUIRE_INVITE=true`
  - `TELEGRAM_INVITE_CODES=abogados2026,taxpybeta`

Instalar servicio:

```bash
chmod +x scripts/install_systemd_taxpy.sh
sudo APP_DIR=/opt/taxpy/rag-documentos RUN_USER=<TU_USUARIO_VPS> ./scripts/install_systemd_taxpy.sh
```

Ver logs:

```bash
sudo journalctl -u taxpy-telegram -f
```

## 4) Deploy automático desde GitHub

Ya quedó creado el workflow:

- `.github/workflows/deploy-hostinger.yml`

Debes configurar estos Secrets en GitHub:

- `HOSTINGER_HOST` (IP o dominio del VPS)
- `HOSTINGER_PORT` (normalmente `22`)
- `HOSTINGER_USER` (usuario SSH)
- `HOSTINGER_SSH_KEY` (llave privada SSH)
- `HOSTINGER_APP_DIR` (ej: `/opt/taxpy/rag-documentos`)

También quedó creado el script remoto:

- `scripts/deploy_hostinger.sh`

Cuando hagas push a `main`, GitHub Actions:
- entra por SSH al VPS,
- hace `git pull`,
- instala dependencias,
- valida sintaxis,
- reinicia el servicio `taxpy-telegram`.

## 5) Subdominio `ragtelegram.taxpy.cl`

Para el bot por long polling, **no es obligatorio** subdominio.
Igual puedes crearlo para ordenar infraestructura:

- Crea un registro `A`:
  - `ragtelegram.taxpy.cl` -> `IP_DEL_VPS`

Te sirve para futuros endpoints web (panel, pagos, webhook Mercado Pago), aunque el bot Telegram MVP no lo necesita.

## 6) Flujo diario recomendado

1. Desarrollas local.
2. Pruebas local.
3. `git add/commit/push main`.
4. GitHub Actions despliega y reinicia en Hostinger.
5. Validas con `/saldo` en Telegram y `journalctl`.
