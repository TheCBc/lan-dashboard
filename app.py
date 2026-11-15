#!/usr/bin/env python3
"""
LAN Dashboard - Ultra-lightweight self-hosted dashboard for Linux LXC.
Single-file backend with Flask and Python standard libraries + zeroconf.
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
    "services": [
        {
            "id": "srv-1",
            "name": "Router / Gateway",
            "url": "http://192.168.1.1",
            "subtitle": "192.168.1.1",
            "icon": "router",
            "color": "#3b82f6",
            "new_tab": True
        },
        {
            "id": "srv-2",
            "name": "Pi-hole / DNS",
            "url": "http://192.168.1.2/admin",
            "subtitle": "AdBlocker & DNS Local",
            "icon": "shield",
            "color": "#10b981",
            "new_tab": True
        },
        {
            "id": "srv-3",
            "name": "Home Assistant",
            "url": "http://192.168.1.100:8123",
            "subtitle": "Domotica & IoT Hub",
            "icon": "home",
            "color": "#06b6d4",
            "new_tab": True
        }
    ]
}


def load_data():
    """Load configuration from links.json, creating it if it doesn't exist."""
    if not os.path.exists(DATA_FILE):
        save_data(DEFAULT_DATA)
        return DEFAULT_DATA.copy()
    try:
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            if isinstance(data, list):
                data = {"title": "LAN Dashboard", "services": data}
            if "title" not in data:
                data["title"] = "LAN Dashboard"
            if "services" not in data or not isinstance(data["services"], list):
                data["services"] = []
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
    """
    Check if a URL/host is reachable:
    1. HTTP/HTTPS GET request with unverified SSL (supports self-signed certificates).
    2. Fallback to raw TCP socket connection to the target port.
    Returns: {'online': bool, 'latency_ms': int, 'detail': str}
    """
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

    # 1. HTTP/HTTPS Check (ignores invalid / self-signed SSL certs)
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

    # 2. TCP Socket Fallback
    try:
        t_sock = time.time()
        with socket.create_connection((hostname, port), timeout=timeout):
            latency = int((time.time() - t_sock) * 1000)
            return {'online': True, 'latency_ms': latency, 'detail': f'TCP Port {port} Open'}
    except Exception as e:
        return {'online': False, 'latency_ms': None, 'detail': 'Unreachable / Timeout'}


# ==========================================
# LAN SCANNER HELPERS (mDNS + TCP Port Scan)
# ==========================================

def get_local_subnet_ips():
    """Detect local IP and return list of /24 subnet IPs."""
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
    """Listener for mDNS service discovery."""
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

    def update_service(self, zc, type_, name):
        pass

    def remove_service(self, zc, type_, name):
        pass


def probe_http_title(url):
    """Quickly grab <title> from web server if available."""
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
                if clean:
                    return clean
    except Exception:
        pass
    return None


def run_lan_scan():
    """
    Executes fast mDNS discovery and parallel subnet port scan.
    Returns deduplicated list of discovered LAN devices.
    """
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
            print(f"[WARN] Zeroconf start error: {e}")

    # Subnet Port Scan (80, 443, 8123, 8006, 8080)
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

    # Scan open ports in parallel
    with ThreadPoolExecutor(max_workers=80) as ex:
        open_ports = list(filter(None, ex.map(_scan_target, targets)))

    # Allow mDNS brief time to complete packet collection
    if HAS_ZEROCONF and zc:
        time.sleep(1.0)
        try:
            zc.close()
        except Exception:
            pass

    results = []
    seen_endpoints = set()

    # 1. Process mDNS results (highest name fidelity)
    for item in collector.found:
        ip = item['ip']
        port = item['port']
        endpoint = f"{ip}:{port}"
        if endpoint in seen_endpoints:
            continue
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

    # 2. Process TCP open ports
    for ip, port in open_ports:
        endpoint = f"{ip}:{port}"
        if endpoint in seen_endpoints:
            continue
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

        # Try HTML title
        if not name:
            name = probe_http_title(url)

        # Try reverse DNS
        if not name:
            try:
                h, _, _ = socket.gethostbyaddr(ip)
                if h:
                    name = h.split('.')[0].replace('-', ' ').title()
            except Exception:
                pass

        if not name:
            name = f"Dispositivo ({ip}:{port})"

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
    """Render the dashboard page with initial data preloaded."""
    data = load_data()
    return render_template('index.html', title=data.get('title', 'LAN Dashboard'), services=data.get('services', []))


