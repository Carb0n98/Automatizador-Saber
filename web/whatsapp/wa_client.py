"""
WahaSessionManager — Gerenciador de sessão WhatsApp via WAHA HTTP API.
Documentação WAHA: https://waha.devlike.pro/docs/

Arquitetura:
  - Classe WahaSessionManager com cache local de estado
  - Decisões baseadas em estado real (state-driven), sem chamadas cegas à API
  - Retry com backoff exponencial apenas em send_text
  - Logs estruturados integrados ao sistema centralizado (web.logger)

Estados da sessão WAHA:
  STOPPED   → sessão existe mas não está iniciada
  STARTING  → iniciando (aguardar QR)
  SCAN_QR   → QR disponível para escanear
  WORKING   → conectado e funcionando ✅
  FAILED    → erro — necessita reset
  (404)     → sessão não existe ainda

Regras de transição:
  Não existe → POST /api/sessions                   → STARTING
  STOPPED    → POST /api/sessions/{name}/start       → STARTING
  FAILED     → POST /api/sessions/{name}/stop + start → STARTING
  STARTING   → aguardar polling                      → SCAN_QR → WORKING
  WORKING    → POST /api/sessions/{name}/stop        → STOPPED

NUNCA:
  - POST /api/sessions se sessão já existe (422)
  - Solicitar QR se status for WORKING
  - DELETE sessão ao desconectar (impede reconexão limpa)
"""
import os
import re
import time
import threading
import requests
from typing import Dict, List, Optional


# ─── Configuração via variáveis de ambiente ────────────────────────────────────
_BASE_URL    = os.environ.get('WAHA_BASE_URL', 'http://localhost:3000').rstrip('/')
_API_KEY     = os.environ.get('WAHA_API_KEY', 'autoverifica-waha-key-2024')
_SESSION     = 'default'
_TIMEOUT     = 15
_MAX_RETRIES = 2


# ═══════════════════════════════════════════════════════════════════════════════
# WahaSessionManager
# ═══════════════════════════════════════════════════════════════════════════════

