from flask import Blueprint, render_template, request, jsonify
from flask_login import login_required, current_user
from ..models import User
from ..extensions import db, bcrypt
from ..utils import PERMISSIONS, require_admin
from ..logger import log_audit

import secrets
import string

usuarios_bp = Blueprint('usuarios', __name__, url_prefix='/usuarios')


def _gerar_senha_segura(tamanho=16):
    """
    Gera senha aleatória segura com pelo menos:
    - 2 maiúsculas, 2 minúsculas, 2 dígitos, 2 caracteres especiais.
    Comprimento mínimo: 12 caracteres.
    """
    tamanho = max(tamanho, 12)
    maiusculas = string.ascii_uppercase
    minusculas = string.ascii_lowercase
    digitos = string.digits
    especiais = '!@#$%&*?'

    # Garantir pelo menos 2 de cada categoria
    senha_chars = [
        secrets.choice(maiusculas), secrets.choice(maiusculas),
        secrets.choice(minusculas), secrets.choice(minusculas),
        secrets.choice(digitos), secrets.choice(digitos),
        secrets.choice(especiais), secrets.choice(especiais),
    ]
    # Preencher o restante com mix de todos
    todos = maiusculas + minusculas + digitos + especiais
    for _ in range(tamanho - len(senha_chars)):
        senha_chars.append(secrets.choice(todos))

    # Embaralhar para evitar padrão previsível
    resultado = list(senha_chars)
    secrets.SystemRandom().shuffle(resultado)
    return ''.join(resultado)


@usuarios_bp.route('/')
@login_required
@require_admin
def index():
    users = User.query.order_by(User.is_admin.desc(), User.username).all()
    return render_template('usuarios/index.html',
        active='usuarios',
        users=users,
        permissions=PERMISSIONS,
    )


@usuarios_bp.route('/api', methods=['POST'])
@login_required
@require_admin
def api_criar():
    data = request.get_json()
    username = (data.get('username') or '').strip()
    is_admin = bool(data.get('is_admin', False))
    perms = data.get('permissions', [])

    if not username:
        return jsonify({'status': 'erro', 'mensagem': 'Nome de usuário é obrigatório.'}), 400

    if User.query.filter_by(username=username).first():
        return jsonify({'status': 'erro', 'mensagem': 'Nome de usuário já existe.'}), 400

    # Gerar senha temporária segura automaticamente
    senha_temporaria = _gerar_senha_segura(16)

    u = User(
        username=username,
        password_hash=bcrypt.generate_password_hash(senha_temporaria).decode('utf-8'),
        is_admin=is_admin,
        ativo=True,
        must_change_password=True,
    )
    u.set_perms(perms)
    db.session.add(u)
    db.session.commit()

    log_audit(
        f'Usuário "{username}" criado com senha temporária (troca obrigatória)',
        origem='usuarios'
    )

    return jsonify({
        'status': 'ok',
        'mensagem': f'Usuário "{username}" criado com sucesso.',
        'user': u.to_dict(),
        'senha_temporaria': senha_temporaria,
    }), 201


@usuarios_bp.route('/api/<int:uid>', methods=['PUT'])
@login_required
@require_admin
def api_editar(uid):
    u = db.session.get(User, uid)
    if not u:
        return jsonify({'status': 'erro', 'mensagem': 'Usuário não encontrado.'}), 404

    # Não permite rebaixar o único admin
    if u.is_admin and not request.get_json().get('is_admin', True):
        admins = User.query.filter_by(is_admin=True).count()
        if admins <= 1:
            return jsonify({'status': 'erro', 'mensagem': 'Não é possível remover o único administrador.'}), 400

    data = request.get_json()
    username = (data.get('username') or '').strip()
    password = (data.get('password') or '').strip()
    is_admin = bool(data.get('is_admin', u.is_admin))
    ativo = bool(data.get('ativo', u.ativo))
    perms = data.get('permissions', u.get_perms())

    if username and username != u.username:
        if User.query.filter_by(username=username).first():
            return jsonify({'status': 'erro', 'mensagem': 'Nome de usuário já em uso.'}), 400
        u.username = username

    if password:
        u.password_hash = bcrypt.generate_password_hash(password).decode('utf-8')

    # Impede desativar a própria conta
    if u.id == current_user.id and not ativo:
        return jsonify({'status': 'erro', 'mensagem': 'Você não pode desativar sua própria conta.'}), 400

    u.is_admin = is_admin
    u.ativo = ativo
    u.set_perms([] if is_admin else perms)  # Admin não precisa de perms explícitas
    db.session.commit()
    return jsonify({'status': 'ok', 'mensagem': f'Usuário "{u.username}" atualizado.', 'user': u.to_dict()})


@usuarios_bp.route('/api/<int:uid>/reset-senha', methods=['POST'])
@login_required
@require_admin
def api_reset_senha(uid):
    """Gera nova senha temporária e redefine must_change_password=True."""
    u = db.session.get(User, uid)
    if not u:
        return jsonify({'status': 'erro', 'mensagem': 'Usuário não encontrado.'}), 404

    nova_senha = _gerar_senha_segura(16)
    u.password_hash = bcrypt.generate_password_hash(nova_senha).decode('utf-8')
    u.must_change_password = True
    u.password_changed_at = None
    db.session.commit()

    log_audit(
        f'Senha temporária redefinida pelo admin para usuário "{u.username}" (troca obrigatória)',
        origem='usuarios'
    )

    return jsonify({
        'status': 'ok',
        'mensagem': f'Nova senha temporária gerada para "{u.username}".',
        'senha_temporaria': nova_senha,
        'user': u.to_dict(),
    })


@usuarios_bp.route('/api/<int:uid>', methods=['DELETE'])
@login_required
@require_admin
def api_excluir(uid):
    u = db.session.get(User, uid)
    if not u:
        return jsonify({'status': 'erro', 'mensagem': 'Usuário não encontrado.'}), 404

    if u.id == current_user.id:
        return jsonify({'status': 'erro', 'mensagem': 'Você não pode excluir sua própria conta.'}), 400

    if u.is_admin and User.query.filter_by(is_admin=True).count() <= 1:
        return jsonify({'status': 'erro', 'mensagem': 'Não é possível excluir o único administrador.'}), 400

    nome = u.username
    db.session.delete(u)
    db.session.commit()
    return jsonify({'status': 'ok', 'mensagem': f'Usuário "{nome}" excluído.'})
