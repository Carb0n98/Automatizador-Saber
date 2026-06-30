from flask import Blueprint, render_template, redirect, url_for, request, flash, jsonify
from flask_login import login_user, logout_user, login_required, current_user
from datetime import datetime, timezone
from ..models import User
from ..extensions import db, bcrypt
from ..logger import log_audit, log_warn
import re

auth_bp = Blueprint('auth', __name__, url_prefix='/auth')


def _validar_politica_senha(senha):
    """
    Valida política de segurança da senha.
    Retorna (ok: bool, mensagem: str).
    """
    if len(senha) < 12:
        return False, 'A senha deve ter no mínimo 12 caracteres.'
    if not re.search(r'[A-Z]', senha):
        return False, 'A senha deve conter pelo menos uma letra maiúscula.'
    if not re.search(r'[a-z]', senha):
        return False, 'A senha deve conter pelo menos uma letra minúscula.'
    if not re.search(r'[0-9]', senha):
        return False, 'A senha deve conter pelo menos um número.'
    if not re.search(r'[!@#$%^&*?_\-+=~`|\\:;"\'<>,./()\[\]{}]', senha):
        return False, 'A senha deve conter pelo menos um caractere especial (!@#$%&*? etc).'
    return True, ''


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        # Se já logado mas precisa trocar senha, redirecionar para lá
        if getattr(current_user, 'must_change_password', False):
            return redirect(url_for('auth.forcar_troca_senha'))
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

                # Verificar se precisa trocar senha obrigatoriamente
                if user.must_change_password:
                    return redirect(url_for('auth.forcar_troca_senha'))

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


@auth_bp.route('/alterar-senha-obrigatoria')
@login_required
def forcar_troca_senha():
    """Tela de troca obrigatória de senha (primeiro acesso)."""
    if not current_user.must_change_password:
        return redirect(url_for('dashboard.index'))
    return render_template('auth/alterar_senha.html')


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

    # Validar política de segurança
    ok, msg = _validar_politica_senha(nova_senha)
    if not ok:
        return jsonify({'status': 'erro', 'mensagem': msg}), 400

    # Verificar senha atual
    if not bcrypt.check_password_hash(current_user.password_hash, senha_atual):
        return jsonify({'status': 'erro', 'mensagem': 'Senha atual incorreta.'}), 400

    # Impedir reutilização da senha temporária (nova = atual)
    if bcrypt.check_password_hash(current_user.password_hash, nova_senha):
        return jsonify({'status': 'erro', 'mensagem': 'A nova senha não pode ser igual à senha atual.'}), 400

    # Atualizar senha
    current_user.password_hash = bcrypt.generate_password_hash(nova_senha).decode('utf-8')
    current_user.must_change_password = False
    current_user.password_changed_at = datetime.now(timezone.utc)
    db.session.commit()

    is_first_change = data.get('is_first_change', False)
    if is_first_change:
        log_audit(
            f'Primeira alteração de senha realizada pelo usuário: {current_user.username}',
            origem='auth'
        )
    else:
        log_audit(
            f'Senha alterada pelo próprio usuário: {current_user.username}',
            origem='auth'
        )

    return jsonify({'status': 'ok', 'mensagem': 'Senha alterada com sucesso.'})
