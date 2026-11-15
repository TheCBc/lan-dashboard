#!/usr/bin/env bash

# Assicurati che lo script venga eseguito come root
if [ "$EUID" -ne 0 ]; then
  echo "Esegui questo script con i privilegi di root (sudo)."
  exit 1
fi

echo "=== Installazione Dashboard LAN in corso ==="

# 1. Aggiornamento e installazione dipendenze di sistema (incluso python3-pip e python3-venv)
echo "[1/4] Aggiornamento pacchetti e installazione dipendenze..."
apt-get update -y && apt-get upgrade -y
apt-get install -y python3 python3-pip python3-venv git curl

# 2. Creazione della cartella di installazione
INSTALL_DIR="/opt/lan-dashboard"
echo "[2/4] Configurazione della cartella in $INSTALL_DIR..."
mkdir -p "$INSTALL_DIR"

# Sostituisci questo URL con il link raw o il clone del tuo repository GitHub
# (Se il repo è pubblico, puoi clonarlo direttamente)
if [ -d "$INSTALL_DIR/.git" ]; then
    cd "$INSTALL_DIR" && git pull
else
    # INSERISci QUI IL LINK AL TUO REPOSITORY GITHUB
    git clone https://github.com/TheCBc/lan-dashboard.git "$INSTALL_DIR"
fi

cd "$INSTALL_DIR"

# 3. Configurazione dell'ambiente virtuale Python e installazione librerie
echo "[3/4] Configurazione ambiente Python e librerie..."
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
if [ -f "requirements.txt" ]; then
    pip install -r requirements.txt
else
    pip install flask zeroconf requests
fi

# 4. Creazione del servizio Systemd per l'avvio automatico
echo "[4/4] Creazione del servizio Systemd..."
cat << EOF > /etc/systemd/system/landashboard.service
[Unit]
Description=LAN Dashboard Python Flask Service
After=network.target

[Service]
User=root
WorkingDirectory=$INSTALL_DIR
ExecStart=$INSTALL_DIR/venv/bin/python app.py
Restart=always

[Install]
WantedBy=multi-user.target
EOF

# Ricarica systemd e avvia il servizio
systemctl daemon-reload
systemctl enable landashboard.service
systemctl restart landashboard.service

# Ottieni l'IP del container per mostrarlo alla fine
IP_ADDR=$(hostname -I | awk '{print $1}')

echo "=================================================="
echo " Installazione completata con successo!"
echo " La dashboard è attiva e raggiungibile all'indirizzo:"
echo " http://$IP_ADDR:5000"
echo "=================================================="
