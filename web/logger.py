"""
Logger centralizado do sistema.

Uso em qualquer módulo:
    from .logger import log_info, log_warn, log_error, log_debug, log_audit

Todas as funções são seguras fora de app_context (falham silenciosamente).
"""
import traceback
from datetime import datetime, timezone


def log(nivel: str, mensagem: str, origem: str = 'sistema',
        detalhe: str = None, usuario: str = None) -> None:
    """
    Persiste um evento de log no banco de dados.
    Falha silenciosamente se não houver app_context (ex: import time).
    """
    try:
        from .extensions import db
        from .models import AppLog

        entry = AppLog(
            nivel=nivel.upper()[:10],
            origem=origem[:50],
            mensagem=str(mensagem)[:2000],
            detalhe=(str(detalhe)[:5000] if detalhe else None),
            usuario=(str(usuario)[:80] if usuario else None),
        )
        db.session.add(entry)
        db.session.commit()
    except Exception:
        # Logger nunca deve crashar a aplicação
        pass


def log_info(mensagem: str, **kw) -> None:
    """Registra evento informativo."""
    log('INFO', mensagem, **kw)


def log_warn(mensagem: str, **kw) -> None:
    """Registra aviso (possível problema)."""
    log('WARNING', mensagem, **kw)


def log_error(mensagem: str, exc: Exception = None, **kw) -> None:
    """
    Registra erro. Se `exc` for fornecido, o stack trace é capturado
    automaticamente como `detalhe`.
    """
    detalhe = kw.pop('detalhe', None)
    if exc is not None and detalhe is None:
        detalhe = traceback.format_exc()
    log('ERROR', mensagem, detalhe=detalhe, **kw)


def log_debug(mensagem: str, **kw) -> None:
    """Registra mensagem de debug (apenas em desenvolvimento)."""
    log('DEBUG', mensagem, **kw)


def log_audit(mensagem: str, **kw) -> None:
    """
    Registra ação de auditoria (login, alteração de dados, exclusão).
    Captura automaticamente IP e user-agent quando em contexto de request.
    """
    detalhe_parts = []

    # Capturar contexto de request se disponível
    try:
        from flask import request as req, has_request_context
        if has_request_context():
            ip = req.headers.get('X-Forwarded-For', req.remote_addr)
            ua = req.headers.get('User-Agent', 'N/A')[:200]
            detalhe_parts.append(f'IP: {ip}')
            detalhe_parts.append(f'User-Agent: {ua}')
    except Exception:
        pass

    # Capturar usuário se logado
    usuario = kw.pop('usuario', None)
    if usuario is None:
        try:
            from flask_login import current_user
            if current_user and current_user.is_authenticated:
                usuario = current_user.username
        except Exception:
            pass

    existing_detalhe = kw.pop('detalhe', None)
    if existing_detalhe:
        detalhe_parts.append(str(existing_detalhe))

    detalhe = '\n'.join(detalhe_parts) if detalhe_parts else None
    log('INFO', f'[AUDIT] {mensagem}', detalhe=detalhe, usuario=usuario, **kw)


def purge_old_logs(days: int = 30) -> int:
    """
    Remove logs mais antigos que `days` dias.
    Retorna o número de registros removidos.
    Chamado automaticamente pelo scheduler diário.
    """
    try:
        from .extensions import db
        from .models import AppLog
        from datetime import timedelta

        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        deleted = AppLog.query.filter(AppLog.criado_em < cutoff).delete()
        db.session.commit()
        if deleted:
            log_info(
                f'Limpeza automática: {deleted} log(s) removidos (mais de {days} dias).',
                origem='sistema'
            )
        return deleted
    except Exception:
        return 0
