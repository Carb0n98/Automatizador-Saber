"""
Wrapper para a Evolution API — gateway WhatsApp REST.
Documentação: https://doc.evolution-api.com
"""
import os
import time
import requests
from typing import Dict, List

# ─── Configuração via variáveis de ambiente ────────────────────────────────
BASE_URL = os.environ.get('EVOLUTION_BASE_URL', 'http://localhost:8080').rstrip('/')
API_KEY  = os.environ.get('EVOLUTION_API_KEY', 'autoverifica-evo-key-2024')
INSTANCE = 'autoverifica'
TIMEOUT  = 15


def _h() -> Dict[str, str]:
    return {'apikey': API_KEY, 'Content-Type': 'application/json'}


def _url(path: str) -> str:
    return f"{BASE_URL}{path}"


def _extract_qr(data: dict) -> Dict:
    """
    Tenta extrair o QR code de diferentes formatos de resposta da Evolution API v2.
    Retorna { base64, code } se encontrado, ou {} se não.
    """
    # Formato 1: { base64: '...', code: '...' }  (GET /instance/connect)
    if data.get('base64'):
        return {'base64': data['base64'], 'code': data.get('code', '')}

    # Formato 2: { qrcode: { base64: '...', code: '...' } }  (POST /instance/create)
    qr = data.get('qrcode') or {}
    if qr.get('base64'):
        return {'base64': qr['base64'], 'code': qr.get('code', '')}

    # Formato 3: { instance: { qrcode: { base64: '...' } } }
    inst = data.get('instance') or {}
    qr2 = inst.get('qrcode') or {}
    if qr2.get('base64'):
        return {'base64': qr2['base64'], 'code': qr2.get('code', '')}

    return {}


# ─── Instância ────────────────────────────────────────────────────────────

def _instance_exists() -> bool:
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


def _create_instance() -> Dict:
    """Cria uma nova instância e retorna resposta completa."""
    r = requests.post(_url('/instance/create'), headers=_h(), json={
        'instanceName': INSTANCE,
        'qrcode': True,
        'integration': 'WHATSAPP-BAILEYS',
    }, timeout=TIMEOUT)
    if not r.ok:
        return {'error': f'HTTP {r.status_code}: {r.text[:300]}'}
    return r.json()


def _get_connect_qr() -> Dict:
    """Chama GET /instance/connect para buscar QR de instância existente."""
    try:
        r = requests.get(
            _url(f'/instance/connect/{INSTANCE}'),
            headers=_h(), timeout=TIMEOUT
        )
        if r.ok:
            return r.json()
    except Exception:
        pass
    return {}


def ensure_instance_and_get_qr() -> Dict:
    """
    Garante que a instância existe e retorna o QR code.
    Estratégia:
    1. Se já existe → busca QR com GET /instance/connect
    2. Se GET não tiver QR → recria a instância (delete + create)
    3. QR vem no corpo do POST /instance/create (campo 'qrcode')
    4. Se ainda sem QR → polling por até 10s
    """
    if _instance_exists():
        # Tenta get QR para reconexão
        data = _get_connect_qr()
        qr = _extract_qr(data)
        if qr:
            return qr
        # GET não gerou QR — deleta para recriar
        print(f'[WA] GET /connect retornou sem QR ({data}), recriando instância...')
        disconnect()
        time.sleep(2)

    # Cria instância nova
    print('[WA] Criando instância...')
    data = _create_instance()
    if data.get('error'):
        return data

    # QR pode estar direto na resposta do create
    qr = _extract_qr(data)
    if qr:
        print('[WA] QR obtido da resposta do create. OK.')
        return qr

    # Caso raro: QR não veio no create, aguarda e tenta connect
    print('[WA] QR não veio no create. Aguardando inicialização...')
    time.sleep(3)
    for attempt in range(5):
        data = _get_connect_qr()
        qr = _extract_qr(data)
        if qr:
            print(f'[WA] QR obtido via GET /connect na tentativa {attempt + 1}.')
            return qr
        print(f'[WA] Tentativa {attempt + 1} sem QR: {data}')
        time.sleep(2)

    return {'error': f'QR não disponível após várias tentativas. Última resposta: {data}'}


def get_qr() -> Dict:
    """Polling: retorna QR atual ou sinaliza se conectado."""
    try:
        st = get_status()
        if st.get('connected'):
            return {'already_connected': True}
        data = _get_connect_qr()
        qr = _extract_qr(data)
        if qr:
            return qr
        if data:
            return {'error': f'Sem QR: {str(data)[:150]}'}
        return {'error': 'Resposta vazia da Evolution API'}
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
                result.append({'id': jid, 'name': name, 'type': 'contato', 'pic': ''})
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
