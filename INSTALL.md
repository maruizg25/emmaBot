# SercoBot — Guía de Instalación en Servidor Linux (RHEL 9)

> Guía completa para desplegar SercoBot en producción.
> Sistema operativo: Red Hat Enterprise Linux 9.3
> Servidor: DC-VS-APP&BDD-CHATBOT-PROD

---

## Requisitos previos (pedir al equipo de TI)

- [ ] Acceso SSH al servidor (puerto 22 desde tu equipo)
- [ ] Salida a internet del servidor hacia `ollama.com`, `pypi.org`, `github.com`, `huggingface.co`
- [ ] Subdominio con SSL, ej: `sercobot.sercop.gob.ec` (HTTPS requerido por WhatsApp/Meta)
- [ ] Puerto 443 abierto hacia internet (para recibir mensajes de WhatsApp)

---

## PASO 1 — Diagnóstico del servidor

Conectarse y verificar el estado:

```bash
ssh root@IP_DEL_SERVIDOR

# RAM, CPU y disco
free -h
nproc
df -kh

# GPU (si hay)
nvidia-smi 2>/dev/null || echo "Sin GPU NVIDIA"

# Qué ya está instalado
python3 --version
psql --version
systemctl status postgresql* 2>/dev/null | grep Active
curl -s http://localhost:11434/api/tags 2>/dev/null || echo "Ollama: no instalado"
```

---

## PASO 2 — Instalar dependencias del sistema

```bash
dnf update -y

dnf install -y \
    python3.11 python3.11-pip python3.11-devel \
    git curl wget \
    gcc gcc-c++ make \
    openssl-devel libffi-devel \
    nginx

python3.11 --version   # debe mostrar 3.11.x
```

---

## PASO 3 — Configurar PostgreSQL

La partición `/var/lib/pgsql` ya está montada (330 GB).

```bash
# Inicializar base de datos (solo la primera vez)
postgresql-setup --initdb 2>/dev/null || /usr/pgsql-16/bin/postgresql-16-setup initdb

# Arrancar y habilitar al boot
systemctl enable --now postgresql-16 2>/dev/null || systemctl enable --now postgresql
systemctl status postgresql*
```

Crear usuario y base de datos:

```bash
sudo -u postgres psql << 'SQL'
CREATE USER sercop_admin WITH PASSWORD 'Admin2024!' SUPERUSER;
CREATE DATABASE sercop_db OWNER sercop_admin;
\l
SQL
```

Instalar pgvector:

```bash
# Opción A — desde repos (si está disponible)
dnf install -y pgvector_16

# Opción B — compilar desde fuente (si no está en repos)
dnf install -y git make gcc postgresql16-devel
git clone --branch v0.8.0 https://github.com/pgvector/pgvector.git /tmp/pgvector
cd /tmp/pgvector && make && make install

# Activar extensiones
sudo -u postgres psql -d sercop_db << 'SQL'
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS unaccent;

-- Función necesaria para índice GIN con unaccent
CREATE OR REPLACE FUNCTION unaccent_immutable(text)
RETURNS text AS $$ SELECT unaccent($1); $$ LANGUAGE sql IMMUTABLE;

\dx
SQL
```

Permitir conexiones locales con contraseña — editar `pg_hba.conf`:

```bash
# Encontrar el archivo
sudo -u postgres psql -c "SHOW hba_file;"

# Agregar al final de pg_hba.conf (antes de las líneas existentes de local)
# host    sercop_db    sercop_admin    127.0.0.1/32    md5

systemctl reload postgresql-16 2>/dev/null || systemctl reload postgresql
```

---

## PASO 4 — Instalar Ollama

```bash
curl -fsSL https://ollama.com/install.sh | sh
systemctl enable --now ollama
sleep 5
curl http://localhost:11434/api/tags   # debe responder JSON
```

Descargar modelos (requiere internet desde el servidor):

```bash
# Embeddings — 300 MB (rápido)
ollama pull nomic-embed-text

# LLM principal — 7 GB (10-20 min según red)
ollama pull gemma4:e2b

# LLM máxima calidad — 17 GB — solo si RAM >= 32 GB o hay GPU
# ollama pull gemma4:26b

# Verificar
ollama list
```

---

## PASO 5 — Clonar el repositorio

```bash
cd /opt
git clone https://github.com/Hainrixz/whatsapp-agentkit.git sercobot
cd /opt/sercobot

# Crear entorno virtual
python3.11 -m venv .venv
source .venv/bin/activate

# Instalar dependencias Python
pip install --upgrade pip
pip install -r requirements.txt
```

---

## PASO 6 — Configurar variables de entorno

