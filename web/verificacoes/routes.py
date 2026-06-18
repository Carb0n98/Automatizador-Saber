from flask import Blueprint, render_template, request, jsonify
from flask_login import login_required, current_user
from ..models import Verificacao
from ..extensions import db
from datetime import datetime, date
import calendar
from ..utils import require_perm, hoje_local
from ..logger import log_audit

verificacoes_bp = Blueprint('verificacoes', __name__, url_prefix='/verificacoes')


@verificacoes_bp.route('/')
@login_required
def index():
    cargos = db.session.query(Verificacao.cargo).distinct().filter(
        Verificacao.cargo != None, Verificacao.cargo != ''
    ).order_by(Verificacao.cargo).all()

    cargos = [c[0] for c in cargos]

    # Meses disponíveis para o seletor
    from ..analise.services import get_meses_disponiveis, MESES_PT
    meses_disponiveis = get_meses_disponiveis()

    hoje = hoje_local()
    atual_str = f"{hoje.year}-{hoje.month:02d}"
    if not any(m['value'] == atual_str for m in meses_disponiveis):
        meses_disponiveis.insert(0, {
            'ano': hoje.year,
            'mes': hoje.month,
            'label': f'{MESES_PT[hoje.month]} {hoje.year}',
            'value': atual_str
        })

    return render_template('verificacoes/index.html',
        active='verificacoes',
        cargos=cargos,
        can_manage=current_user.has_perm('gerenciar_verificacoes'),
        meses_disponiveis=meses_disponiveis,
        mes_atual=atual_str,
    )


@verificacoes_bp.route('/api')
@login_required
def api_list():
    """API JSON com filtros para a tabela AJAX, incluindo filtro mensal."""
    q = request.args.get('q', '').strip()
    cargo = request.args.get('cargo', '').strip()
    status = request.args.get('status', '').strip()
    data_de = request.args.get('data_de', '').strip()
    data_ate = request.args.get('data_ate', '').strip()
    mes = request.args.get('mes', '').strip()  # formato: YYYY-MM
    page = max(1, int(request.args.get('page', 1)))
    per_page = min(max(1, int(request.args.get('per_page', 20))), 100)

    query = Verificacao.query

    # ─── Filtro mensal (prioridade sobre data_de/data_ate) ───
    if mes:
        try:
            parts = mes.split('-')
            ano_f = int(parts[0])
            mes_f = int(parts[1])
            primeiro_dia = date(ano_f, mes_f, 1)
            ultimo_dia = date(ano_f, mes_f, calendar.monthrange(ano_f, mes_f)[1])
            query = query.filter(
                Verificacao.data_verificacao >= primeiro_dia,
                Verificacao.data_verificacao <= ultimo_dia,
            )
        except (ValueError, IndexError):
            pass  # Formato inválido — ignora silenciosamente
    elif not data_de and not data_ate:
        # Default: mês atual quando nenhum filtro de data especificado
        hoje = hoje_local()
        primeiro_dia = date(hoje.year, hoje.month, 1)
        ultimo_dia = date(hoje.year, hoje.month, calendar.monthrange(hoje.year, hoje.month)[1])
        query = query.filter(
            Verificacao.data_verificacao >= primeiro_dia,
            Verificacao.data_verificacao <= ultimo_dia,
        )

    if q:
        query = query.filter(Verificacao.nome.ilike(f'%{q}%'))

    if cargo:
        query = query.filter(Verificacao.cargo == cargo)

    # Filtro especial 'atrasado': pendentes com data anterior a hoje
    if status == 'atrasado':
        hoje = hoje_local()
        query = query.filter(
            Verificacao.status == 'pendente',
            Verificacao.data_verificacao < hoje
        )
    elif status == 'verificado':
        from ..constants import VERIFICADO_STATUSES
        query = query.filter(Verificacao.status.in_(VERIFICADO_STATUSES))
    elif status:
        query = query.filter(Verificacao.status == status)

    if data_de:
        try:
            d = datetime.strptime(data_de, '%Y-%m-%d').date()
            query = query.filter(Verificacao.data_verificacao >= d)
        except ValueError:
            pass

    if data_ate:
        try:
            d = datetime.strptime(data_ate, '%Y-%m-%d').date()
            query = query.filter(Verificacao.data_verificacao <= d)
        except ValueError:
            pass

    total = query.count()
    # Atrasados: ordenar do mais antigo ao mais recente (urgência)
    if status == 'atrasado':
        order = query.order_by(Verificacao.data_verificacao.asc())
    else:
        order = query.order_by(Verificacao.data_verificacao.desc(), Verificacao.criado_em.desc())

    items = order.offset((page - 1) * per_page).limit(per_page).all()

    # Métricas rápidas para o mês filtrado
    from ..constants import VERIFICADO_STATUSES
    total_verificados = query.filter(Verificacao.status.in_(VERIFICADO_STATUSES)).count() if total > 0 else 0

    return jsonify({
        'total': total,
        'page': page,
        'per_page': per_page,
        'pages': max(1, (total + per_page - 1) // per_page),
        'total_verificados': total_verificados,
        'data': [v.to_dict() for v in items],
    })


@verificacoes_bp.route('/<int:vid>/marcar-apto', methods=['POST'])
@verificacoes_bp.route('/<int:vid>/marcar-verificado', methods=['POST'])
@login_required
@require_perm('gerenciar_verificacoes')
def marcar_apto(vid):
    v = db.session.get(Verificacao, vid)
    if not v:
        return jsonify({'status': 'erro', 'mensagem': 'Registro não encontrado.'}), 404
    status_anterior = v.status
    v.status = 'apto'
    db.session.commit()
    log_audit(
        f'Verificação marcada como APTO: {v.nome} ({v.data_verificacao}) '
        f'[{status_anterior} → apto]',
        origem='verificacoes'
    )
    return jsonify({'status': 'ok', 'mensagem': f'{v.nome} marcado como VERIFICADO.'})


@verificacoes_bp.route('/<int:vid>/excluir', methods=['POST'])
@login_required
@require_perm('gerenciar_verificacoes')
def excluir(vid):
    v = db.session.get(Verificacao, vid)
    if not v:
        return jsonify({'status': 'erro', 'mensagem': 'Registro não encontrado.'}), 404
    nome = v.nome
    data = v.data_verificacao
    db.session.delete(v)
    db.session.commit()
    log_audit(f'Verificação excluída: {nome} ({data})', origem='verificacoes')
    return jsonify({'status': 'ok', 'mensagem': 'Registro excluído.'})
