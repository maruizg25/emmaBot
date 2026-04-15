# DEPLOY.md — Guía completa de instalación SercoBot en servidor Linux
# Para alguien que nunca ha trabajado en Linux

> Sigue cada paso en orden. No saltes pasos.
> Ante cualquier error, copia el mensaje y consulta con el desarrollador.

---

## ANTES DE EMPEZAR — Lo que necesitas tener listo

Antes de tocar el servidor, confirma que tienes:

- [ ] La **IP del servidor** (ej: 192.168.1.100)
- [ ] Usuario: `root` y la **contraseña** del servidor
- [ ] Un **subdominio con SSL** asignado (ej: `sercobot.sercop.gob.ec`) — pedirlo al equipo de TI
- [ ] Tu **token de Meta** (META_ACCESS_TOKEN) y **Phone Number ID**
- [ ] Acceso SSH habilitado desde tu equipo al servidor (pedirlo a TI)

---

## PARTE 1 — CONECTARSE AL SERVIDOR DESDE TU MAC

### Opción A — Terminal simple (recomendada para empezar)

1. Abre la aplicación **Terminal** en tu Mac
   - Busca "Terminal" en Spotlight (Cmd + Espacio)

2. Escribe este comando (reemplaza la IP):
   ```bash
   ssh root@IP_DEL_SERVIDOR
   ```
   Ejemplo: `ssh root@192.168.1.100`

3. Te pregunta: `Are you sure you want to continue connecting? (yes/no)`
   → Escribe `yes` y presiona Enter

4. Te pide la contraseña del servidor → escríbela y presiona Enter
   *(Al escribir la contraseña no verás nada en pantalla — es normal)*

5. Si ves algo como `[root@dc-vs-appbdd-chatbot ~]#` → **estás dentro del servidor**

---

### Opción B — VS Code con Remote SSH (más cómodo, igual que Windows)

1. Abre VS Code en tu Mac

2. Instala la extensión **Remote - SSH**:
   - Clic en el ícono de extensiones (cuadraditos izquierda)
   - Busca: `Remote - SSH`
   - Instala la de Microsoft

3. Presiona `F1` → escribe `Remote-SSH: Connect to Host` → Enter

4. Escribe: `root@IP_DEL_SERVIDOR` → Enter

5. Ingresa la contraseña cuando la pida

6. Ya estás dentro del servidor con VS Code — puedes abrir archivos, editar y usar la terminal integrada exactamente igual que en Windows

---

## PARTE 2 — PREPARAR EL SERVIDOR

> Todos los comandos siguientes se ejecutan **dentro del servidor** (en la terminal SSH o en VS Code conectado al servidor)

### PASO 1 — Ver el estado actual del servidor

Copia y pega este bloque completo:

```bash
echo "=== SISTEMA ===" && cat /etc/redhat-release
echo "=== MEMORIA ===" && free -h
echo "=== DISCO ===" && df -kh
echo "=== PYTHON ===" && python3 --version 2>/dev/null || echo "No instalado"
echo "=== POSTGRESQL ===" && psql --version 2>/dev/null || echo "No instalado"
echo "=== GIT ===" && git --version 2>/dev/null || echo "No instalado"
echo "=== OLLAMA ===" && curl -s http://localhost:11434/api/tags 2>/dev/null | head -1 || echo "No instalado"
```

Guarda o fotografía el resultado — lo necesitarás para el siguiente paso.

---

### PASO 2 — Instalar herramientas básicas

```bash
dnf update -y
dnf install -y python3.11 python3.11-pip python3.11-devel \
    git curl wget gcc gcc-c++ make \
    openssl-devel libffi-devel nginx
```

Espera que termine (puede tardar 3-5 minutos). Al finalizar verifica:

```bash
python3.11 --version
git --version
```

Ambos deben mostrar un número de versión.

---

### PASO 3 — Configurar PostgreSQL

La partición `/var/lib/pgsql` ya está lista en el servidor (330 GB).

**Inicializar la base de datos:**

```bash
postgresql-setup --initdb
```

Si ese comando falla, prueba:
```bash
/usr/pgsql-16/bin/postgresql-16-setup initdb
```

**Arrancar PostgreSQL y que inicie automáticamente:**

```bash
systemctl enable --now postgresql-16
```

Si falla, prueba sin el `-16`:
```bash
systemctl enable --now postgresql
```

**Verificar que está corriendo:**

```bash
systemctl status postgresql-16
```

Debe decir `Active: active (running)` en verde.

**Crear el usuario y base de datos para SercoBot:**

```bash
sudo -u postgres psql << 'SQL'
CREATE USER sercop_admin WITH PASSWORD 'Admin2024!' SUPERUSER;
CREATE DATABASE sercop_db OWNER sercop_admin;
\q
SQL
```

**Instalar la extensión pgvector** (búsqueda semántica):

```bash
dnf install -y pgvector_16
```

Si ese comando falla con "package not found", usa esto:

