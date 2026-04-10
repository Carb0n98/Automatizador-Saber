"""
Wrapper para a Evolution API — gateway WhatsApp REST.
Documentação: https://doc.evolution-api.com
"""
import os
import requests
from typing import Dict, List, Any

# ─── Configuração via variáveis de ambiente ────────────────────────────────
BASE_URL   = os.environ.get('EVOLUTION_BASE_URL', 'http://localhost:8080').rstrip('/')
API_KEY    = os.environ.get('EVOLUTION_API_KEY', 'autoverifica-evo-key-2024')
INSTANCE   = 'autoverifica'
TIMEOUT    = 15  # segundos


def _h() -> Dict[str, str]:
    return {'apikey': API_KEY, 'Content-Type': 'application/json'}


def _url(path: str) -> str:
    return f"{BASE_URL}{path}"


# ─── Instância ────────────────────────────────────────────────────────────

def ensure_instance() -> Dict:
    """Cria a instância se não existir, ou retorna a existente."""
    try:
        # Tenta buscar instância existente
        r = requests.get(_url(f'/instance/fetchInstances?instanceName={INSTANCE}'),
                         headers=_h(), timeout=TIMEOUT)
        if r.ok:
            data = r.json()
            if isinstance(data, list) and data:
                return {'criada': False, 'instancia': data[0]}
    except Exception:
        pass

    # Cria nova instância
    r = requests.post(_url('/instance/create'), headers=_h(), json={
        'instanceName': INSTANCE,
        'qrcode': True,
        'integration': 'WHATSAPP-BAILEYS',
    }, timeout=TIMEOUT)
    return {'criada': True, 'instancia': r.json()}


def get_qr() -> Dict:
    """Retorna o QR code atual (base64) para conexão."""
    r = requests.get(_url(f'/instance/connect/{INSTANCE}'),
                     headers=_h(), timeout=TIMEOUT)
    if not r.ok:
        return {'error': f'HTTP {r.status_code}', 'base64': None, 'code': None}
    data = r.json()
    # Evolution API v2 retorna { base64, code } ou { pairingCode }
    return data


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
                return {
                    'connected': inst.get('connectionStatus') == 'open',
                    'status': inst.get('connectionStatus', 'unknown'),
                    'name': inst.get('profileName', ''),
                    'phone': inst.get('ownerJid', '').split('@')[0] if inst.get('ownerJid') else '',
                    'pic': inst.get('profilePicUrl', ''),
                }
        return {'connected': False, 'status': 'not_found'}
    except requests.exceptions.ConnectionError:
        return {'connected': False, 'status': 'evolution_offline'}
    except Exception as e:
        return {'connected': False, 'status': 'error', 'error': str(e)}


def disconnect() -> bool:
    """Desconecta o WhatsApp e remove a instância."""
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
    """
    Retorna lista de chats (grupos e contatos recentes).
    Formato: [{ id, name, type ('group'|'contact') }, ...]
    """
    result = []

    # Grupos
    try:
        r = requests.get(_url(f'/group/fetchAllGroups/{INSTANCE}?getParticipants=false'),
                         headers=_h(), timeout=TIMEOUT)
        if r.ok:
            for g in (r.json() or []):
                result.append({
                    'id':   g.get('id', ''),
                    'name': g.get('subject', g.get('id', '')),
                    'type': 'grupo',
                    'pic':  g.get('pictureUrl', ''),
                })
    except Exception:
        pass

    # Contatos
    try:
        r = requests.post(_url(f'/chat/findContacts/{INSTANCE}'),
                          headers=_h(), json={}, timeout=TIMEOUT)
        if r.ok:
            for c in (r.json() or [])[:100]:
                jid = c.get('id', '')
                if '@g.us' in jid:   # grupos já listados
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
    """
    Envia mensagem de texto.
    `to` pode ser um JID de grupo (ex: 12345@g.us) ou número (ex: 5584999999999).
    """
    # Normaliza número de telefone
    if '@' not in to:
        to = to.replace('+', '').replace(' ', '').replace('-', '')
        if not to.endswith('@s.whatsapp.net'):
            to = to  # Evolution API aceita número puro

    r = requests.post(_url(f'/message/sendText/{INSTANCE}'),
                      headers=_h(),
                      json={'number': to, 'text': text},
                      timeout=30)
    if r.ok:
        return {'ok': True, 'data': r.json()}
    return {'ok': False, 'error': f'HTTP {r.status_code}: {r.text[:200]}'}


def is_evolution_online() -> bool:
    """Verifica se a Evolution API está acessível."""
    try:
        r = requests.get(_url('/'), headers=_h(), timeout=5)
        return r.status_code < 500
    except Exception:
        return False
