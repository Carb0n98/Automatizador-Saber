"""
Utilitários compartilhados: migração automática de colunas e sistema de permissões.
"""
from functools import wraps
from flask import abort, flash, redirect, url_for, jsonify, request
from flask_login import current_user

# ─── Catálogo de permissões disponíveis no sistema ─────────────────────────
PERMISSIONS = {
    'efetuar_busca':           'Efetuar Buscas no SABER',
    'gerenciar_verificacoes':  'Gerenciar Verificações (marcar APTO, excluir)',
    'ver_mensagens':           'Ver Mensagens e Resumo Diário',
    'gerenciar_templates':     'Criar / Editar / Excluir Templates de Mensagem',
    'editar_credenciais_saber':'Alterar Credenciais do SABER (URL, usuário, senha)',
    'editar_telefone':         'Alterar Número de WhatsApp / Telefone',
    'gerenciar_usuarios':      'Gerenciar Usuários (somente admin)',
}


def require_perm(perm):
    """
    Decorator que exige uma permissão específica.
    - Em rotas JSON (API): retorna 403 JSON.
    - Em rotas normais: flash de erro + redirect para dashboard.
    """
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if not current_user.is_authenticated:
                abort(401)
            if not current_user.has_perm(perm):
                if request.is_json or request.path.startswith('/') and 'api' in request.path:
                    return jsonify({'status': 'erro', 'mensagem': 'Sem permissão para esta ação.'}), 403
                flash('Você não tem permissão para realizar esta ação.', 'danger')
                return redirect(url_for('dashboard.index'))
            return f(*args, **kwargs)
        return wrapper
    return decorator


def require_admin(f):
    """Decorator que exige is_admin=True."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated:
            abort(401)
        if not current_user.is_admin:
            if request.is_json or 'api' in request.path:
                return jsonify({'status': 'erro', 'mensagem': 'Acesso restrito a administradores.'}), 403
            flash('Acesso restrito a administradores.', 'danger')
            return redirect(url_for('dashboard.index'))
        return f(*args, **kwargs)
    return wrapper


# ─── Migração automática de colunas na tabela users ───────────────────────
def migrate_user_columns(db):
    """
    Adiciona colunas novas à tabela users se não existirem.
    Compatível com SQLite (ALTER TABLE ADD COLUMN).
    """
    from sqlalchemy import text
    new_columns = [
        ("is_admin",    "BOOLEAN DEFAULT 0 NOT NULL"),
        ("ativo",       "BOOLEAN DEFAULT 1 NOT NULL"),
        ("permissions", "TEXT DEFAULT '[]'"),
    ]
    try:
        with db.engine.connect() as conn:
            for col_name, col_def in new_columns:
                try:
                    conn.execute(text(f"ALTER TABLE users ADD COLUMN {col_name} {col_def}"))
                    conn.commit()
                    print(f'[MIGRATE] Coluna adicionada: users.{col_name}')
                except Exception:
                    pass  # Coluna já existe — ignorar
    except Exception as e:
        print(f'[MIGRATE] Aviso: {e}')
