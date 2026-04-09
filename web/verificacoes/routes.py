from flask import Blueprint, render_template, request, jsonify
from flask_login import login_required
from ..models import Verificacao
from ..extensions import db
from datetime import datetime, date

verificacoes_bp = Blueprint('verificacoes', __name__, url_prefix='/verificacoes')


@verificacoes_bp.route('/')
@login_required
def index():
    cargos = db.session.query(Verificacao.cargo).distinct().filter(
        Verificacao.cargo != None, Verificacao.cargo != ''
    ).order_by(Verificacao.cargo).all()

    cargos = [c[0] for c in cargos]

    return render_template('verificacoes/index.html',
        active='verificacoes',
        cargos=cargos,
    )


@verificacoes_bp.route('/api')
@login_required
def api_list():
    """API JSON com filtros para a tabela AJAX."""
    q = request.args.get('q', '').strip()
    cargo = request.args.get('cargo', '').strip()
    status = request.args.get('status', '').strip()
    data_de = request.args.get('data_de', '').strip()
    data_ate = request.args.get('data_ate', '').strip()
    page = max(1, int(request.args.get('page', 1)))
    per_page = min(max(1, int(request.args.get('per_page', 20))), 100)  # Fix #4: limite máximo 100

    query = Verificacao.query

    if q:
        query = query.filter(Verificacao.nome.ilike(f'%{q}%'))

    if cargo:
        query = query.filter(Verificacao.cargo == cargo)

    # Filtro especial 'atrasado': pendentes com data anterior a hoje
    if status == 'atrasado':
        hoje = date.today()
        query = query.filter(
            Verificacao.status == 'pendente',
            Verificacao.data_verificacao < hoje
        )
    elif status:
        query = query.filter(Verificacao.status == status)

    if data_de:
        try:
            d = datetime.strptime(data_de, '%Y-%m-%d').date()
            query = query.filter(Verificacao.data_verificacao >= d)
        except ValueError:
            pass  # formato inválido — filtro ignorado silenciosamente

    if data_ate:
        try:
            d = datetime.strptime(data_ate, '%Y-%m-%d').date()
            query = query.filter(Verificacao.data_verificacao <= d)
        except ValueError:
            pass  # formato inválido — filtro ignorado silenciosamente

    total = query.count()
    # Atrasados: ordenar do mais antigo ao mais recente (urgência)
    if status == 'atrasado':
        order = query.order_by(Verificacao.data_verificacao.asc())
    else:
        order = query.order_by(Verificacao.data_verificacao.desc(), Verificacao.criado_em.desc())

    items = order.offset((page - 1) * per_page).limit(per_page).all()

    return jsonify({
        'total': total,
        'page': page,
        'per_page': per_page,
        'pages': (total + per_page - 1) // per_page,
        'data': [v.to_dict() for v in items],
    })


@verificacoes_bp.route('/<int:vid>/marcar-apto', methods=['POST'])
@login_required
def marcar_apto(vid):
    # Fix #5: db.session.get() é o substituto correto para SQLAlchemy 2.x
    v = db.session.get(Verificacao, vid)
    if not v:
        return jsonify({'status': 'erro', 'mensagem': 'Registro não encontrado.'}), 404
    v.status = 'apto'
    db.session.commit()
    return jsonify({'status': 'ok', 'mensagem': f'{v.nome} marcado como APTO.'})


@verificacoes_bp.route('/<int:vid>/excluir', methods=['POST'])
@login_required
def excluir(vid):
    # Fix #5: db.session.get() em vez de get_or_404 depreciado
    v = db.session.get(Verificacao, vid)
    if not v:
        return jsonify({'status': 'erro', 'mensagem': 'Registro não encontrado.'}), 404
    db.session.delete(v)
    db.session.commit()
    return jsonify({'status': 'ok', 'mensagem': 'Registro excluído.'})
