"""
Adapter para WAHA (WhatsApp HTTP API) — gateway WhatsApp REST.
Documentação: https://waha.devlike.pro/docs/

Diferenças principais em relação à Evolution API:
- 1 container único, sem PostgreSQL nem Redis
- Engine NOWEB: sessões persistidas em disco, sem dependência de RAM
- chatId formato: "5584999999999@c.us" (contatos), "GROUP_ID@g.us" (grupos)
- Autenticação via header "X-Api-Key"
- Status da sessão: STOPPED | STARTING | SCAN_QR | WORKING | FAILED

Arquitetura de retry:
- send_text() tenta até MAX_RETRIES vezes com backoff exponencial
- Health check via get_status() antes de qualquer envio
- Log integrado via web.logger em todos os paths de erro
"""
import os
import re
import time
import requests
from typing import Dict, List

# ─── Configuração ─────────────────────────────────────────────────────────────
BASE_URL    = os.environ.get('WAHA_BASE_URL', 'http://localhost:3000').rstrip('/')
API_KEY     = os.environ.get('WAHA_API_KEY', 'autoverifica-waha-key-2024')
SESSION     = 'default'   # nome da sessão WAHA (pode ser qualquer string)
TIMEOUT     = 15
MAX_RETRIES = 2           # tentativas extras após a primeira falha


def _h() -> Dict:
    return {'X-Api-Key': API_KEY, 'Content-Type': 'application/json'}


def _url(path: str) -> str:
    return f"{BASE_URL}{path}"


def _log(nivel, msg, **kw):
    """Wrapper seguro para o logger — nunca falha."""
    try:
        from .logger import log
        log(nivel, msg, **kw)
    except Exception:
        pass


# ─── Normalização de chatId ───────────────────────────────────────────────────

def _to_chat_id(to: str) -> str:
    """
    Converte qualquer formato de destino para chatId do WAHA.

    WAHA usa sufixos obrigatórios:
      - Contatos: "5584999999999@c.us"
      - Grupos:   "123456789012345678@g.us"

    Aceita como entrada:
      - "5584999999999"           → "5584999999999@c.us"
      - "+55 84 99999-9999"       → "5584999999999@c.us"
      - "5584999999999@c.us"     → sem mudança
      - "5584999999999@s.whatsapp.net" → "5584999999999@c.us"
      - "123456789@g.us"         → "123456789@g.us"
    """
    to = to.strip()
    if '@g.us' in to:
        return to  # grupo — mantém intacto
    if '@' in to:
        # JID qualquer de contato: extrai dígitos + adiciona @c.us
        numero = re.sub(r'[^0-9]', '', to.split('@')[0])
    else:
        # Número avulso: remove tudo que não é dígito
        numero = re.sub(r'[^0-9]', '', to)
    return f"{numero}@c.us" if numero else ''


# ─── Status do servidor ───────────────────────────────────────────────────────

def is_waha_online() -> bool:
    """Verifica se o WAHA está acessível."""
    try:
        r = requests.get(_url('/api/server/status'), headers=_h(), timeout=5)
        return r.status_code < 500
    except Exception:
        return False


# ─── Gestão de sessão ─────────────────────────────────────────────────────────

def get_status() -> Dict:
    """
    Retorna o status atual da sessão WAHA.

    Possíveis valores de 'status':
      STOPPED   — sessão não iniciada
      STARTING  — iniciando (aguardar)
      SCAN_QR   — QR disponível para escanear
      WORKING   — conectado e funcionando
      FAILED    — falha (re-iniciar necessário)
    """
    try:
        r = requests.get(_url(f'/api/sessions/{SESSION}'), headers=_h(), timeout=TIMEOUT)
        if r.ok:
            data = r.json()
            status = data.get('status', 'UNKNOWN')
            connected = status == 'WORKING'
            return {
                'connected': connected,
                'status': status,
                'name': data.get('me', {}).get('pushName', '') if data.get('me') else '',
                'phone': data.get('me', {}).get('id', '').split('@')[0] if data.get('me') else '',
            }
        if r.status_code == 404:
            return {'connected': False, 'status': 'STOPPED'}
        return {'connected': False, 'status': f'HTTP_{r.status_code}'}
    except requests.exceptions.ConnectionError:
        return {'connected': False, 'status': 'WAHA_OFFLINE'}
    except Exception as e:
        return {'connected': False, 'status': 'ERROR', 'error': str(e)}


