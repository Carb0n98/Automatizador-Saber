from flask import Blueprint, render_template, redirect, url_for, request, flash, jsonify
from flask_login import login_user, logout_user, login_required, current_user
from ..models import User
from ..extensions import db, bcrypt
from ..logger import log_audit, log_warn

auth_bp = Blueprint('auth', __name__, url_prefix='/auth')


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard.index'))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')

        if not username or not password:
            flash('Preencha todos os campos.', 'danger')
            return render_template('auth/login.html')

        user = User.query.filter_by(username=username).first()

        if user:
            # Verificar bloqueio por brute-force
            if user.is_bloqueado():
                minutos_restantes = 15  # tempo fixo de bloqueio
                flash(f'Conta bloqueada temporariamente. Tente novamente em {minutos_restantes} minutos.', 'danger')
                log_warn(
                    f'Tentativa de login em conta bloqueada: {username}',
                    origem='auth'
                )
                db.session.commit()
                return render_template('auth/login.html')

            # Verificar se conta está ativa
            if not user.ativo:
                flash('Esta conta está desativada. Contate o administrador.', 'danger')
                log_warn(f'Tentativa de login em conta desativada: {username}', origem='auth')
                return render_template('auth/login.html')

            # Verificar senha
            if bcrypt.check_password_hash(user.password_hash, password):
                user.registrar_login_sucesso()
                db.session.commit()
                login_user(user, remember=True)
                log_audit(f'Login bem-sucedido: {username}', origem='auth')

                next_page = request.args.get('next')
                return redirect(next_page or url_for('dashboard.index'))
            else:
                # Senha incorreta — incrementar tentativas
                user.registrar_login_falho()
                db.session.commit()

                tentativas_restantes = max(0, 5 - (user.tentativas_login or 0))
                if user.is_bloqueado():
                    flash('Conta bloqueada por excesso de tentativas. Aguarde 15 minutos.', 'danger')
                elif tentativas_restantes <= 2:
                    flash(f'Senha incorreta. {tentativas_restantes} tentativa(s) restante(s).', 'danger')
                else:
                    flash('Usuário ou senha incorretos.', 'danger')

                log_warn(
                    f'Login falhou para {username} (tentativa #{user.tentativas_login})',
                    origem='auth'
                )
        else:
            # Usuário não existe — mensagem genérica para não revelar se o user existe
            flash('Usuário ou senha incorretos.', 'danger')
            log_warn(f'Tentativa de login com usuário inexistente: {username}', origem='auth')

    return render_template('auth/login.html')


@auth_bp.route('/logout')
@login_required
def logout():
    username = current_user.username
    logout_user()
    log_audit(f'Logout: {username}', origem='auth')
    flash('Sessão encerrada com sucesso.', 'info')
    return redirect(url_for('auth.login'))


@auth_bp.route('/alterar-senha', methods=['POST'])
@login_required
def alterar_senha():
    """Permite que o usuário logado altere sua própria senha."""
    data = request.get_json() or {}
    senha_atual = data.get('senha_atual', '').strip()
    nova_senha = data.get('nova_senha', '').strip()
    confirmar = data.get('confirmar_senha', '').strip()

    if not senha_atual or not nova_senha or not confirmar:
        return jsonify({'status': 'erro', 'mensagem': 'Todos os campos são obrigatórios.'}), 400

    if nova_senha != confirmar:
        return jsonify({'status': 'erro', 'mensagem': 'Nova senha e confirmação não coincidem.'}), 400

    if len(nova_senha) < 6:
        return jsonify({'status': 'erro', 'mensagem': 'A nova senha deve ter no mínimo 6 caracteres.'}), 400

    if not bcrypt.check_password_hash(current_user.password_hash, senha_atual):
        return jsonify({'status': 'erro', 'mensagem': 'Senha atual incorreta.'}), 400

    current_user.password_hash = bcrypt.generate_password_hash(nova_senha).decode('utf-8')
    db.session.commit()
    log_audit(f'Senha alterada pelo próprio usuário: {current_user.username}', origem='auth')
    return jsonify({'status': 'ok', 'mensagem': 'Senha alterada com sucesso.'})
