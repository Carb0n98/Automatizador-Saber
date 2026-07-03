from flask import Blueprint, render_template, jsonify, request
from flask_login import login_required, current_user
import threading
from ..utils import require_perm, hoje_local, now_local

dashboard_bp = Blueprint('dashboard', __name__)


@dashboard_bp.route('/')
@login_required
def index():
    from ..models import Verificacao, LogAutomacao, Config
    from ..extensions import db
    from ..constants import VERIFICADO_STATUSES
    import calendar
    from datetime import date

    hoje = hoje_local()
    agora = now_local()
    tz_nome = Config.get('timezone', 'America/Sao_Paulo')

    # Suporte a filtro mensal via query param
    mes_param = request.args.get('mes', '').strip()
    if mes_param:
        try:
            parts = mes_param.split('-')
            ano_sel = int(parts[0])
            mes_sel = int(parts[1])
        except (ValueError, IndexError):
            ano_sel, mes_sel = hoje.year, hoje.month
    else:
        ano_sel, mes_sel = hoje.year, hoje.month

    # Limites do mês selecionado
    primeiro_dia = date(ano_sel, mes_sel, 1)
    ultimo_dia = date(ano_sel, mes_sel, calendar.monthrange(ano_sel, mes_sel)[1])

    # Total de registros no mês
    total_mes = Verificacao.query.filter(
        Verificacao.data_verificacao >= primeiro_dia,
        Verificacao.data_verificacao <= ultimo_dia,
    ).count()

    # Verificados no mês (apto + parcialmente_apto)
    verificados_mes = Verificacao.query.filter(
        Verificacao.status.in_(VERIFICADO_STATUSES),
        Verificacao.data_verificacao >= primeiro_dia,
        Verificacao.data_verificacao <= ultimo_dia,
    ).count()

    progresso_pct = int((verificados_mes / total_mes) * 100) if total_mes > 0 else 0

    # Card "Hoje" dinâmico: apenas pendentes do dia
    nao_verificados_hoje = Verificacao.query.filter(
        Verificacao.data_verificacao == hoje,
        Verificacao.status == 'pendente'
    ).count()

    # Não verificados no mês
    nao_verificados = Verificacao.query.filter(
        Verificacao.status == 'pendente',
        Verificacao.data_verificacao >= primeiro_dia,
        Verificacao.data_verificacao <= ultimo_dia,
    ).count()

    # Verificados no mês
    verificados = Verificacao.query.filter(
        Verificacao.status.in_(VERIFICADO_STATUSES),
        Verificacao.data_verificacao >= primeiro_dia,
        Verificacao.data_verificacao <= ultimo_dia,
    ).count()

    # Atrasados: pendentes com data anterior a hoje (dentro do mês)
    atrasados = Verificacao.query.filter(
        Verificacao.status == 'pendente',
        Verificacao.data_verificacao >= primeiro_dia,
        Verificacao.data_verificacao < hoje
    ).count()

    ultimos_logs = LogAutomacao.query.order_by(
        LogAutomacao.executado_em.desc()
    ).limit(8).all()

    ultimo_log = ultimos_logs[0] if ultimos_logs else None

    from ..scheduler import get_next_run, get_scheduler_status
    next_run = get_next_run()
    scheduler_status = get_scheduler_status()

    # Offset UTC
    tz_offset = int(agora.utcoffset().total_seconds() // 3600) if agora.utcoffset() else -3

    # Meses disponíveis para seletor
    from ..analise.services import get_meses_disponiveis, MESES_PT
    meses_disponiveis = get_meses_disponiveis()
    atual_str = f"{ano_sel}-{mes_sel:02d}"
    if not any(m['value'] == atual_str for m in meses_disponiveis):
        meses_disponiveis.insert(0, {
            'ano': ano_sel,
            'mes': mes_sel,
            'label': f'{MESES_PT[mes_sel]} {ano_sel}',
            'value': atual_str
        })

    # Sparkline: verificações por dia nos últimos 7 dias úteis do mês
    from sqlalchemy import func, case
    from datetime import timedelta

    DIAS_SEMANA = ['Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb', 'Dom']

    sparkline_data = []
    for i in range(6, -1, -1):
        dia = hoje - timedelta(days=i)
        if dia < primeiro_dia or dia > ultimo_dia:
            sparkline_data.append({
                'dia': dia.strftime('%d'),
                'weekday': DIAS_SEMANA[dia.weekday()],
                'date': dia.isoformat(),
                'total': 0,
                'verificados': 0,
                'progress': 0,
                'is_today': dia == hoje,
            })
            continue
        row = db.session.query(
            func.count(Verificacao.id).label('total'),
            func.sum(case((Verificacao.status.in_(VERIFICADO_STATUSES), 1), else_=0)).label('verificados'),
        ).filter(
            Verificacao.data_verificacao == dia,
        ).first()
        total_dia = row.total or 0
        verif_dia = int(row.verificados or 0)
        pct_dia = int((verif_dia / total_dia) * 100) if total_dia > 0 else 0
        sparkline_data.append({
            'dia': dia.strftime('%d'),
            'weekday': DIAS_SEMANA[dia.weekday()],
            'date': dia.isoformat(),
            'total': total_dia,
            'verificados': verif_dia,
            'progress': pct_dia,
            'is_today': dia == hoje,
        })

    # Saúde do sistema
    from ..models import AppLog
    from datetime import timezone as tz_module
    desde_24h = agora.astimezone(tz_module.utc).replace(tzinfo=None) - timedelta(hours=24)
    # Approximate UTC conversion for the query
    from datetime import datetime as dt_cls
    utc_24h = dt_cls.now(tz_module.utc) - timedelta(hours=24)
    erros_recentes = AppLog.query.filter(
        AppLog.criado_em >= utc_24h,
        AppLog.nivel == 'ERROR'
    ).count()

    return render_template('dashboard/index.html',
        active='dashboard',
        total_mes=total_mes,
        verificados_mes=verificados_mes,
        progresso_pct=progresso_pct,
        nao_verificados_hoje=nao_verificados_hoje,
        nao_verificados=nao_verificados,
        verificados=verificados,
        atrasados=atrasados,
        ultimo_log=ultimo_log,
        ultimos_logs=ultimos_logs,
        next_run=next_run,
        scheduler_status=scheduler_status,
        tz_nome=tz_nome,
        data_hoje=hoje.strftime('%d/%m/%Y'),
        hora_atual=agora.strftime('%H:%M'),
        tz_offset=tz_offset,
        meses_disponiveis=meses_disponiveis,
        mes_selecionado=atual_str,
        sparkline_data=sparkline_data,
        erros_recentes=erros_recentes,
    )



@dashboard_bp.route('/api/buscar', methods=['POST'])
@login_required
@require_perm('efetuar_busca')
def buscar_manual():
    from ..tasks import executar_coleta, is_running
    from flask import current_app
    from ..logger import log_audit

    if is_running():
        return jsonify({'status': 'ocupado', 'mensagem': 'Uma coleta já está em andamento. Aguarde.'})

    app = current_app._get_current_object()
    log_audit('Coleta manual iniciada.', origem='dashboard')

    def run():
        executar_coleta(app, 'manual')

    thread = threading.Thread(target=run, daemon=True)
    thread.start()

    return jsonify({'status': 'iniciado', 'mensagem': 'Coleta iniciada em segundo plano.'})


@dashboard_bp.route('/api/status')
@login_required
def get_status():
    from ..models import LogAutomacao
    from ..tasks import is_running
    from ..scheduler import get_next_run, get_scheduler_status

    ultimo_log = LogAutomacao.query.order_by(LogAutomacao.executado_em.desc()).first()

    return jsonify({
        'executando': is_running(),
        'scheduler': get_scheduler_status(),
        'proxima_execucao': get_next_run(),
        'ultimo_log': ultimo_log.to_dict() if ultimo_log else None,
    })