def _ensure_session_started() -> bool:
    """
    Garante que a sessão existe E está iniciada no WAHA.

    Fluxo correto por estado:
      Não existe (404) → POST /api/sessions           (cria + inicia)
      STOPPED / FAILED → POST /api/sessions/{name}/start (inicia existente)
      STARTING         → já iniciando, ok
      SCAN_QR          → QR pronto, ok
      WORKING          → já conectado, ok

    NUNCA faz POST /api/sessions se a sessão já existe.
    NUNCA usa PUT (apenas para reconfigurar, não para conectar).
    """
    st = get_status()
    status = st.get('status', 'UNKNOWN')

    _log('DEBUG', f'[WAHA] _ensure_session_started: status atual = {status}', origem='whatsapp')

    # Já está em bom estado — não precisa fazer nada
    if status in ('STARTING', 'SCAN_QR', 'WORKING'):
        return True

    # Sessão não existe → cria (POST /api/sessions)
    if status in ('STOPPED', 'HTTP_404', 'UNKNOWN'):
        # Tenta verificar se realmente não existe via 404
        r_check = None
        try:
            r_check = requests.get(_url(f'/api/sessions/{SESSION}'),
                                   headers=_h(), timeout=TIMEOUT)
        except Exception:
            pass

        session_exists = r_check is not None and r_check.status_code != 404

        if not session_exists:
            # Criar sessão nova
            _log('INFO', 'Criando sessão WAHA (não existia).', origem='whatsapp')
            try:
                r = requests.post(_url('/api/sessions'), headers=_h(), json={
                    'name': SESSION,
                    'config': {
                        'debug': False,
                        'noweb': {'store': {'enabled': True, 'fullSync': False}},
                    },
                }, timeout=TIMEOUT)
                if r.ok:
                    return True
                print(f'[WAHA] POST /api/sessions → HTTP {r.status_code}: {r.text[:200]}')
            except Exception as e:
                print(f'[WAHA] Erro ao criar sessão: {e}')
                return False

        # Sessão existe mas está STOPPED/FAILED → usa start
        _log('INFO', f'Iniciando sessão WAHA existente (status: {status}).', origem='whatsapp')
        try:
            r = requests.post(_url(f'/api/sessions/{SESSION}/start'),
                              headers=_h(), timeout=TIMEOUT)
            if r.ok:
                return True
            body = r.text[:200]
            # "already started" = 422 — é sucesso
            if r.status_code == 422 and 'already' in body.lower():
                return True
            print(f'[WAHA] POST /api/sessions/{SESSION}/start → HTTP {r.status_code}: {body}')
            return False
        except Exception as e:
            print(f'[WAHA] Erro ao iniciar sessão: {e}')
            return False

    if status == 'FAILED':
        # FAILED → tenta stop + start para resetar
        _log('WARNING', 'Sessão WAHA em FAILED, tentando resetar.', origem='whatsapp')
        try:
            requests.post(_url(f'/api/sessions/{SESSION}/stop'),
                          headers=_h(), timeout=TIMEOUT)
            time.sleep(1)
            r = requests.post(_url(f'/api/sessions/{SESSION}/start'),
                              headers=_h(), timeout=TIMEOUT)
            return r.ok
        except Exception as e:
            print(f'[WAHA] Erro ao resetar sessão FAILED: {e}')
            return False

    return False