```bash
dnf install -y git make gcc postgresql16-devel
git clone --branch v0.8.0 https://github.com/pgvector/pgvector.git /tmp/pgvector
cd /tmp/pgvector
make
make install
cd ~
```

**Activar extensiones en la base de datos:**

```bash
sudo -u postgres psql -d sercop_db << 'SQL'
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS unaccent;
CREATE OR REPLACE FUNCTION unaccent_immutable(text)
RETURNS text AS $$ SELECT unaccent($1); $$ LANGUAGE sql IMMUTABLE;
SELECT extname FROM pg_extension;
SQL
```

Debes ver `vector` y `unaccent` en la lista.

**Permitir conexión local con contraseña:**

Primero, encontrar el archivo de configuración:

```bash
sudo -u postgres psql -c "SHOW hba_file;"
```

Abre ese archivo (normalmente `/var/lib/pgsql/data/pg_hba.conf`):

```bash
nano /var/lib/pgsql/data/pg_hba.conf
```

Agrega esta línea al **inicio** del archivo (antes de las otras líneas que empiezan con `host`):

```
host    sercop_db    sercop_admin    127.0.0.1/32    md5
```

Guarda con `Ctrl+O`, Enter, `Ctrl+X`.

Recarga PostgreSQL:

```bash
systemctl reload postgresql-16
```

**Verificar conexión:**

```bash
psql -U sercop_admin -h 127.0.0.1 -d sercop_db -c "SELECT 1;"
```

Te pide contraseña: `Admin2024!`
Debe mostrar `1` como resultado.

---

### PASO 4 — Instalar Ollama (el motor de IA)

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

Habilitar como servicio permanente:

```bash
systemctl enable --now ollama
sleep 5
curl http://localhost:11434/api/tags
```

Debe responder con un JSON (aunque esté vacío `{}`).

**Descargar los modelos de IA:**

> ⚠️ Esto requiere que el servidor tenga acceso a internet.
> Si no tiene acceso, ver la sección "Instalación sin internet" al final.

```bash
# Modelo de embeddings — 300 MB
ollama pull nomic-embed-text

# Modelo principal gemma4:e2b — 7 GB (tarda 10-30 min según la red)
ollama pull gemma4:e2b

# Verificar que están descargados
ollama list
```

Debes ver `nomic-embed-text` y `gemma4:e2b` en la lista.

---

### PASO 5 — Clonar el código de SercoBot

```bash
cd /opt
git clone https://github.com/maruizg25/emmaBot.git sercobot
cd /opt/sercobot
```

Crear entorno Python e instalar dependencias:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

Esto tarda 3-5 minutos. Al terminar no debe mostrar errores en rojo.

---

### PASO 6 — Configurar las variables del sistema

```bash
cp /opt/sercobot/.env.example /opt/sercobot/.env
nano /opt/sercobot/.env
```

Se abre un editor de texto. Modifica estos valores:

```
META_ACCESS_TOKEN=PEGA_TU_TOKEN_AQUI
META_PHONE_NUMBER_ID=PEGA_TU_PHONE_ID_AQUI
META_VERIFY_TOKEN=sercop-verify-prod
DATABASE_URL=postgresql+asyncpg://sercop_admin:Admin2024!@127.0.0.1:5432/sercop_db
OLLAMA_MODEL=gemma4:e2b
```

Guarda con `Ctrl+O`, Enter, `Ctrl+X`.

---

### PASO 7 — Restaurar la base de datos (los 3,059 chunks)

**Desde tu Mac**, abre una terminal NUEVA (sin cerrar la del servidor):

```bash
# Exportar desde Multipass
multipass exec pg-db -- sudo -u postgres pg_dump sercop_db \
    --no-owner --no-acl -Fc -f /tmp/sercop_db.dump

multipass transfer pg-db:/tmp/sercop_db.dump /tmp/sercop_db.dump

# Subir al servidor (reemplaza la IP)
scp /tmp/sercop_db.dump root@IP_DEL_SERVIDOR:/tmp/
```

**De vuelta en la terminal del servidor:**

```bash
pg_restore -U sercop_admin -h 127.0.0.1 -d sercop_db \
    --no-owner --no-acl /tmp/sercop_db.dump

# Verificar — debe mostrar 3059
psql -U sercop_admin -h 127.0.0.1 -d sercop_db \
    -c "SELECT COUNT(*) FROM chunks;"
```

---

### PASO 8 — Probar que SercoBot responde

```bash
source /opt/sercobot/.venv/bin/activate
cd /opt/sercobot
uvicorn agent.main:app --host 0.0.0.0 --port 8000
```

Abre otra terminal y prueba:

```bash
curl http://localhost:8000/
```

Si responde cualquier cosa (incluso un error JSON) → el servidor está corriendo.

Para detenerlo: `Ctrl+C`

---

### PASO 9 — Configurar nginx con SSL

> Necesitas tener el subdominio y el certificado SSL listos.
> Pídele al equipo de TI: el archivo `.crt` y el archivo `.key` del certificado.