class WahaSessionManager:
    """
    Gerenciador de sessão WAHA com cache local de estado.

    Benefícios vs. chamadas diretas à API:
      - Elimina chamadas repetidas: cache TTL de 10s por padrão
      - Zero erros 422 "already exists/started": decisões por estado
      - Polling inteligente apenas quando necessário
      - Thread-safe com Lock
    """

    # Estados que indicam que a sessão está operacional (não precisa de ação)
    _HEALTHY  = {'WORKING'}
    _PENDING  = {'STARTING', 'SCAN_QR'}
    _INACTIVE = {'STOPPED', 'FAILED'}

    def __init__(
        self,
        base_url: str  = _BASE_URL,
        api_key: str   = _API_KEY,
        session: str   = _SESSION,
        timeout: int   = _TIMEOUT,
        max_retries: int = _MAX_RETRIES,
        state_ttl: int = 10,   # segundos de cache do estado
    ):
        self.base_url    = base_url
        self.api_key     = api_key
        self.session     = session
        self.timeout     = timeout
        self.max_retries = max_retries
        self.state_ttl   = state_ttl

        # Cache de estado local
        self._state: Optional[Dict]  = None
        self._state_ts: float        = 0.0
        self._lock = threading.Lock()

    # ─── Infraestrutura ───────────────────────────────────────────────────────

    def _h(self) -> Dict:
        return {'X-Api-Key': self.api_key, 'Content-Type': 'application/json'}

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def _log(self, nivel: str, msg: str, **kw):
        """Wrapper seguro para o logger centralizado — nunca propaga exceção."""
        try:
            from web.logger import log
            log(nivel, msg, origem='whatsapp', **kw)
        except Exception:
            pass

    # ─── Cache de estado ──────────────────────────────────────────────────────

    def _is_cache_valid(self) -> bool:
        return (
            self._state is not None
            and (time.time() - self._state_ts) < self.state_ttl
        )

    def _invalidate_cache(self):
        with self._lock:
            self._state    = None
            self._state_ts = 0.0

    def _cache_state(self, state: Dict):
        with self._lock:
            self._state    = state
            self._state_ts = time.time()

    # ─── API: Status da sessão ────────────────────────────────────────────────

    def get_status(self, force: bool = False) -> Dict:
        """
        Retorna o status atual da sessão.

        Usa cache local para evitar polling excessivo.
        Passe force=True para forçar leitura da API.

        Returns:
            {
                'connected': bool,
                'status': 'WORKING' | 'STOPPED' | 'STARTING' | 'SCAN_QR' | 'FAILED' | ...,
                'exists': bool,
                'name': str,   # nome exibido no WhatsApp
                'phone': str,  # número conectado
            }
        """
        if not force and self._is_cache_valid():
            return self._state.copy()

        result = self._fetch_status()
        self._cache_state(result)
        return result.copy()

    def _fetch_status(self) -> Dict:
        """Consulta real à API — sempre usa a rede."""
        try:
            r = requests.get(
                self._url(f'/api/sessions/{self.session}'),
                headers=self._h(),
                timeout=self.timeout,
            )
            if r.ok:
                data   = r.json()
                status = data.get('status', 'UNKNOWN')
                me     = data.get('me') or {}
                return {
                    'connected': status == 'WORKING',
                    'status':    status,
                    'exists':    True,
                    'name':      me.get('pushName', ''),
                    'phone':     me.get('id', '').split('@')[0],
                }
            if r.status_code == 404:
                return {'connected': False, 'status': 'NOT_EXISTS', 'exists': False}
            return {'connected': False, 'status': f'HTTP_{r.status_code}', 'exists': False}

        except requests.exceptions.ConnectionError:
            return {'connected': False, 'status': 'WAHA_OFFLINE', 'exists': False}
        except Exception as e:
            return {'connected': False, 'status': 'ERROR', 'exists': False, 'error': str(e)}

    def is_online(self) -> bool:
        """Verifica se o servidor WAHA está acessível."""
        try:
            r = requests.get(self._url('/api/server/status'), headers=self._h(), timeout=5)
            return r.status_code < 500
        except Exception:
            return False

    # ─── API: Ciclo de vida da sessão ─────────────────────────────────────────

    def _create_session(self) -> bool:
        """POST /api/sessions — somente quando a sessão NÃO existe."""
        self._log('INFO', f'Criando sessão WAHA "{self.session}".')
        try:
            r = requests.post(self._url('/api/sessions'), headers=self._h(), json={
                'name': self.session,
                'config': {
                    'debug': False,
                    'noweb': {'store': {'enabled': True, 'fullSync': False}},
                },
            }, timeout=self.timeout)
            ok = r.ok
            if not ok:
                self._log('ERROR',
                          f'Falha ao criar sessão: HTTP {r.status_code}',
                          detalhe=r.text[:300])
            return ok
        except Exception as e:
            self._log('ERROR', f'Exceção ao criar sessão: {e}')
            return False

    def _start_session(self) -> bool:
        """POST /api/sessions/{name}/start — quando sessão existe mas está STOPPED."""
        self._log('INFO', f'Iniciando sessão WAHA "{self.session}" (start).')
        try:
            r = requests.post(
                self._url(f'/api/sessions/{self.session}/start'),
                headers=self._h(),
                timeout=self.timeout,
            )
            if r.ok:
                return True
            body = r.text[:300]
            # 422 "already started" = já está ativa = sucesso
            if r.status_code == 422 and 'already' in body.lower():
                self._log('DEBUG', 'Sessão já estava iniciada (422 ignorado como sucesso).')
                return True
            self._log('ERROR', f'Falha ao iniciar sessão: HTTP {r.status_code}', detalhe=body)
            return False
        except Exception as e:
            self._log('ERROR', f'Exceção ao iniciar sessão: {e}')
            return False

    def _stop_session(self) -> bool:
        """POST /api/sessions/{name}/stop — para a sessão sem deletar."""
        try:
            r = requests.post(
                self._url(f'/api/sessions/{self.session}/stop'),
                headers=self._h(),
                timeout=self.timeout,
            )
            self._log('INFO', f'Sessão parada (stop). HTTP {r.status_code}.')
            return r.ok
        except Exception as e:
            self._log('WARNING', f'Erro ao parar sessão: {e}')
            return False

    # ─── Lógica principal: garantir sessão ativa ─────────────────────────────

    def ensure_ready(self) -> Dict:
        """
        Garante que a sessão está no caminho correto para WORKING.

        Fluxo state-driven (sem chamadas desnecessárias):

          NOT_EXISTS  → _create_session()  → STARTING
          STOPPED     → _start_session()   → STARTING
          FAILED      → _stop_session()
                        + _start_session() → STARTING
          STARTING    → aguarda SCAN_QR   (sem nova ação)
          SCAN_QR     → busca QR          (sem nova ação)
          WORKING     → retorna already_connected imediatamente

        Returns:
            {'already_connected': True}              — já conectado
            {'base64': '...', 'mimetype': '...'}     — QR pronto
            {'starting': True, 'status': '...'}      — iniciando, continuar polling
            {'error': '...'}                         — erro irrecuperável
        """
        self._invalidate_cache()   # força leitura fresca ao iniciar fluxo
        st = self.get_status(force=True)
        status = st.get('status')

        self._log('DEBUG', f'ensure_ready: status={status}')

        # ── Servidor inacessível ───────────────────────────────────────────
        if status == 'WAHA_OFFLINE':
            return {'error': 'WAHA offline. Verifique o container no Dokploy.'}

        # ── Já conectado ───────────────────────────────────────────────────
        if status == 'WORKING':
            return {'already_connected': True}

        # ── Sessão inexistente: criar ──────────────────────────────────────
        if status == 'NOT_EXISTS':
            ok = self._create_session()
            if not ok:
                return {'error': 'Falha ao criar sessão WAHA.'}
            self._invalidate_cache()

        # ── Sessão parada: iniciar ─────────────────────────────────────────
        elif status == 'STOPPED':
            ok = self._start_session()
            if not ok:
                return {'error': 'Falha ao iniciar sessão WAHA.'}
            self._invalidate_cache()

        # ── Sessão com falha: resetar ──────────────────────────────────────
        elif status == 'FAILED':
            self._log('WARNING', 'Sessão em FAILED — executando reset (stop + start).')
            self._stop_session()
            time.sleep(1)
            ok = self._start_session()
            if not ok:
                return {'error': 'Falha ao resetar sessão WAHA em estado FAILED.'}
            self._invalidate_cache()

        # ── STARTING/SCAN_QR: já está no caminho certo, só busca QR ──────
        # (nenhuma ação de start necessária)

        # ── Aguarda até SCAN_QR com polling controlado (máx 20s) ──────────
        for attempt in range(10):
            time.sleep(2)
            st = self.get_status(force=True)
            status = st.get('status')

            if status == 'WORKING':
                return {'already_connected': True}
            if status == 'SCAN_QR':
                break
            if status in ('FAILED', 'WAHA_OFFLINE'):
                return {'error': f'Sessão entrou em estado {status} durante a inicialização.'}
            # STARTING → continua aguardando

        # ── Tenta obter QR ────────────────────────────────────────────────
        return self._fetch_qr()

    # ─── QR Code ──────────────────────────────────────────────────────────────

    def _fetch_qr(self) -> Dict:
        """
        Busca o QR code da sessão.

        Retornos:
          {'base64': '...', 'mimetype': '...'}  → QR pronto
          {'starting': True, 'status': '...'}   → ainda iniciando, polling continua
          {'already_connected': True}            → conectou enquanto aguardava
          {'error': '...'}                       → erro real
        """
        # Não solicita QR se já conectado
        st = self.get_status()
        if st.get('connected'):
            return {'already_connected': True}

        try:
            r = requests.get(
                self._url(f'/api/sessions/{self.session}/qr'),
                headers=self._h(),
                timeout=self.timeout,
            )
            if r.ok:
                data = r.json()
                if data.get('data'):
                    return {
                        'base64':   data['data'],
                        'mimetype': data.get('mimetype', 'image/png'),
                    }
            if r.status_code == 404:
                # Sessão ainda em STARTING — não é erro
                st = self.get_status(force=True)
                if st.get('connected'):
                    return {'already_connected': True}
                return {'starting': True, 'status': st.get('status', 'STARTING')}

            return {'error': f'Erro ao buscar QR: HTTP {r.status_code} — {r.text[:200]}'}

        except Exception as e:
            return {'error': f'Erro ao buscar QR: {e}'}

    def get_qr_poll(self) -> Dict:
        """
        Endpoint para polling de QR pelo frontend (chamado a cada 5s).

        Não inicia sessão — apenas retorna estado atual.
        """
        st = self.get_status(force=True)
        status = st.get('status')

        if status == 'WORKING':
            return {'connected': True}
        if status == 'WAHA_OFFLINE':
            return {'connected': False, 'error': 'WAHA offline'}
        return self._fetch_qr()

    # ─── Desconexão ───────────────────────────────────────────────────────────

    def disconnect(self) -> bool:
        """
        Para a sessão (STOP), mas NÃO deleta.

        Manter a sessão em STOPPED permite reconectar via /start
        sem precisar recriar — evita o 422 "already exists".
        """
        ok = self._stop_session()
        self._invalidate_cache()
        return ok

    # ─── Contatos e Grupos ────────────────────────────────────────────────────

    def get_chats(self, limit: int = 200) -> List[Dict]:
        """Retorna grupos e contatos da sessão ativa."""
        result: List[Dict] = []

        # Grupos
        try:
            r = requests.get(
                self._url(f'/api/{self.session}/groups'),
                headers=self._h(), timeout=self.timeout,
            )
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
            r = requests.get(
                self._url(f'/api/contacts/all?session={self.session}'),
                headers=self._h(), timeout=self.timeout,
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

    # ─── Envio de mensagem ────────────────────────────────────────────────────

    @staticmethod
    def _normalize_chat_id(to: str) -> str:
        """
        Normaliza destino para chatId WAHA.

        Grupos (@g.us): mantém intacto.
        Contatos: extrai dígitos + adiciona @c.us.
        """
        to = to.strip()
        if '@g.us' in to:
            return to
        if '@' in to:
            digits = re.sub(r'[^0-9]', '', to.split('@')[0])
        else:
            digits = re.sub(r'[^0-9]', '', to)
        return f"{digits}@c.us" if digits else ''

    def send_text(self, to: str, text: str) -> Dict:
        """
        Envia mensagem de texto com validação de sessão e retry.

        Camadas:
          1. Verifica se sessão está WORKING (sem chamada extra se cache válido)
          2. Normaliza chatId
          3. Envia com backoff exponencial (até max_retries tentativas extras)
          4. Loga todos os eventos

        Returns:
          {'ok': True, 'data': {...}}
          {'ok': False, 'error': '...', 'needs_reconnect': bool}
        """
        # Camada 1: sessão ativa?
        st = self.get_status()
        if not st.get('connected'):
            msg = (f'Sessão WAHA não está ativa (status: {st.get("status")}). '
                   'Acesse a aba WhatsApp e escaneie o QR Code.')
            self._log('WARNING', msg)
            return {'ok': False, 'error': msg, 'needs_reconnect': True}

        # Camada 2: normalizar chatId
        chat_id = self._normalize_chat_id(to)
        if not chat_id:
            return {'ok': False, 'error': f'Número/JID inválido: {to!r}'}

        payload = {'chatId': chat_id, 'text': text, 'session': self.session}
        preview = text[:60] + ('...' if len(text) > 60 else '')
        self._log('DEBUG', f'Enviando → {chat_id} | "{preview}"')

        # Camada 3: envio com retry
        last_result: Dict = {}
        for attempt in range(self.max_retries + 1):
            if attempt > 0:
                wait = 2 ** (attempt - 1)   # 1s, 2s, 4s...
                self._log('WARNING',
                          f'Retry {attempt}/{self.max_retries} para {chat_id} ({wait}s).')
                time.sleep(wait)

            last_result = self._do_send(chat_id, payload)

            if last_result['ok']:
                # Invalida cache de status para refletir estado real após envio
                self._invalidate_cache()
                self._log('INFO', f'Mensagem enviada → {chat_id}')
                return last_result

            if not last_result.get('retryable', False):
                break   # erro definitivo

        self._log('ERROR',
                  f'Falha definitiva ao enviar para {chat_id}: {last_result.get("error")}',
                  detalhe=last_result.get('raw_response', ''))
        return last_result

    def _do_send(self, chat_id: str, payload: Dict) -> Dict:
        """Executa uma tentativa de HTTP POST para /api/sendText."""
        try:
            r = requests.post(
                self._url('/api/sendText'),
                headers=self._h(),
                json=payload,
                timeout=30,
            )
            if r.ok:
                return {'ok': True, 'data': r.json()}

            try:
                err_body = r.json()
            except Exception:
                err_body = r.text[:300]

            err_str = str(err_body).lower()

            # Sessão não autenticada → reconectar
            if any(k in err_str for k in ('not authenticated', 'session not found',
                                           'not connected', 'no session')):
                self._invalidate_cache()
                return {
                    'ok': False,
                    'error': 'Sessão WhatsApp não autenticada. Reconecte na aba WhatsApp.',
                    'needs_reconnect': True,
                    'retryable': False,
                    'raw_response': str(err_body),
                }

            # Rate limit / timeout → retryable
            if r.status_code in (429, 503) or 'timeout' in err_str:
                return {
                    'ok': False,
                    'error': f'WAHA sobrecarregado (HTTP {r.status_code}). Tentando novamente...',
                    'retryable': True,
                    'raw_response': str(err_body),
                }

            return {
                'ok': False,
                'error': f'HTTP {r.status_code}: {err_body}',
                'retryable': False,
                'raw_response': str(err_body),
            }

        except requests.exceptions.Timeout:
            return {'ok': False, 'error': 'Timeout ao chamar WAHA.', 'retryable': True}
        except requests.exceptions.ConnectionError:
            return {'ok': False, 'error': 'WAHA inacessível.', 'retryable': False}
        except Exception as e:
            return {'ok': False, 'error': str(e), 'retryable': False}


# ═══════════════════════════════════════════════════════════════════════════════
# Singleton — instância única compartilhada por toda a aplicação Flask
# ═══════════════════════════════════════════════════════════════════════════════

_manager: Optional[WahaSessionManager] = None


def _get_manager() -> WahaSessionManager:
    """Retorna o singleton WahaSessionManager (lazy init)."""
    global _manager
    if _manager is None:
        _manager = WahaSessionManager()
    return _manager


# ─── API pública — mantém compatibilidade com routes.py existente ─────────────

def is_waha_online() -> bool:
    return _get_manager().is_online()

def get_status() -> Dict:
    return _get_manager().get_status()

def ensure_session_and_get_qr() -> Dict:
    return _get_manager().ensure_ready()

def get_qr() -> Dict:
    return _get_manager().get_qr_poll()

def disconnect() -> bool:
    return _get_manager().disconnect()

def get_chats(limit: int = 200) -> List[Dict]:
    return _get_manager().get_chats(limit)

def send_text(to: str, text: str) -> Dict:
    return _get_manager().send_text(to, text)
