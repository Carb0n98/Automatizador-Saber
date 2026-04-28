"""
Wrapper para a Evolution API — gateway WhatsApp REST.
Documentação: https://doc.evolution-api.com

Arquitetura de sessão:
- A Evolution API v2 persiste o STATUS da instância no PostgreSQL
- Os ARQUIVOS de sessão Baileys ficam em /evolution/instances (volume Docker)
- Porém o cache local (RAM) pode ser perdido em restarts, causando SessionError
- Por isso, validamos a sessão ativamente antes de enviar mensagens
"""
import os
import re
import time
import requests
from typing import Dict, List

# ─── Configuração via variáveis de ambiente ────────────────────────────────
BASE_URL = os.environ.get('EVOLUTION_BASE_URL', 'http://localhost:8080').rstrip('/')
API_KEY  = os.environ.get('EVOLUTION_API_KEY', 'autoverifica-evo-key-2024')
INSTANCE = 'autoverifica-v2'
TIMEOUT  = 15


def _h() -> Dict:
    return {'apikey': API_KEY, 'Content-Type': 'application/json'}


def _url(path: str) -> str:
    return f"{BASE_URL}{path}"


def _extract_qr(data: dict) -> Dict:
    """Extrai base64 do QR de qualquer formato de resposta da Evolution API v2."""
    if not isinstance(data, dict):
        return {}
    if data.get('base64'):
        return {'base64': data['base64'], 'code': data.get('code', '')}
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


# ─── Health Check de Sessão ───────────────────────────────────────────────

def check_session_health() -> bool:
    """
    Verifica se a sessão Baileys está REALMENTE ativa fazendo uma chamada
    leve que requer sessão válida (fetchProfile da própria instância).

    Isso detecta o caso onde connectionStatus=open no banco mas a sessão
    RAM foi perdida (container restart com CACHE_LOCAL_ENABLED).

    Retorna True se a sessão está funcional, False se está morta.
    """
    try:
        # GET /chat/findChats é leve e exige sessão ativa
        r = requests.post(
            _url(f'/chat/findChats/{INSTANCE}'),
            headers=_h(),
            json={'where': {}, 'limit': 1},
            timeout=8
        )
        if r.status_code == 200:
            return True
        # 400 com "No sessions" ou "Bad Session" = sessão morta
        if r.status_code == 400:
            body = r.text.lower()
            if 'no sessions' in body or 'session' in body or 'bad session' in body:
                print(f'[WA] Health check: {r.text[:150]}')
                try:
                    from ..logger import log_warn
                    log_warn('Sessão Baileys morta (health check).', origem='whatsapp', detalhe=r.text[:500])
                except Exception:
                    pass
                return False
        # Outros erros (404, 500) não são definitivos sobre sessão
        return r.status_code < 500
    except Exception as e:
        print(f'[WA] Health check: erro de conexão → {e}')
        return False


def _try_reconnect() -> bool:
    """
    Tenta reativar uma sessão morta fazendo logout + reconnect.
    Não deleta a instância (preserva credenciais no banco).
    Retorna True se reconectou, False se precisa de novo QR.
    """
    print('[WA] Tentando reconexão automática da sessão...')
    try:
        # Logout limpa o estado corrompido sem deletar a instância
        requests.delete(_url(f'/instance/logout/{INSTANCE}'),
                        headers=_h(), timeout=TIMEOUT)
        time.sleep(2)

        # Tenta reconectar — se houver sessão salva em disco, o Baileys recupera
        r = requests.get(_url(f'/instance/connect/{INSTANCE}'),
                         headers=_h(), timeout=TIMEOUT)
        if r.ok:
            data = r.json()
            # Se retornou QR → não reconectou automaticamente, precisa scan
            if _extract_qr(data):
                print('[WA] Reconexão: QR gerado, usuário precisa escanear.')
                return False
            # status code 200 sem QR pode significar que reconectou
            print(f'[WA] Reconexão: resposta sem QR → {data}')
            time.sleep(3)
            status = get_status()
            if status.get('connected'):
                print('[WA] Reconexão automática bem-sucedida!')
                return True
    except Exception as e:
        print(f'[WA] Erro na tentativa de reconexão: {e}')
    return False


# ─── QR Code e Conexão ───────────────────────────────────────────────────