def ensure_session_and_get_qr() -> Dict:
    """
    Garante que a sessão existe e retorna o QR Code para scan.

    Fluxo WAHA:
      WORKING              → já conectado, retorna already_connected
      STARTING / SCAN_QR   → sessão iniciando, aguarda e retorna QR
      STOPPED / FAILED     → inicia sessão, aguarda SCAN_QR
      Não existe (HTTP_404) → cria sessão e aguarda SCAN_QR
    """
    st = get_status()

    if st['status'] == 'WAHA_OFFLINE':
        return {'error': 'WAHA está offline ou inacessível. Verifique o container.'}

    if st.get('connected'):  # WORKING
        return {'already_connected': True}

    # Se não está rodando, garante que está iniciada
    if st['status'] not in ('STARTING', 'SCAN_QR'):
        _log('INFO', f'Iniciando sessão WAHA (status: {st["status"]}).', origem='whatsapp')
        ok = _ensure_session_started()
        if not ok:
            return {'error': 'Não foi possível iniciar a sessão WAHA. Verifique os logs do container.'}

    # Aguarda até SCAN_QR (máx 12s) com polling curto
    for attempt in range(6):
        time.sleep(2)
        st = get_status()
        if st.get('connected'):
            return {'already_connected': True}
        if st['status'] == 'SCAN_QR':
            break
        if st['status'] == 'FAILED':
            return {'error': 'Sessão WAHA entrou em estado FAILED. Tente desconectar e reconectar.'}

    # Busca o QR — pode ainda estar STARTING (retorna 'starting' para polling)
    return _fetch_qr()



def _fetch_qr() -> Dict:
    """
    Busca o QR code atual da sessão.

    Retornos possíveis:
      {'base64': '...', 'mimetype': 'image/png'}  → QR pronto para exibir
      {'starting': True}                           → sessão iniciando, polling continua
      {'error': '...'}                             → erro real
    """
    try:
        r = requests.get(_url(f'/api/sessions/{SESSION}/qr'),
                         headers=_h(), timeout=TIMEOUT)
        if r.ok:
            data = r.json()
            # WAHA retorna {mimetype: "image/png", data: "base64string"}
            if data.get('data'):
                return {'base64': data['data'], 'mimetype': data.get('mimetype', 'image/png')}
        if r.status_code == 404:
            # 404 = sessão ainda não chegou em SCAN_QR (está STARTING)
            # Não é erro — o frontend deve continuar fazendo polling
            st = get_status()
            if st.get('connected'):
                return {'already_connected': True}
            return {'starting': True, 'status': st.get('status', 'STARTING')}
        return {'error': f'Erro ao buscar QR: HTTP {r.status_code} — {r.text[:200]}'}
    except Exception as e:
        return {'error': f'Erro ao buscar QR: {e}'}


def get_qr() -> Dict:
    """Polling: retorna QR atual ou sinaliza se conectado."""
    st = get_status()
    if st.get('connected'):
        return {'already_connected': True}
    if st['status'] == 'WAHA_OFFLINE':
        return {'error': 'WAHA offline'}
    return _fetch_qr()


def disconnect() -> bool:
    """
    Para a sessão WAHA (stop), mas NÃO deleta.

    Manter a sessão no estado STOPPED permite reconectar via
    POST /api/sessions/{name}/start sem precisar recriar.
    Deletar causaria o erro 422 "already exists" na próxima criação.
    """
    try:
        r = requests.post(_url(f'/api/sessions/{SESSION}/stop'),
                          headers=_h(), timeout=TIMEOUT)
        _log('INFO', f'Sessão WAHA parada (stop). HTTP {r.status_code}.', origem='whatsapp')
    except Exception as e:
        _log('WARNING', f'Erro ao parar sessão WAHA: {e}', origem='whatsapp')
    return True


# ─── Contatos e Grupos ────────────────────────────────────────────────────────

def get_chats(limit: int = 200) -> List[Dict]:
    """Retorna grupos e contatos da sessão atual."""
    result = []

    # Grupos
    try:
        r = requests.get(_url(f'/api/{SESSION}/groups'),
                         headers=_h(), timeout=TIMEOUT)
        if r.ok:
            for g in (r.json() or []):
                result.append({
                    'id':   g.get('id', ''),
                    'name': g.get('subject') or g.get('name') or g.get('id', ''),
                    'type': 'grupo',
                    'pic':  '',
                })
    except Exception:
        pass

    # Contatos recentes
    try:
        r = requests.get(_url(f'/api/contacts/all?session={SESSION}'),
                         headers=_h(), timeout=TIMEOUT)
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


# ─── Envio de Mensagem ────────────────────────────────────────────────────────

