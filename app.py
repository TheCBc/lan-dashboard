#!/usr/bin/env python3
"""
LAN Dashboard - Ultra-lightweight self-hosted dashboard for Linux LXC.
Single-file backend with Flask and Python standard libraries + zeroconf.
Supports categories, drag-and-drop ordering, and bulk multi-device import.
"""

import os
import re
import json
import time
import socket
import ssl
import uuid
import ipaddress
import urllib.request
import urllib.parse
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from flask import Flask, render_template, request, jsonify, send_file

try:
    from zeroconf import Zeroconf, ServiceBrowser
    HAS_ZEROCONF = True
except ImportError:
    HAS_ZEROCONF = False

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE_DIR, 'links.json')

DEFAULT_DATA = {
    "title": "LAN Dashboard",
    "root_order": ["cat:Infrastruttura", "cat:Domotica"],
    "categories": [
        {
            "id": "cat-1",
            "name": "Domotica",
            "icon": "home",
            "color": "#06b6d4"
        },
        {
            "id": "cat-2",
            "name": "Infrastruttura",
            "icon": "server",
            "color": "#3b82f6"
        }
    ],
    "services": [
        {
            "id": "srv-1",
            "name": "Router / Gateway",
            "url": "http://192.168.1.1",
            "subtitle": "192.168.1.1",
            "icon": "router",
            "color": "#3b82f6",
            "new_tab": True,
            "category": "Infrastruttura"
        },
        {
            "id": "srv-2",
            "name": "Pi-hole / DNS",
            "url": "http://192.168.1.2/admin",
            "subtitle": "AdBlocker & DNS Local",
            "icon": "shield",
            "color": "#10b981",
            "new_tab": True,
            "category": "Infrastruttura"
        },
        {
            "id": "srv-3",
            "name": "Home Assistant",
            "url": "http://192.168.1.100:8123",
            "subtitle": "Domotica & IoT Hub",
            "icon": "home",
            "color": "#06b6d4",
            "new_tab": True,
            "category": "Domotica"
        }
    ]
}


def load_data():
    """Load configuration from links.json with full backward compatibility."""
    if not os.path.exists(DATA_FILE):
        save_data(DEFAULT_DATA)
        return DEFAULT_DATA.copy()
    try:
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            if isinstance(data, list):
                data = {"title": "LAN Dashboard", "services": data, "categories": [], "root_order": []}
            if "title" not in data:
                data["title"] = "LAN Dashboard"
            if "services" not in data or not isinstance(data["services"], list):
                data["services"] = []
            if "categories" not in data or not isinstance(data["categories"], list):
                data["categories"] = []

            # Auto-register any category used in services if missing from categories list
            existing_cats = {c.get('name') for c in data["categories"] if c.get('name')}
            for s in data["services"]:
                c_name = (s.get('category') or '').strip()
                if c_name and c_name not in existing_cats:
                    data["categories"].append({
                        "id": f"cat-{str(uuid.uuid4())[:8]}",
                        "name": c_name,
                        "icon": "folder",
                        "color": "#8b5cf6"
                    })
                    existing_cats.add(c_name)

            # Ensure root_order exists and contains all root items and categories
            if "root_order" not in data or not isinstance(data["root_order"], list):
                data["root_order"] = []

            known_keys = set(data["root_order"])
            # Add any categories not yet in root_order
            for c in data["categories"]:
                key = f"cat:{c['name']}"
                if key not in known_keys:
                    data["root_order"].append(key)
                    known_keys.add(key)
            # Add any root services not yet in root_order
            for s in data["services"]:
                if not (s.get("category") or "").strip():
                    sid = s.get("id")
                    if sid and sid not in known_keys:
                        data["root_order"].append(sid)
                        known_keys.add(sid)

            return data
    except Exception as e:
        print(f"[WARN] Error loading {DATA_FILE}: {e}")
        return DEFAULT_DATA.copy()


