from flask import Blueprint, render_template, request, jsonify, flash, redirect, url_for
from flask_login import login_required
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
    }
    return render_template('configuracoes/index.html',
        active='configuracoes',
        config=campos,
    )


@configuracoes_bp.route('/salvar', methods=['POST'])
@login_required
def salvar():
    campos = ['saber_url', 'saber_usuario', 'saber_senha', 'restaurante', 'telefone']

    for campo in campos:
        valor = request.form.get(campo, '').strip()
        Config.set(campo, valor)

    db.session.commit()

    return jsonify({'status': 'ok', 'mensagem': 'Configurações salvas com sucesso.'})


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
