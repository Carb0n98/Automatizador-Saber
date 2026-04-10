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


def _h() -> Dict:
    return {'apikey': API_KEY, 'Content-Type': 'application/json'}


def _url(path: str) -> str:
    return f"{BASE_URL}{path}"


def _extract_qr(data: dict) -> Dict:
    """Extrai base64 do QR de qualquer formato de resposta da Evolution API v2."""
    if not isinstance(data, dict):
        return {}

    # Formato A: { base64: '...', code: '...' }
    if data.get('base64'):
        return {'base64': data['base64'], 'code': data.get('code', '')}

    # Formato B: { qrcode: { base64: '...', code: '...' } }
    qr = data.get('qrcode') or {}
    if isinstance(qr, dict) and qr.get('base64'):
        return {'base64': qr['base64'], 'code': qr.get('code', '')}

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


def _delete_instance():
    """Remove a instância completamente (sem verificar erros)."""
    try:
        requests.delete(_url(f'/instance/logout/{INSTANCE}'),
                        headers=_h(), timeout=TIMEOUT)
    except Exception:
        pass
    try:
        requests.delete(_url(f'/instance/delete/{INSTANCE}'),
                        headers=_h(), timeout=TIMEOUT)
    except Exception:
        pass


def ensure_instance_and_get_qr() -> Dict:
    """
    Garante a instância e retorna QR.
    Estratégia: se já conectado retorna; senão, deleta tudo e cria fresh.
    O QR vem diretamente na resposta do POST /instance/create.
    """
    # 1. Se já está conectado — não faz nada
    st = get_status()
    if st.get('connected'):
        return {'already_connected': True}

    # 2. Deleta instância existente (se houver) para garantir QR no create
    if _instance_exists():
        print(f'[WA] Removendo instancia existente antes de recriar...')
        _delete_instance()
        time.sleep(2)

    # 3. Cria instância nova com qrcode=True
    print(f'[WA] Criando instancia {INSTANCE}...')
    try:
        r = requests.post(_url('/instance/create'), headers=_h(), json={
            'instanceName': INSTANCE,
            'qrcode': True,
            'integration': 'WHATSAPP-BAILEYS',
        }, timeout=TIMEOUT)
    except requests.exceptions.ConnectionError:
        return {'error': 'Evolution API offline ou inacessível. Verifique se o container evolution-api está rodando.'}
    except Exception as e:
        return {'error': f'Erro ao criar instância: {e}'}

    if not r.ok:
        return {'error': f'Evolution API retornou HTTP {r.status_code}: {r.text[:300]}'}

    create_data = r.json()
    print(f'[WA] Resposta do create: {str(create_data)[:300]}')

    # 4. QR vem na resposta do create
    qr = _extract_qr(create_data)
    if qr:
        print('[WA] QR extraido da resposta do create com sucesso.')
        return qr

    # 5. Fallback: aguarda e busca QR via GET /instance/connect
    print('[WA] QR nao encontrado na resposta do create. Aguardando...')
    time.sleep(4)

    try:
        r2 = requests.get(_url(f'/instance/connect/{INSTANCE}'),
                          headers=_h(), timeout=TIMEOUT)
        if r2.ok:
            qr = _extract_qr(r2.json())
            if qr:
                return qr
            return {'error': f'GET /connect retornou: {r2.json()}'}
        return {'error': f'GET /connect HTTP {r2.status_code}: {r2.text[:200]}'}
    except Exception as e:
        return {'error': f'Erro no fallback GET /connect: {e}'}


def get_qr() -> Dict:
    """Polling: retorna QR atual ou sinaliza se conectado."""
    try:
        st = get_status()
        if st.get('connected'):
            return {'already_connected': True}
        r = requests.get(_url(f'/instance/connect/{INSTANCE}'),
                         headers=_h(), timeout=TIMEOUT)
        if r.ok:
            data = r.json()
            qr = _extract_qr(data)
            return qr if qr else {'error': f'Sem QR: {data}'}
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
    _delete_instance()
    return True


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
        r = requests.post(_url(f'/chat/findContacts/{INSTANCE}'),
                          headers=_h(), json={}, timeout=TIMEOUT)
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