def save_data(data):
    """Safely persist configuration to links.json."""
    temp_file = DATA_FILE + ".tmp"
    with open(temp_file, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(temp_file, DATA_FILE)


def check_host_health(url, timeout=1.5):
    """Check if a URL/host is reachable via HTTP or TCP socket."""
    t_start = time.time()
    url_clean = (url or "").strip()
    if not url_clean:
        return {'online': False, 'latency_ms': None, 'detail': 'Empty URL'}

    if not (url_clean.startswith('http://') or url_clean.startswith('https://')):
        test_url = 'http://' + url_clean
    else:
        test_url = url_clean

    try:
        parsed = urllib.parse.urlparse(test_url)
        hostname = parsed.hostname
        scheme = parsed.scheme.lower()
        port = parsed.port or (443 if scheme == 'https' else 80)
    except Exception as e:
        return {'online': False, 'latency_ms': None, 'detail': f'Parse error: {e}'}

    if not hostname:
        return {'online': False, 'latency_ms': None, 'detail': 'Invalid hostname'}

    # 1. HTTP/HTTPS Check
    try:
        ctx = ssl._create_unverified_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        
        req = urllib.request.Request(
            test_url,
            headers={'User-Agent': 'Mozilla/5.0 (LAN-Dashboard-Checker)'},
            method='GET'
        )
        
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            latency = int((time.time() - t_start) * 1000)
            return {'online': True, 'latency_ms': latency, 'detail': f'HTTP {resp.status}'}

    except urllib.error.HTTPError as e:
        latency = int((time.time() - t_start) * 1000)
        return {'online': True, 'latency_ms': latency, 'detail': f'HTTP {e.code}'}

    except Exception:
        pass

    # 2. Fallback TCP Socket
    try:
        t_sock = time.time()
        with socket.create_connection((hostname, port), timeout=timeout):
            latency = int((time.time() - t_sock) * 1000)
            return {'online': True, 'latency_ms': latency, 'detail': f'TCP Port {port} Open'}
    except Exception:
        return {'online': False, 'latency_ms': None, 'detail': 'Unreachable / Timeout'}


# ==========================================
# LAN SCANNER (mDNS + TCP Port Scan)
# ==========================================

def get_local_subnet_ips():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('1.1.1.1', 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = '127.0.0.1'
    finally:
        s.close()
        
    if ip == '127.0.0.1':
        return []
    try:
        net = ipaddress.IPv4Network(f'{ip}/24', strict=False)
        return [str(h) for h in net.hosts()]
    except Exception:
        return []


class MDNSServiceCollector:
    def __init__(self):
        self.found = []

    def add_service(self, zc, type_, name):
        try:
            info = zc.get_service_info(type_, name)
            if info:
                addrs = [socket.inet_ntoa(a) for a in info.addresses if len(a) == 4]
                if addrs:
                    clean_name = name.split('.')[0]
                    self.found.append({
                        'ip': addrs[0],
                        'port': info.port,
                        'name': clean_name,
                        'type': type_
                    })
        except Exception:
            pass

    def update_service(self, zc, type_, name): pass
    def remove_service(self, zc, type_, name): pass


def probe_http_title(url):
    try:
        ctx = ssl._create_unverified_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (LAN-Scanner)'})
        with urllib.request.urlopen(req, timeout=0.5, context=ctx) as r:
            content = r.read(2048).decode('utf-8', errors='ignore')
            m = re.search(r'<title>(.*?)</title>', content, re.I | re.S)
            if m:
                clean = re.sub(r'\s+', ' ', m.group(1)).strip()
                if clean: return clean
    except Exception:
        pass
    return None


def run_lan_scan():
    collector = MDNSServiceCollector()
    zc = None
    if HAS_ZEROCONF:
        try:
            zc = Zeroconf()
            services = [
                '_http._tcp.local.', '_home-assistant._tcp.local.',
                '_printer._tcp.local.', '_ipp._tcp.local.',
                '_googlecast._tcp.local.', '_ssh._tcp.local.'
            ]
            browser = ServiceBrowser(zc, services, collector)
        except Exception as e:
            print(f"[WARN] Zeroconf error: {e}")

    hosts = get_local_subnet_ips()
    ports = [80, 443, 8123, 8006, 8080]
    targets = [(h, p) for h in hosts for p in ports]

    def _scan_target(target):
        ip, port = target
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.2)
        try:
            s.connect((ip, port))
            s.close()
            return ip, port
        except Exception:
            s.close()
            return None

    with ThreadPoolExecutor(max_workers=80) as ex:
        open_ports = list(filter(None, ex.map(_scan_target, targets)))

    if HAS_ZEROCONF and zc:
        time.sleep(1.0)
        try: zc.close()
        except Exception: pass

    results = []
    seen_endpoints = set()

    for item in collector.found:
        ip = item['ip']
        port = item['port']
        endpoint = f"{ip}:{port}"
        if endpoint in seen_endpoints: continue
        seen_endpoints.add(endpoint)

        proto = 'https' if port in (443, 8006) else 'http'
        url = f"{proto}://{ip}" if port in (80, 443) else f"{proto}://{ip}:{port}"

        icon = 'globe'
        color = '#3b82f6'
        mtype = item.get('type', '')
        if 'home-assistant' in mtype or port == 8123:
            icon = 'home'
            color = '#06b6d4'
        elif 'printer' in mtype or 'ipp' in mtype:
            icon = 'box'
            color = '#10b981'
        elif 'googlecast' in mtype:
            icon = 'tv'
            color = '#ec4899'

        results.append({
            'name': item['name'],
            'url': url,
            'subtitle': f"{ip}:{port}",
            'icon': icon,
            'color': color,
            'source': 'mDNS'
        })

    for ip, port in open_ports:
        endpoint = f"{ip}:{port}"
        if endpoint in seen_endpoints: continue
        seen_endpoints.add(endpoint)

        proto = 'https' if port in (443, 8006) else 'http'
        url = f"{proto}://{ip}" if port in (80, 443) else f"{proto}://{ip}:{port}"

        icon = 'globe'
        color = '#3b82f6'
        name = None

        if port == 8123:
            name = 'Home Assistant'
            icon = 'home'
            color = '#06b6d4'
        elif port == 8006:
            name = 'Proxmox VE'
            icon = 'server'
            color = '#f59e0b'
        elif ip.endswith('.1') or ip.endswith('.254'):
            name = 'Router / Gateway'
            icon = 'router'
            color = '#3b82f6'

        if not name: name = probe_http_title(url)
        if not name:
            try:
                h, _, _ = socket.gethostbyaddr(ip)
                if h: name = h.split('.')[0].replace('-', ' ').title()
            except Exception: pass
        if not name: name = f"Dispositivo ({ip}:{port})"

        results.append({
            'name': name,
            'url': url,
            'subtitle': f"{ip}:{port}",
            'icon': icon,
            'color': color,
            'source': f"Porta {port}"
        })

    return results


# ==========================================
# FLASK ROUTES
# ==========================================

@app.route('/')
def index():
    data = load_data()
    return render_template(
        'index.html',
        title=data.get('title', 'LAN Dashboard'),
        services=data.get('services', []),
        categories=data.get('categories', []),
        root_order=data.get('root_order', [])
    )


@app.route('/api/links', methods=['GET'])
def get_links():
    return jsonify(load_data())


@app.route('/api/links', methods=['POST'])
def add_link():
    """Aggiunge un singolo servizio supportando l'assegnazione a una cartella."""
    payload = request.get_json(force=True, silent=True) or {}
    name = (payload.get('name') or '').strip()
    url = (payload.get('url') or '').strip()
    category = (payload.get('category') or '').strip() or None
    
    if not name or not url:
        return jsonify({'error': 'Name and URL are required'}), 400

    data = load_data()
    
    # Registra la categoria se nuova
    if category:
        existing_cats = {c.get('name') for c in data["categories"]}
        if category not in existing_cats:
            data["categories"].append({
                "id": f"cat-{str(uuid.uuid4())[:8]}",
                "name": category,
                "icon": "folder",
                "color": "#8b5cf6"
            })
            cat_key = f"cat:{category}"
            if cat_key not in data["root_order"]:
                data["root_order"].append(cat_key)

    new_id = f"srv-{str(uuid.uuid4())[:8]}"
    new_item = {
        "id": new_id,
        "name": name,
        "url": url,
        "subtitle": (payload.get('subtitle') or '').strip() or url,
        "icon": (payload.get('icon') or 'globe').strip(),
        "color": (payload.get('color') or '#3b82f6').strip(),
        "new_tab": bool(payload.get('new_tab', True)),
        "category": category
    }
    data["services"].append(new_item)

    # Se aggiunto alla Home (senza categoria), inseriscilo in root_order
    if not category and new_id not in data["root_order"]:
        data["root_order"].append(new_id)

    save_data(data)
    return jsonify({
        'success': True,
        'item': new_item,
        'categories': data["categories"],
        'root_order': data["root_order"]
    }), 201


@app.route('/api/links/bulk', methods=['POST'])
def add_links_bulk():
    """Importa più dispositivi in blocco con un salvataggio atomico."""
    payload = request.get_json(force=True, silent=True) or {}
    items = payload.get('items', [])
    target_category = (payload.get('category') or '').strip() or None

    if not isinstance(items, list) or not items:
        return jsonify({'error': 'No items provided'}), 400

    data = load_data()

    if target_category:
        existing_cats = {c.get('name') for c in data["categories"]}
        if target_category not in existing_cats:
            data["categories"].append({
                "id": f"cat-{str(uuid.uuid4())[:8]}",
                "name": target_category,
                "icon": "folder",
                "color": "#8b5cf6"
            })
            cat_key = f"cat:{target_category}"
            if cat_key not in data["root_order"]:
                data["root_order"].append(cat_key)

    added_items = []
    for item in items:
        name = (item.get('name') or '').strip()
        url = (item.get('url') or '').strip()
        if not name or not url:
            continue
        
        cat = (item.get('category') or '').strip() or target_category
        new_id = f"srv-{str(uuid.uuid4())[:8]}"

        new_item = {
            "id": new_id,
            "name": name,
            "url": url,
            "subtitle": (item.get('subtitle') or '').strip() or url,
            "icon": (item.get('icon') or 'globe').strip(),
            "color": (item.get('color') or '#3b82f6').strip(),
            "new_tab": bool(item.get('new_tab', True)),
            "category": cat
        }
        data["services"].append(new_item)
        added_items.append(new_item)

        if not cat and new_id not in data["root_order"]:
            data["root_order"].append(new_id)

    save_data(data)
    return jsonify({
        'success': True,
        'added_count': len(added_items),
        'items': added_items,
        'categories': data["categories"],
        'root_order': data["root_order"]
    }), 201


@app.route('/api/links/<link_id>', methods=['PUT'])
def update_link(link_id):
    """Modifica un servizio esistente (incluso cambio categoria/spostamento)."""
    payload = request.get_json(force=True, silent=True) or {}
    data = load_data()
    item = next((s for s in data["services"] if s.get('id') == link_id), None)
    
    if not item:
        return jsonify({'error': 'Service not found'}), 404

    old_category = item.get('category')

    if 'name' in payload:
        name = payload['name'].strip()
        if name: item['name'] = name
    if 'url' in payload:
        url = payload['url'].strip()
        if url: item['url'] = url
    if 'subtitle' in payload: item['subtitle'] = payload['subtitle'].strip()
    if 'icon' in payload: item['icon'] = payload['icon'].strip()
    if 'color' in payload: item['color'] = payload['color'].strip()
    if 'new_tab' in payload: item['new_tab'] = bool(payload['new_tab'])
    
    if 'category' in payload:
        cat = (payload['category'] or '').strip() or None
        item['category'] = cat

        if cat:
            existing_cats = {c.get('name') for c in data["categories"]}
            if cat not in existing_cats:
                data["categories"].append({
                    "id": f"cat-{str(uuid.uuid4())[:8]}",
                    "name": cat,
                    "icon": "folder",
                    "color": "#8b5cf6"
                })
                cat_key = f"cat:{cat}"
                if cat_key not in data["root_order"]:
                    data["root_order"].append(cat_key)
            # Se spostato dentro una cartella, toglilo da root_order
            if link_id in data["root_order"]:
                data["root_order"].remove(link_id)
        else:
            # Se spostato nella Home, assicurati sia in root_order
            if link_id not in data["root_order"]:
                data["root_order"].append(link_id)

    save_data(data)
    return jsonify({
        'success': True,
        'item': item,
        'categories': data["categories"],
        'root_order': data["root_order"]
    })


@app.route('/api/links/<link_id>', methods=['DELETE'])
def delete_link(link_id):
    data = load_data()
    initial_len = len(data["services"])
    data["services"] = [s for s in data["services"] if s.get('id') != link_id]
    
    if len(data["services"]) == initial_len:
        return jsonify({'error': 'Service not found'}), 404

    if link_id in data.get("root_order", []):
        data["root_order"].remove(link_id)

    save_data(data)
    return jsonify({'success': True, 'root_order': data["root_order"]})


@app.route('/api/layout/reorder', methods=['POST'])
def reorder_layout():
    """Salva il nuovo ordine della griglia principale (cartelle e pulsanti root)."""
    payload = request.get_json(force=True, silent=True) or {}
    order = payload.get('order', [])
    if not isinstance(order, list):
        return jsonify({'error': 'Invalid order list'}), 400

    data = load_data()
    data['root_order'] = order
    save_data(data)
    return jsonify({'success': True, 'root_order': data['root_order']})


@app.route('/api/links/reorder', methods=['POST'])
def reorder_links():
    """Riordina l'array generale dei servizi."""
    payload = request.get_json(force=True, silent=True) or {}
    order = payload.get('order', [])
    if not isinstance(order, list):
        return jsonify({'error': 'Invalid order list'}), 400

    data = load_data()
    services_map = {s.get('id'): s for s in data["services"]}
    
    reordered = []
    for item_id in order:
        if item_id in services_map:
            reordered.append(services_map.pop(item_id))
    
    reordered.extend(services_map.values())
    data["services"] = reordered
    save_data(data)
    return jsonify({'success': True, 'services': data["services"]})


# ==========================================
# CATEGORIES API
# ==========================================

@app.route('/api/categories', methods=['GET'])
def get_categories():
    data = load_data()
    return jsonify({'categories': data.get('categories', [])})


@app.route('/api/categories', methods=['POST'])
def create_or_update_category():
    payload = request.get_json(force=True, silent=True) or {}
    name = (payload.get('name') or '').strip()
    old_name = (payload.get('old_name') or '').strip()
    icon = (payload.get('icon') or 'folder').strip()
    color = (payload.get('color') or '#8b5cf6').strip()

    if not name:
        return jsonify({'error': 'Category name is required'}), 400

    data = load_data()
    
    if old_name:
        for c in data["categories"]:
            if c.get('name') == old_name:
                c['name'] = name
                c['icon'] = icon
                c['color'] = color
        for s in data["services"]:
            if s.get('category') == old_name:
                s['category'] = name
        # Aggiorna anche in root_order
        old_key = f"cat:{old_name}"
        new_key = f"cat:{name}"
        if old_key in data["root_order"]:
            idx = data["root_order"].index(old_key)
            data["root_order"][idx] = new_key
    else:
        existing = next((c for c in data["categories"] if c.get('name') == name), None)
        if existing:
            existing['icon'] = icon
            existing['color'] = color
        else:
            new_cat = {
                "id": f"cat-{str(uuid.uuid4())[:8]}",
                "name": name,
                "icon": icon,
                "color": color
            }
            data["categories"].append(new_cat)
            cat_key = f"cat:{name}"
            if cat_key not in data["root_order"]:
                data["root_order"].append(cat_key)

    save_data(data)
    return jsonify({
        'success': True,
        'categories': data["categories"],
        'root_order': data["root_order"]
    })


@app.route('/api/categories/<path:cat_name>', methods=['DELETE'])
def delete_category(cat_name):
    """Elimina una cartella e riporta i servizi associati nella Home."""
    data = load_data()
    cat_name = cat_name.strip()
    
    data["categories"] = [c for c in data["categories"] if c.get('name') != cat_name]
    cat_key = f"cat:{cat_name}"
    if cat_key in data["root_order"]:
        data["root_order"].remove(cat_key)

    for s in data["services"]:
        if s.get('category') == cat_name:
            s['category'] = None
            sid = s.get('id')
            if sid and sid not in data["root_order"]:
                data["root_order"].append(sid)

    save_data(data)
    return jsonify({
        'success': True,
        'categories': data["categories"],
        'root_order': data["root_order"]
    })


@app.route('/api/title', methods=['POST'])
def update_title():
    payload = request.get_json(force=True, silent=True) or {}
    new_title = (payload.get('title') or '').strip()
    if not new_title:
        return jsonify({'error': 'Title cannot be empty'}), 400

    data = load_data()
    data['title'] = new_title
    save_data(data)
    return jsonify({'success': True, 'title': new_title})


@app.route('/api/status', methods=['GET'])
def check_status():
    target_id = request.args.get('id')
    data = load_data()
    services = data.get("services", [])

    if target_id:
        services = [s for s in services if s.get('id') == target_id]

    if not services:
        return jsonify({'results': {}})

    results = {}

    def _worker(svc):
        sid = svc.get('id')
        surl = svc.get('url')
        health = check_host_health(surl)
        return sid, health

    with ThreadPoolExecutor(max_workers=min(16, len(services))) as executor:
        futures = [executor.submit(_worker, s) for s in services]
        for f in futures:
            try:
                sid, health = f.result()
                results[sid] = health
            except Exception:
                pass

    return jsonify({'results': results})


@app.route('/api/scan', methods=['GET', 'POST'])
def scan_network():
    devices = run_lan_scan()
    return jsonify({'success': True, 'devices': devices})


@app.route('/api/backup', methods=['GET'])
def download_backup():
    load_data()
    return send_file(
        DATA_FILE,
        as_attachment=True,
        download_name='lan_dashboard_links.json',
        mimetype='application/json'
    )


@app.route('/api/restore', methods=['POST'])
def restore_backup():
    try:
        new_data = None
        if 'file' in request.files:
            file = request.files['file']
            new_data = json.load(file.stream)
        else:
            new_data = request.get_json(force=True, silent=True)

        if not new_data:
            return jsonify({'error': 'No valid JSON provided'}), 400

        if isinstance(new_data, list):
            new_data = {"title": "LAN Dashboard", "services": new_data, "categories": [], "root_order": []}
        
        if "services" not in new_data or not isinstance(new_data["services"], list):
            return jsonify({'error': 'JSON must contain a "services" list'}), 400

        for s in new_data["services"]:
            if 'id' not in s:
                s['id'] = f"srv-{str(uuid.uuid4())[:8]}"
            if 'name' not in s or 'url' not in s:
                return jsonify({'error': 'Each service must have name and url'}), 400

        save_data(new_data)
        return jsonify({'success': True, 'data': new_data})
    except Exception as e:
        return jsonify({'error': f'Failed to restore backup: {str(e)}'}), 400


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