```bash
cp /opt/sercobot/.env.example /opt/sercobot/.env
nano /opt/sercobot/.env
```

Valores a completar:

```env
META_ACCESS_TOKEN=TU_TOKEN_DE_META
META_PHONE_NUMBER_ID=TU_PHONE_ID
META_VERIFY_TOKEN=sercop-verify-prod
DATABASE_URL=postgresql+asyncpg://sercop_admin:Admin2024!@localhost:5432/sercop_db
OLLAMA_MODEL=gemma4:e2b
```

---

## PASO 7 — Restaurar la base de datos

Transferir el dump desde el equipo de desarrollo:

```bash
# Desde el Mac del desarrollador:
scp /tmp/sercop_db.dump root@IP_DEL_SERVIDOR:/tmp/

# En el servidor:
pg_restore -U sercop_admin -d sercop_db \
    --no-owner --no-acl -v /tmp/sercop_db.dump

# Verificar (debe mostrar ~3059)
psql -U sercop_admin -d sercop_db -c "SELECT COUNT(*) FROM chunks;"
```

---

## PASO 8 — Probar la aplicación

```bash
source /opt/sercobot/.venv/bin/activate
cd /opt/sercobot
uvicorn agent.main:app --host 0.0.0.0 --port 8000

# En otra terminal — probar que responde
curl http://localhost:8000/
```

---

## PASO 9 — Configurar nginx como reverse proxy con SSL

```bash
# Copiar certificado SSL institucional a:
# /etc/ssl/certs/sercobot.crt
# /etc/ssl/private/sercobot.key

cat > /etc/nginx/conf.d/sercobot.conf << 'NGINX'
server {
    listen 80;
    server_name sercobot.sercop.gob.ec;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    server_name sercobot.sercop.gob.ec;

    ssl_certificate     /etc/ssl/certs/sercobot.crt;
    ssl_certificate_key /etc/ssl/private/sercobot.key;
    ssl_protocols       TLSv1.2 TLSv1.3;

    location / {
        proxy_pass         http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_read_timeout 120s;
    }
}
NGINX

nginx -t && systemctl enable --now nginx
```

---

## PASO 10 — Servicios systemd (arranque automático)

```bash
cat > /etc/systemd/system/sercobot.service << 'SERVICE'
[Unit]
Description=SercoBot — Asistente SERCOP
After=network.target ollama.service postgresql-16.service

[Service]
Type=simple
User=root
WorkingDirectory=/opt/sercobot
EnvironmentFile=/opt/sercobot/.env
ExecStart=/opt/sercobot/.venv/bin/uvicorn agent.main:app \
    --host 127.0.0.1 --port 8000 --workers 2
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
SERVICE

systemctl daemon-reload
systemctl enable --now sercobot
systemctl status sercobot
```

---

## PASO 11 — Firewall

```bash
firewall-cmd --permanent --add-service=http
firewall-cmd --permanent --add-service=https
firewall-cmd --reload
firewall-cmd --list-all
```

---

## PASO 12 — Configurar webhook en Meta

En el panel de Meta for Developers:

- **Webhook URL:** `https://sercobot.sercop.gob.ec/webhook`
- **Verify Token:** el valor de `META_VERIFY_TOKEN` en `.env`

---

## Verificación final

```bash
# Todos los servicios corriendo
systemctl status ollama sercobot nginx postgresql-16

# Test de respuesta HTTPS
curl https://sercobot.sercop.gob.ec/

# Logs en tiempo real
journalctl -u sercobot -f
```

---

## Comandos de mantenimiento

```bash
# Ver logs
journalctl -u sercobot -f
journalctl -u sercobot --since "1 hour ago"

# Reiniciar tras cambios en .env o código
systemctl restart sercobot

# Actualizar código
cd /opt/sercobot && git pull && systemctl restart sercobot

# Agregar documentos nuevos al RAG
source /opt/sercobot/.venv/bin/activate
cd /opt/sercobot
python scripts/scraper_biblioteca.py

# Backup de la base de datos
sudo -u postgres pg_dump sercop_db -Fc -f /tmp/backup_$(date +%Y%m%d).dump
```

---

## Troubleshooting

| Síntoma | Causa probable | Solución |
|---|---|---|
| `sercobot.service` no inicia | `.env` mal configurado | `journalctl -u sercobot -n 50` |
| Ollama no responde | Servicio caído | `systemctl restart ollama` |
| Error 502 en nginx | App no corre en 8000 | `systemctl status sercobot` |
| Embeddings lentos | Modelo no cargado aún | Esperar 30s tras reinicio de Ollama |
| DB connection refused | PostgreSQL detenido | `systemctl start postgresql-16` |
