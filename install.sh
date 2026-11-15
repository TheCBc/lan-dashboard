#!/usr/bin/env bash

# Assicurati di essere root sulla shell di Proxmox
if [ "$EUID" -ne 0 ]; then
  echo "Esegui questo script come root sulla shell del nodo Proxmox."
  exit 1
fi

set -e

echo "=== Creazione Container LXC per LAN Dashboard ==="

# 1. Configurazione di base del container
# Puoi modificare ID, nome, storage e risorse in base alle tue preferenze
CTID=200                  # ID del container (modificalo se il 200 è già occupato)
HOSTNAME="landashboard"
STORAGE="local-lvm"       # Nome del tuo storage Proxmox (es. local-lvm o zfs)
BRIDGE="vmbr0"            # Bridge di rete principale
CORES=1                   # 1 singolo core (più che sufficiente)
MEMORY=256                # 256 MB di RAM a riposo (consumo reale < 50MB)
SWAP=512
DISK_SIZE="2"             # 2 GB di disco

# 2. Download dell'ultimo template Debian disponibile sul Proxmox storage
TEMPLATE_STORAGE="local"  # Storage dove Proxmox scarica i template (di solito 'local')
echo "[1/5] Controllo e download del template Debian (latest)..."
pveam update
TEMPLATE=$(pveam available -section system | grep debian-12 | tail -n 1 | awk '{print $2}')
if [ -z "$TEMPLATE" ]; then
    TEMPLATE="debian-12-standard_12.2-1_amd64.tar.zst"
fi

if ! pveam list "$TEMPLATE_STORAGE" | grep -q "$TEMPLATE"; then
    pveam download "$TEMPLATE_STORAGE" "$TEMPLATE"
fi

# Percorso completo del template scaricato
TEMPLATE_PATH="$TEMPLATE_STORAGE:vztmpl/$TEMPLATE"

# 3. Creazione del container LXC
echo "[2/5] Creazione del container LXC ID $CTID..."
pct create $CTID "$TEMPLATE_PATH" \
    --ostype debian \
    --hostname "$HOSTNAME" \
    --cores $CORES \
    --memory $MEMORY \
    --swap $SWAP \
    --storage "$STORAGE" \
    --rootfs "$STORAGE:$DISK_SIZE" \
    --net0 name=eth0,bridge="$BRIDGE",ip=dhcp \
    --unprivileged 1 \
    --onboot 1 \
    --start 1

echo "[3/5] Attesa avvio del container..."
sleep 5

# 4. Installazione delle dipendenze e dell'applicazione all'interno del container
echo "[4/5] Configurazione dell'ambiente e installazione della dashboard nel container..."
pct exec $CTID -- bash -c "
export DEBIAN_FRONTEND=noninteractive
apt-get update -y && apt-get upgrade -y
apt-get install -y python3 python3-pip python3-venv git curl

# Clona direttamente dal tuo repository GitHub
INSTALL_DIR='/opt/lan-dashboard'
mkdir -p \$INSTALL_DIR
git clone https://github.com/TheCBc/lan-dashboard.git \$INSTALL_DIR || (cd \$INSTALL_DIR && git pull)

cd \$INSTALL_DIR
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
if [ -f 'requirements.txt' ]; then
    pip install -r requirements.txt
else
    pip install flask zeroconf requests
fi

# Configurazione del servizio Systemd
cat << 'EOF' > /etc/systemd/system/landashboard.service
[Unit]
Description=LAN Dashboard Python Flask Service
After=network.target

[Service]
User=root
WorkingDirectory=/opt/lan-dashboard
ExecStart=/opt/lan-dashboard/venv/bin/python app.py
Restart=always

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable landashboard.service
systemctl restart landashboard.service
"

# 5. Recupero dell'IP assegnato al container
CT_IP=$(pct exec $CTID -- hostname -I | awk '{print $1}')

echo "=================================================="
echo " Installazione completata con successo!"
echo " Container LXC ID: $CTID"
echo " La dashboard è attiva e raggiungibile all'indirizzo:"
echo " http://$CT_IP:5000"
echo "=================================================="