@app.route('/api/links', methods=['GET'])
def get_links():
    """Get all configured services and dashboard title."""
    return jsonify(load_data())


@app.route('/api/links', methods=['POST'])
def add_link():
    """Add a new service card."""
    payload = request.get_json(force=True, silent=True) or {}
    name = (payload.get('name') or '').strip()
    url = (payload.get('url') or '').strip()
    
    if not name or not url:
        return jsonify({'error': 'Name and URL are required'}), 400

    data = load_data()
    new_id = f"srv-{str(uuid.uuid4())[:8]}"
    new_item = {
        "id": new_id,
        "name": name,
        "url": url,
        "subtitle": (payload.get('subtitle') or '').strip() or url,
        "icon": (payload.get('icon') or 'globe').strip(),
        "color": (payload.get('color') or '#3b82f6').strip(),
        "new_tab": bool(payload.get('new_tab', True))
    }
    data["services"].append(new_item)
    save_data(data)
    return jsonify({'success': True, 'item': new_item}), 201


@app.route('/api/links/<link_id>', methods=['PUT'])
def update_link(link_id):
    """Update an existing service card."""
    payload = request.get_json(force=True, silent=True) or {}
    data = load_data()
    item = next((s for s in data["services"] if s.get('id') == link_id), None)
    
    if not item:
        return jsonify({'error': 'Service not found'}), 404

    if 'name' in payload:
        name = payload['name'].strip()
        if name:
            item['name'] = name
    if 'url' in payload:
        url = payload['url'].strip()
        if url:
            item['url'] = url
    if 'subtitle' in payload:
        item['subtitle'] = payload['subtitle'].strip()
    if 'icon' in payload:
        item['icon'] = payload['icon'].strip()
    if 'color' in payload:
        item['color'] = payload['color'].strip()
    if 'new_tab' in payload:
        item['new_tab'] = bool(payload['new_tab'])

    save_data(data)
    return jsonify({'success': True, 'item': item})


@app.route('/api/links/<link_id>', methods=['DELETE'])
def delete_link(link_id):
    """Delete a service card."""
    data = load_data()
    initial_len = len(data["services"])
    data["services"] = [s for s in data["services"] if s.get('id') != link_id]
    
    if len(data["services"]) == initial_len:
        return jsonify({'error': 'Service not found'}), 404

    save_data(data)
    return jsonify({'success': True})


@app.route('/api/links/reorder', methods=['POST'])
def reorder_links():
    """Reorder services based on an array of IDs."""
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


@app.route('/api/title', methods=['POST'])
def update_title():
    """Update dashboard title."""
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
    """
    Check status of all configured services in parallel.
    Supports ?id=<link_id> query to check just one service.
    """
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
    """
    API endpoint: Scans LAN subnet for common open ports (80, 443, 8123, 8006, 8080)
    and discovers mDNS services via zeroconf.
    """
    devices = run_lan_scan()
    return jsonify({'success': True, 'devices': devices})


@app.route('/api/backup', methods=['GET'])
def download_backup():
    """Download links.json as backup."""
    load_data()
    return send_file(
        DATA_FILE,
        as_attachment=True,
        download_name='lan_dashboard_links.json',
        mimetype='application/json'
    )


@app.route('/api/restore', methods=['POST'])
def restore_backup():
    """Restore links.json from uploaded file or JSON payload."""
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
            new_data = {"title": "LAN Dashboard", "services": new_data}
        
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