Copia los archivos del certificado al servidor:

```bash
# Desde tu Mac (te los habrá dado TI)
scp certificado.crt root@IP_DEL_SERVIDOR:/etc/ssl/certs/sercobot.crt
scp certificado.key root@IP_DEL_SERVIDOR:/etc/ssl/private/sercobot.key
```

Crear la configuración de nginx:

```bash
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

nginx -t
```

Si dice `syntax is ok` y `test is successful`:

```bash
systemctl enable --now nginx
```

---

### PASO 10 — Hacer que SercoBot arranque automáticamente

```bash
cat > /etc/systemd/system/sercobot.service << 'EOF'
[Unit]
Description=SercoBot Asistente SERCOP
After=network.target ollama.service postgresql-16.service

[Service]
Type=simple
User=root
WorkingDirectory=/opt/sercobot
EnvironmentFile=/opt/sercobot/.env
ExecStart=/opt/sercobot/.venv/bin/uvicorn agent.main:app --host 127.0.0.1 --port 8000 --workers 2
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now sara
systemctl status sara
```

Debe decir `Active: active (running)`.

---

### PASO 11 — Abrir el firewall

```bash
firewall-cmd --permanent --add-service=http
firewall-cmd --permanent --add-service=https
firewall-cmd --reload
```

---

### PASO 12 — Verificación final

```bash
# Ver todos los servicios activos
systemctl status sara
systemctl status nginx
systemctl status ollama
systemctl status postgresql-16

# Probar HTTPS (reemplaza con tu dominio)
curl https://sercobot.sercop.gob.ec/
```

---

### PASO 13 — Configurar el webhook en Meta

En el panel de Meta for Developers → WhatsApp → Configuración:

- **Webhook URL:** `https://sercobot.sercop.gob.ec/webhook`
- **Verify Token:** `sercop-verify-prod`

---

## COMANDOS DE USO DIARIO

```bash
# Ver qué está pasando en tiempo real
journalctl -u sara -f

# Reiniciar SercoBot (tras cambios)
systemctl restart sara

# Actualizar el código
cd /opt/sercobot
git pull
systemctl restart sara

# Ver los últimos errores
journalctl -u sara -n 50 --no-pager
```

---

## SI ALGO FALLA — Guía de errores comunes

| Qué ves | Qué significa | Qué hacer |
|---|---|---|
| `Connection refused` al hacer SSH | SSH no habilitado | Pedir a TI que habiliten el puerto 22 |
| `sercobot.service` en rojo | La app no arranca | Correr `journalctl -u sara -n 30` y mandar el error al desarrollador |
| `502 Bad Gateway` en nginx | SercoBot no está corriendo | `systemctl restart sara` |
| Ollama no responde | Servicio caído | `systemctl restart ollama` y esperar 30 segundos |
| Error de base de datos | PostgreSQL detenido o mal configurado | `systemctl status postgresql-16` |
| `curl: SSL certificate problem` | Certificado mal instalado | Verificar rutas en `/etc/nginx/conf.d/sercobot.conf` |

---

## INSTALACIÓN SIN INTERNET (si el servidor está bloqueado)

Si el servidor no tiene salida a internet, hay que transferir todo desde tu Mac.

**Desde tu Mac — descargar los modelos de Ollama:**

```bash
# Instalar Ollama en tu Mac si no lo tienes
brew install ollama

# Los modelos ya están descargados en tu Mac
# Copiarlos al servidor
scp -r ~/.ollama/models root@IP_DEL_SERVIDOR:/usr/share/ollama/.ollama/
```

**Desde tu Mac — empaquetar las dependencias Python:**

```bash
cd /Users/mauricioruiz/emmabot/whatsapp-agentkit
source /Users/mauricioruiz/emmabot/.venv/bin/activate
pip download -r requirements.txt -d /tmp/sercobot_wheels/
tar czf /tmp/sercobot_wheels.tar.gz -C /tmp sercobot_wheels/
scp /tmp/sercobot_wheels.tar.gz root@IP_DEL_SERVIDOR:/tmp/
```

**En el servidor — instalar sin internet:**

```bash
cd /opt/sercobot
python3.11 -m venv .venv
source .venv/bin/activate
tar xzf /tmp/sercobot_wheels.tar.gz -C /tmp/
pip install --no-index --find-links=/tmp/sercobot_wheels -r requirements.txt
```

---

## RESUMEN DE PUERTOS Y SERVICIOS

| Servicio | Puerto | Para qué |
|---|---|---|
| SercoBot (FastAPI) | 8000 interno | La aplicación — solo accesible desde nginx |
| nginx | 443 externo | HTTPS público — recibe mensajes de WhatsApp |
| PostgreSQL | 5432 interno | Base de datos — solo accesible localmente |
| Ollama | 11434 interno | Motor de IA — solo accesible localmente |
| SSH | 22 | Administración remota |