def ensure_instance_and_get_qr() -> Dict:
    """
    Garante a instância e retorna QR.
    Se já existe, usa o GET /instance/connect (que gera QR para instâncias offline).
    Se não existe, cria a instância. EVITAMOS DELETAR a instância aqui, pois
    a Evolution API v2 apaga instâncias de forma assíncrona, e se recriarmos
    logo em seguida, a task de exclusão apaga a nova instância!
    """
    # 1. Se já está conectado — não faz nada
    st = get_status()
    if st.get('connected'):
        return {'already_connected': True}

    # 2. Se a instância já existe, apenas pede o connect para gerar QR
    if _instance_exists():
        print(f'[WA] Instância {INSTANCE} existe. Solicitando QR (GET /connect)...')
        try:
            r = requests.get(_url(f'/instance/connect/{INSTANCE}'),
                             headers=_h(), timeout=TIMEOUT)
            if r.ok:
                data = r.json()
                qr = _extract_qr(data)
                if qr:
                    return qr
                # Se não retornou QR (ex: count: 0), a instância pode estar corrompida.
                # Tentamos dar um logout (não delete) para resetar a conexão.
                print(f'[WA] GET /connect retornou sem QR: {data}. Resetando conexão...')
                requests.delete(_url(f'/instance/logout/{INSTANCE}'), headers=_h(), timeout=TIMEOUT)
                time.sleep(2)
                # Tenta conectar de novo
                r2 = requests.get(_url(f'/instance/connect/{INSTANCE}'), headers=_h(), timeout=TIMEOUT)
                if r2.ok:
                    qr2 = _extract_qr(r2.json())
                    if qr2:
                        return qr2
                return {'error': f'A instância falhou em gerar o QR code. Resposta: {r2.json()}'}
            return {'error': f'Erro ao solicitar QR: HTTP {r.status_code}'}
        except Exception as e:
            return {'error': f'Erro de conexão ao buscar QR: {e}'}

    # 3. Cria instância nova com qrcode=True
    print(f'[WA] Criando instancia {INSTANCE}...')
    try:
        r = requests.post(_url('/instance/create'), headers=_h(), json={
            'instanceName': INSTANCE,
            'qrcode': True,
            'integration': 'WHATSAPP-BAILEYS',
        }, timeout=TIMEOUT)
    except requests.exceptions.ConnectionError:
        return {'error': 'Evolution API offline ou inacessível.'}
    except Exception as e:
        return {'error': f'Erro ao criar instância: {e}'}

    if not r.ok:
        return {'error': f'Falha ao criar: HTTP {r.status_code}: {r.text[:300]}'}

    create_data = r.json()
    print(f'[WA] Instância criada. Resposta: {str(create_data)[:200]}')

    # 4. QR deve vir na resposta do create
    qr = _extract_qr(create_data)
    if qr:
        return qr

    # 5. Fallback curto
    time.sleep(2)
    try:
        r2 = requests.get(_url(f'/instance/connect/{INSTANCE}'), headers=_h(), timeout=TIMEOUT)
        if r2.ok:
            return _extract_qr(r2.json()) or {'error': f'GET /connect sem QR: {r2.json()}'}
        return {'error': f'Fallback HTTP {r2.status_code}'}
    except Exception as e:
        return {'error': f'Erro fallback: {e}'}


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
    """
    Envia mensagem de texto para número ou grupo via Evolution API.

    Implementa validação de sessão em 3 camadas:
    1. Verificação de status (DB) — rápida
    2. Health check real da sessão Baileys — detecta sessão morta
    3. Tentativa de reconexão automática se sessão morta

    - Grupos (JID @g.us): envia o JID exatamente como está
    - Contatos: normaliza removendo +, espaços e traços, mantendo apenas dígitos
    """
    # ── Camada 1: Verificação de status no banco ──────────────────────────
    status = get_status()
    if not status.get('connected'):
        return {
            'ok': False,
            'error': f'WhatsApp não está conectado (status: {status.get("status")}). '
                     'Acesse a aba WhatsApp e escaneie o QR Code.',
            'needs_reconnect': True,
        }

    # ── Camada 2: Health check real da sessão Baileys ────────────────────
    if not check_session_health():
        print('[WA] Sessão Baileys inativa (SessionError: No sessions detectado preventivamente).')
        # ── Camada 3: Tentativa de reconexão automática ──────────────────
        if not _try_reconnect():
            return {
                'ok': False,
                'error': 'Sessão WhatsApp perdida (possivelmente após restart). '
                         'Acesse a aba WhatsApp e reconecte escaneando o QR Code.',
                'needs_reconnect': True,
            }
        # Reconectou — continua o envio

    # ── Normalização do destinatário ─────────────────────────────────────
    if '@g.us' in to:
        # Grupo — o JID deve ser enviado inteiro (ex: 12345678901234567890@g.us)
        numero = to.strip()
    elif '@s.whatsapp.net' in to:
        # JID completo de contato — extrai só os dígitos antes do @
        numero = re.sub(r'[^0-9]', '', to.split('@')[0])
    else:
        # Número avulso — normaliza: remove +, espaços, traços, parênteses
        numero = re.sub(r'[^0-9]', '', to)

    if not numero:
        return {'ok': False, 'error': 'Número/JID inválido após normalização.'}

    payload = {'number': numero, 'text': text}
    print(f'[WA] send_text → número: {numero!r} | preview: {text[:60]!r}')

    try:
        r = requests.post(
            _url(f'/message/sendText/{INSTANCE}'),
            headers=_h(),
            json=payload,
            timeout=30
        )
        print(f'[WA] Resposta: HTTP {r.status_code} — {r.text[:300]}')

        if r.ok:
            return {'ok': True, 'data': r.json()}

        # ── Tratamento especial: SessionError: No sessions ────────────────
        # Mesmo com health check, pode ocorrer em race condition.
        # Se detectado, sinaliza para o chamador que precisa reconectar.
        try:
            err_body = r.json()
        except Exception:
            err_body = r.text[:300]

        err_str = str(err_body).lower()
        if 'no sessions' in err_str or 'session' in err_str and r.status_code == 400:
            print(f'[WA] SessionError detectado.')
            try:
                from ..logger import log_error
                log_error('SessionError: No sessions no envio.', origem='whatsapp',
                          detalhe=f'Numero: {numero} | Resp: {err_body}')
            except Exception:
                pass
            return {
                'ok': False,
                'error': 'Sessão WhatsApp expirada. Reconecte escaneando o QR Code na aba WhatsApp.',
                'needs_reconnect': True,
                'raw': err_body,
            }

        try:
            from ..logger import log_error
            log_error(f'Falha ao enviar mensagem: HTTP {r.status_code}', origem='whatsapp',
                      detalhe=f'Resp: {err_body}')
        except Exception:
            pass
        return {'ok': False, 'error': f'HTTP {r.status_code}: {err_body}'}

    except Exception as e:
        print(f'[WA] Excecao em send_text: {e}')
        return {'ok': False, 'error': str(e)}


def is_evolution_online() -> bool:
    """Verifica se a Evolution API está acessível."""
    try:
        r = requests.get(_url('/'), headers=_h(), timeout=5)
        return r.status_code < 500
    except Exception:
        return False