def send_text(to: str, text: str) -> Dict:
    """
    Envia mensagem de texto via WAHA com retry automático.

    Camadas de proteção:
      1. Verifica status da sessão (WORKING?)
      2. Normaliza chatId para formato WAHA
      3. Envia com até MAX_RETRIES tentativas (backoff exponencial)
      4. Loga todos os eventos no sistema de logs centralizado

    Args:
        to:   Número de telefone, JID @c.us ou @g.us
        text: Texto da mensagem

    Returns:
        {'ok': True, 'data': ...}          em caso de sucesso
        {'ok': False, 'error': '...', 'needs_reconnect': bool}  em caso de falha
    """
    # ── Camada 1: Verificar sessão ────────────────────────────────────────────
    status = get_status()
    if not status.get('connected'):
        msg = (f'Sessão WAHA não está ativa (status: {status.get("status")}). '
               'Acesse a aba WhatsApp e escaneie o QR Code.')
        _log('WARNING', msg, origem='whatsapp')
        return {'ok': False, 'error': msg, 'needs_reconnect': True}

    # ── Camada 2: Normalizar chatId ───────────────────────────────────────────
    chat_id = _to_chat_id(to)
    if not chat_id:
        return {'ok': False, 'error': f'Número/JID inválido: {to!r}'}

    payload = {'chatId': chat_id, 'text': text, 'session': SESSION}
    preview = text[:60] + ('...' if len(text) > 60 else '')
    _log('DEBUG', f'Tentando enviar → {chat_id} | "{preview}"', origem='whatsapp')

    # ── Camada 3: Envio com retry ─────────────────────────────────────────────
    last_result = None
    for attempt in range(MAX_RETRIES + 1):
        if attempt > 0:
            wait = 2 ** (attempt - 1)  # 1s, 2s, 4s...
            _log('WARNING',
                 f'Retry {attempt}/{MAX_RETRIES} para {chat_id} (aguardando {wait}s).',
                 origem='whatsapp')
            time.sleep(wait)

        last_result = _do_send_text(chat_id, payload, attempt)

        if last_result['ok']:
            _log('INFO',
                 f'Mensagem enviada com sucesso → {chat_id}',
                 origem='whatsapp')
            return last_result

        if not last_result.get('retryable', False):
            break  # erro definitivo — não adianta tentar de novo

    # Todas as tentativas falharam
    _log('ERROR',
         f'Falha ao enviar mensagem para {chat_id} após {MAX_RETRIES + 1} tentativa(s): '
         f'{last_result.get("error")}',
         origem='whatsapp',
         detalhe=last_result.get('raw_response', ''))
    return last_result


def _do_send_text(chat_id: str, payload: Dict, attempt: int = 0) -> Dict:
    """Executa uma tentativa de envio HTTP para o WAHA."""
    try:
        r = requests.post(
            _url('/api/sendText'),
            headers=_h(),
            json=payload,
            timeout=30,
        )
        print(f'[WAHA] sendText → HTTP {r.status_code} (tentativa {attempt + 1}) '
              f'| chatId: {chat_id} | {r.text[:200]}')

        if r.ok:
            return {'ok': True, 'data': r.json()}

        # ── Erros tratados ─────────────────────────────────────────────────
        try:
            err_body = r.json()
        except Exception:
            err_body = r.text[:300]

        err_str = str(err_body).lower()

        # Sessão não está pronta → needs_reconnect (não é retryable)
        if any(k in err_str for k in ('not authenticated', 'session not found',
                                       'not connected', 'no session')):
            return {
                'ok': False,
                'error': 'Sessão WhatsApp não autenticada. Reconecte na aba WhatsApp.',
                'needs_reconnect': True,
                'retryable': False,
                'raw_response': str(err_body),
            }

        # Erro de rate limit ou timeout → retryable
        if r.status_code in (429, 503) or 'timeout' in err_str:
            return {
                'ok': False,
                'error': f'WAHA ocupado (HTTP {r.status_code}). Tentando novamente...',
                'retryable': True,
                'raw_response': str(err_body),
            }

        # Outros erros HTTP (400, 422 etc.) → não retryable
        return {
            'ok': False,
            'error': f'HTTP {r.status_code}: {err_body}',
            'retryable': False,
            'raw_response': str(err_body),
        }

    except requests.exceptions.Timeout:
        return {'ok': False, 'error': 'Timeout na requisição ao WAHA.', 'retryable': True}
    except requests.exceptions.ConnectionError:
        return {'ok': False, 'error': 'WAHA offline ou inacessível.', 'retryable': False}
    except Exception as e:
        return {'ok': False, 'error': str(e), 'retryable': False}
