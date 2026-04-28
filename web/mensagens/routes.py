from flask import Blueprint, render_template, request, jsonify
from flask_login import login_required
from ..models import Mensagem, Verificacao
from ..extensions import db
from datetime import date

mensagens_bp = Blueprint('mensagens', __name__, url_prefix='/mensagens')


@mensagens_bp.route('/')
@login_required
def index():
    hoje = date.today()
    
    atrasados = Verificacao.query.filter(
        Verificacao.status == 'pendente',
        Verificacao.data_verificacao < hoje
    ).order_by(Verificacao.data_verificacao.asc()).all()
    
    vencem_hoje = Verificacao.query.filter(
        Verificacao.status == 'pendente',
        Verificacao.data_verificacao == hoje
    ).order_by(Verificacao.nome.asc()).all()

    linhas = []
    if atrasados:
        linhas.append("\U0001f6a8 ATRASADO")
        for v in atrasados:
            dt = v.data_verificacao.strftime('%d/%m/%Y') if v.data_verificacao else '?'
            # Fix #14: fallback para cargo e atividade vazios
            cargo = v.cargo or 'Sem cargo'
            ativ = v.atividade or 'Sem atividade'
            linhas.append(f"{v.nome}: {cargo} - {ativ} ({dt})")
        linhas.append("")

    if vencem_hoje:
        linhas.append("\U0001f4c5 N\u00c3O VERIFICADOS HOJE")
        for v in vencem_hoje:
            cargo = v.cargo or 'Sem cargo'
            ativ = v.atividade or 'Sem atividade'
            linhas.append(f"{v.nome}: {cargo} - {ativ}")
    
    resumo_texto = "\n".join(linhas).strip()
    if not resumo_texto:
        resumo_texto = "Nenhuma verificação pendente ou atrasada hoje. \u2728"

    templates = Mensagem.query.order_by(Mensagem.criado_em.desc()).all()
    return render_template('mensagens/index.html',
        active='mensagens',
        templates=templates,
        resumo_texto=resumo_texto
    )


@mensagens_bp.route('/api', methods=['GET'])
@login_required
def api_list():
    templates = Mensagem.query.order_by(Mensagem.criado_em.desc()).all()
    return jsonify([m.to_dict() for m in templates])


@mensagens_bp.route('/api', methods=['POST'])
@login_required
def api_criar():
    data = request.get_json()
    titulo = (data.get('titulo') or '').strip()
    conteudo = (data.get('conteudo') or '').strip()

    if not titulo or not conteudo:
        return jsonify({'error': 'Título e conteúdo são obrigatórios.'}), 400

    m = Mensagem(titulo=titulo, conteudo=conteudo)
    db.session.add(m)
    db.session.commit()
    return jsonify(m.to_dict()), 201


@mensagens_bp.route('/api/<int:mid>', methods=['PUT'])
@login_required
def api_editar(mid):
    # Fix #5: db.session.get() em vez de get_or_404 depreciado
    m = db.session.get(Mensagem, mid)
    if not m:
        return jsonify({'error': 'Template não encontrado.'}), 404
    data = request.get_json()
    m.titulo = (data.get('titulo') or m.titulo).strip()
    m.conteudo = (data.get('conteudo') or m.conteudo).strip()
    db.session.commit()
    return jsonify(m.to_dict())


@mensagens_bp.route('/api/<int:mid>', methods=['DELETE'])
@login_required
def api_excluir(mid):
    # Fix #5: db.session.get() em vez de get_or_404 depreciado
    m = db.session.get(Mensagem, mid)
    if not m:
        return jsonify({'error': 'Template não encontrado.'}), 404
    db.session.delete(m)
    db.session.commit()
    return jsonify({'status': 'ok'})
