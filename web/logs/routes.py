from flask import Blueprint, render_template, request, jsonify
from flask_login import login_required, current_user
from ..models import AppLog
from ..extensions import db
from ..utils import require_perm, hoje_local, now_local
from datetime import datetime, timezone, timedelta
from sqlalchemy import or_

logs_bp = Blueprint('logs', __name__, url_prefix='/logs')


# ─── Página principal ────────────────────────────────────────────────────────

@logs_bp.route('/')
@login_required
@require_perm('ver_logs')
def index():
    """Painel de logs — visível apenas para usuários com permissão ver_logs."""
    agora = now_local()

    # Resumo das últimas 24 horas para os cards de topo
    desde_24h = datetime.now(timezone.utc) - timedelta(hours=24)
    total_24h   = AppLog.query.filter(AppLog.criado_em >= desde_24h).count()
    erros_24h   = AppLog.query.filter(AppLog.criado_em >= desde_24h, AppLog.nivel == 'ERROR').count()
    warns_24h   = AppLog.query.filter(AppLog.criado_em >= desde_24h, AppLog.nivel == 'WARNING').count()
    info_24h    = AppLog.query.filter(AppLog.criado_em >= desde_24h, AppLog.nivel == 'INFO').count()

    # Origens distintas para o filtro
    origens = [r[0] for r in db.session.query(AppLog.origem).distinct().order_by(AppLog.origem).all()]

    return render_template('logs/index.html',
        active='logs',
        total_24h=total_24h,
        erros_24h=erros_24h,
        warns_24h=warns_24h,
        info_24h=info_24h,
        origens=origens,
        data_hora=agora.strftime('%d/%m/%Y %H:%M'),
    )


# ─── API JSON paginada com filtros ────────────────────────────────────────────

@logs_bp.route('/api')
@login_required
@require_perm('ver_logs')
def api_list():
    """Retorna logs paginados com filtros."""
    nivel    = request.args.get('nivel', '').strip().upper()
    origem   = request.args.get('origem', '').strip()
    q        = request.args.get('q', '').strip()
    data_de  = request.args.get('data_de', '').strip()
    data_ate = request.args.get('data_ate', '').strip()
    page     = max(1, int(request.args.get('page', 1)))
    per_page = min(max(1, int(request.args.get('per_page', 50))), 200)

    query = AppLog.query

    if nivel and nivel in ('DEBUG', 'INFO', 'WARNING', 'ERROR'):
        query = query.filter(AppLog.nivel == nivel)

    if origem:
        query = query.filter(AppLog.origem == origem)

    if q:
        like = f'%{q}%'
        query = query.filter(or_(
            AppLog.mensagem.ilike(like),
            AppLog.detalhe.ilike(like),
            AppLog.usuario.ilike(like),
        ))

    if data_de:
        try:
            d = datetime.strptime(data_de, '%Y-%m-%d').replace(tzinfo=timezone.utc)
            query = query.filter(AppLog.criado_em >= d)
        except ValueError:
            pass

    if data_ate:
        try:
            d = datetime.strptime(data_ate, '%Y-%m-%d').replace(
                hour=23, minute=59, second=59, tzinfo=timezone.utc)
            query = query.filter(AppLog.criado_em <= d)
        except ValueError:
            pass

    total = query.count()
    items = query.order_by(AppLog.criado_em.desc()) \
                 .offset((page - 1) * per_page) \
                 .limit(per_page).all()

    return jsonify({
        'total': total,
        'page': page,
        'per_page': per_page,
        'pages': max(1, (total + per_page - 1) // per_page),
        'data': [l.to_dict() for l in items],
    })


# ─── API: Resumo rápido (polling do banner) ───────────────────────────────────

@logs_bp.route('/api/resumo')
@login_required
@require_perm('ver_logs')
def api_resumo():
    desde_24h = datetime.now(timezone.utc) - timedelta(hours=24)
    return jsonify({
        'total_24h': AppLog.query.filter(AppLog.criado_em >= desde_24h).count(),
        'erros_24h': AppLog.query.filter(AppLog.criado_em >= desde_24h, AppLog.nivel == 'ERROR').count(),
        'warns_24h': AppLog.query.filter(AppLog.criado_em >= desde_24h, AppLog.nivel == 'WARNING').count(),
        'info_24h':  AppLog.query.filter(AppLog.criado_em >= desde_24h, AppLog.nivel == 'INFO').count(),
    })


# ─── API: Purge manual (admin only) ──────────────────────────────────────────

@logs_bp.route('/api/purge', methods=['POST'])
@login_required
def api_purge():
    if not current_user.is_admin:
        return jsonify({'status': 'erro', 'mensagem': 'Acesso restrito a administradores.'}), 403

    data = request.get_json() or {}
    days = int(data.get('days', 30))
    days = max(1, min(days, 365))

    from ..logger import purge_old_logs, log_info
    removed = purge_old_logs(days)
    log_info(
        f'Purge manual: {removed} log(s) removidos (período: {days} dias) por {current_user.username}.',
        origem='sistema', usuario=current_user.username
    )
    return jsonify({'status': 'ok', 'removidos': removed, 'mensagem': f'{removed} log(s) removidos.'})


# ─── API: Ingestão de erros do frontend (JavaScript) ─────────────────────────

@logs_bp.route('/api/frontend', methods=['POST'])
@login_required
def api_frontend_log():
    """Recebe erros JS do frontend e os persiste como logs de origem 'frontend'."""
    data = request.get_json() or {}
    msg     = str(data.get('mensagem', 'Erro JS sem descrição'))[:500]
    detalhe = str(data.get('detalhe', ''))[:2000] or None
    nivel   = data.get('nivel', 'ERROR').upper()
    if nivel not in ('DEBUG', 'INFO', 'WARNING', 'ERROR'):
        nivel = 'ERROR'

    from ..logger import log
    log(nivel, msg, origem='frontend', detalhe=detalhe,
        usuario=current_user.username if current_user.is_authenticated else None)
    return jsonify({'status': 'ok'})
