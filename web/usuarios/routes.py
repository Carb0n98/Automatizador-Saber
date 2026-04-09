from flask import Blueprint, render_template, request, jsonify
from flask_login import login_required, current_user
from ..models import User
from ..extensions import db, bcrypt
from ..utils import PERMISSIONS, require_admin

usuarios_bp = Blueprint('usuarios', __name__, url_prefix='/usuarios')


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
    password = (data.get('password') or '').strip()
    is_admin = bool(data.get('is_admin', False))
    perms = data.get('permissions', [])

    if not username or not password:
        return jsonify({'status': 'erro', 'mensagem': 'Usuário e senha são obrigatórios.'}), 400

    if User.query.filter_by(username=username).first():
        return jsonify({'status': 'erro', 'mensagem': 'Nome de usuário já existe.'}), 400

    u = User(
        username=username,
        password_hash=bcrypt.generate_password_hash(password).decode('utf-8'),
        is_admin=is_admin,
        ativo=True,
    )
    u.set_perms(perms)
    db.session.add(u)
    db.session.commit()
    return jsonify({'status': 'ok', 'mensagem': f'Usuário "{username}" criado.', 'user': u.to_dict()}), 201


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
