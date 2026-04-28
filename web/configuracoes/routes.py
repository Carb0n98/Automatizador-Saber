from flask import Blueprint, render_template, request, jsonify, flash, redirect, url_for
from flask_login import login_required, current_user
from ..models import Config
from ..extensions import db

configuracoes_bp = Blueprint('configuracoes', __name__, url_prefix='/configuracoes')


@configuracoes_bp.route('/')
@login_required
def index():
    campos = {
        'saber_url': Config.get('saber_url', ''),
        'saber_usuario': Config.get('saber_usuario', ''),
        'saber_senha': Config.get('saber_senha', ''),
        'restaurante': Config.get('restaurante', ''),
        'telefone': Config.get('telefone', ''),
        'timezone': Config.get('timezone', 'America/Sao_Paulo'),
    }
    return render_template('configuracoes/index.html',
        active='configuracoes',
        config=campos,
        can_saber=current_user.has_perm('editar_credenciais_saber'),
        can_tel=current_user.has_perm('editar_telefone'),
    )


@configuracoes_bp.route('/salvar', methods=['POST'])
@login_required
def salvar():
    """Salva configurações — aplica guards por grupo de campos."""
    pode_saber = current_user.has_perm('editar_credenciais_saber')
    pode_tel   = current_user.has_perm('editar_telefone')

    campos_saber = ['saber_url', 'saber_usuario', 'saber_senha', 'restaurante', 'timezone']
    campos_tel   = ['telefone']

    salvou = False
    for campo in campos_saber:
        if pode_saber:
            Config.set(campo, request.form.get(campo, '').strip())
            salvou = True

    for campo in campos_tel:
        if pode_tel:
            Config.set(campo, request.form.get(campo, '').strip())
            salvou = True

    if not salvou:
        return jsonify({'status': 'erro', 'mensagem': 'Sem permissão para alterar as configurações.'}), 403

    db.session.commit()
    return jsonify({'status': 'ok', 'mensagem': 'Configurações salvas com sucesso.'})


@configuracoes_bp.route('/api/timezone-info')
@login_required
def api_timezone_info():
    """Retorna data/hora atual no fuso configurado (usado pelo relógio do dashboard)."""
    from ..utils import now_local
    from ..models import Config
    agora = now_local()
    tz_nome = Config.get('timezone', 'America/Sao_Paulo')
    offset = int(agora.utcoffset().total_seconds() // 3600) if agora.utcoffset() else -3
    return jsonify({
        'data': agora.strftime('%d/%m/%Y'),
        'hora': agora.strftime('%H:%M:%S'),
        'tz': tz_nome,
        'offset': offset,
    })


@configuracoes_bp.route('/api/testar', methods=['POST'])
@login_required
def testar_conexao():
    """Testa a conexão com o SABER (apenas verifica se a URL responde)."""
    import urllib.request
    url = Config.get('saber_url', '')
    try:
        urllib.request.urlopen(url, timeout=5)
        return jsonify({'status': 'ok', 'mensagem': 'Conexão com o SABER estabelecida.'})
    except Exception as e:
        return jsonify({'status': 'erro', 'mensagem': f'Falha na conexão: {str(e)}'}), 200
