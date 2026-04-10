"""
Wrapper para a Evolution API — gateway WhatsApp REST.
Documentação: https://doc.evolution-api.com
"""
import os
import time
import requests
from typing import Dict, List, Optional

# ─── Configuração via variáveis de ambiente ────────────────────────────────
BASE_URL = os.environ.get('EVOLUTION_BASE_URL', 'http://localhost:8080').rstrip('/')
API_KEY  = os.environ.get('EVOLUTION_API_KEY', 'autoverifica-evo-key-2024')
INSTANCE = 'autoverifica'
TIMEOUT  = 15  # segundos


def _h() -> Dict[str, str]:
    return {'apikey': API_KEY, 'Content-Type': 'application/json'}


def _url(path: str) -> str:
    return f"{BASE_URL}{path}"


# ─── Instância ────────────────────────────────────────────────────────────

def _instance_exists() -> bool:
    """Verifica se a instância já existe na Evolution API."""
    try:
        r = requests.get(
            _url(f'/instance/fetchInstances?instanceName={INSTANCE}'),
            headers=_h(), timeout=TIMEOUT
        )
        if r.ok:
            data = r.json()
            return bool(isinstance(data, list) and data)
    except Exception:
        pass
    return False


def ensure_instance_and_get_qr() -> Dict:
    """
    Garante que a instância existe e retorna o QR code.
    Tenta múltiplas vezes caso a instância ainda esteja inicializando.
    Retorna: { 'base64': '...', 'code': '...' } ou { 'error': '...' }
    """
    # Se instância não existe, cria
    if not _instance_exists():
        try:
            r = requests.post(_url('/instance/create'), headers=_h(), json={
                'instanceName': INSTANCE,
                'qrcode': True,
                'integration': 'WHATSAPP-BAILEYS',
            }, timeout=TIMEOUT)
            if not r.ok:
                return {'error': f'Falha ao criar instância: HTTP {r.status_code} — {r.text[:300]}'}
        except Exception as e:
            return {'error': f'Erro ao criar instância: {e}'}

        # Aguarda a instância inicializar (até 8s)
        time.sleep(3)

    # Busca QR code com retentativas
    last_error = 'QR não disponível ainda'
    for attempt in range(4):
        try:
            r = requests.get(
                _url(f'/instance/connect/{INSTANCE}'),
                headers=_h(), timeout=TIMEOUT
            )
            if r.ok:
                data = r.json()
                # Evolution API v2 retorna { base64, code }
                if data.get('base64') or data.get('code'):
                    return data
                # Pode retornar { instance: { ... } } se já conectado
                if data.get('instance'):
                    status = data['instance'].get('state', '')
                    if status == 'open':
                        return {'already_connected': True}
                last_error = f'Resposta inesperada: {str(data)[:200]}'
            else:
                last_error = f'HTTP {r.status_code}: {r.text[:200]}'
        except Exception as e:
            last_error = str(e)

        if attempt < 3:
            time.sleep(2)

    return {'error': last_error}


def get_qr() -> Dict:
    """Retorna QR atual (para polling após conexão iniciada)."""
    try:
        # Primeiro verifica se já está conectado
        status = get_status()
        if status.get('connected'):
            return {'already_connected': True}

        r = requests.get(
            _url(f'/instance/connect/{INSTANCE}'),
            headers=_h(), timeout=TIMEOUT
        )
        if r.ok:
            data = r.json()
            if data.get('base64') or data.get('code'):
                return data
            # Pode estar conectado
            return {'already_connected': status.get('connected', False), 'raw': data}
        return {'error': f'HTTP {r.status_code}'}
    except requests.exceptions.ConnectionError:
        return {'error': 'Evolution API offline'}
    except Exception as e:
        return {'error': str(e)}


def get_status() -> Dict:
    """Retorna o status de conexão da instância."""
    try:
        r = requests.get(
            _url(f'/instance/fetchInstances?instanceName={INSTANCE}'),
            headers=_h(), timeout=TIMEOUT
        )
        if r.ok:
            data = r.json()
            if isinstance(data, list) and data:
                inst = data[0]
                connected = inst.get('connectionStatus') == 'open'
                return {
                    'connected': connected,
                    'status': inst.get('connectionStatus', 'unknown'),
                    'name': inst.get('profileName', ''),
                    'phone': (inst.get('ownerJid', '') or '').split('@')[0],
                    'pic': inst.get('profilePicUrl', ''),
                }
            return {'connected': False, 'status': 'no_instance'}
        return {'connected': False, 'status': f'http_{r.status_code}'}
    except requests.exceptions.ConnectionError:
        return {'connected': False, 'status': 'evolution_offline'}
    except Exception as e:
        return {'connected': False, 'status': 'error', 'error': str(e)}


def disconnect() -> bool:
    """Desconecta e remove a instância."""
    try:
        requests.delete(_url(f'/instance/logout/{INSTANCE}'),
                        headers=_h(), timeout=TIMEOUT)
        requests.delete(_url(f'/instance/delete/{INSTANCE}'),
                        headers=_h(), timeout=TIMEOUT)
        return True
    except Exception:
        return False


# ─── Contatos e Grupos ────────────────────────────────────────────────────

def get_chats(limit: int = 200) -> List[Dict]:
    """Retorna grupos e contatos recentes."""
    result = []

    # Grupos
    try:
        r = requests.get(
            _url(f'/group/fetchAllGroups/{INSTANCE}?getParticipants=false'),
            headers=_h(), timeout=TIMEOUT
        )
        if r.ok:
            for g in (r.json() or []):
                result.append({
                    'id':   g.get('id', ''),
                    'name': g.get('subject') or g.get('id', ''),
                    'type': 'grupo',
                    'pic':  g.get('pictureUrl', ''),
                })
    except Exception:
        pass

    # Contatos
    try:
        r = requests.post(
            _url(f'/chat/findContacts/{INSTANCE}'),
            headers=_h(), json={}, timeout=TIMEOUT
        )
        if r.ok:
            for c in (r.json() or [])[:100]:
                jid = c.get('id', '')
                if '@g.us' in jid:
                    continue
                name = c.get('pushName') or c.get('name') or jid.split('@')[0]
                result.append({
                    'id':   jid,
                    'name': name,
                    'type': 'contato',
                    'pic':  c.get('profilePictureUrl', ''),
                })
    except Exception:
        pass

    return result[:limit]


# ─── Envio de Mensagem ────────────────────────────────────────────────────

def send_text(to: str, text: str) -> Dict:
    """Envia mensagem de texto para número ou grupo."""
    if '@' not in to:
        to = to.replace('+', '').replace(' ', '').replace('-', '')

    try:
        r = requests.post(
            _url(f'/message/sendText/{INSTANCE}'),
            headers=_h(),
            json={'number': to, 'text': text},
            timeout=30
        )
        if r.ok:
            return {'ok': True, 'data': r.json()}
        return {'ok': False, 'error': f'HTTP {r.status_code}: {r.text[:200]}'}
    except Exception as e:
        return {'ok': False, 'error': str(e)}


def is_evolution_online() -> bool:
    """Verifica se a Evolution API está acessível."""
    try:
        r = requests.get(_url('/'), headers=_h(), timeout=5)
        return r.status_code < 500
    except Exception:
        return False
