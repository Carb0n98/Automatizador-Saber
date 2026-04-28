from flask import Blueprint, render_template, jsonify
from flask_login import login_required, current_user
import threading
from ..utils import require_perm, hoje_local, now_local

dashboard_bp = Blueprint('dashboard', __name__)


@dashboard_bp.route('/')
@login_required
def index():
    from ..models import Verificacao, LogAutomacao, Config
    from ..extensions import db
    from sqlalchemy import extract

    hoje = hoje_local()
    agora = now_local()
    tz_nome = Config.get('timezone', 'America/Sao_Paulo')

    total_mes = Verificacao.query.filter(
        extract('month', Verificacao.criado_em) == hoje.month,
        extract('year', Verificacao.criado_em) == hoje.year
    ).count()

    aptos_mes = Verificacao.query.filter(
        Verificacao.status == 'apto',
        extract('month', Verificacao.data_verificacao) == hoje.month,
        extract('year', Verificacao.data_verificacao) == hoje.year
    ).count()

    progresso_pct = int((aptos_mes / total_mes) * 100) if total_mes > 0 else 0

    # Card "Hoje" dinâmico: apenas pendentes do dia
    nao_verificados_hoje = Verificacao.query.filter(
        Verificacao.data_verificacao == hoje,
        Verificacao.status == 'pendente'
    ).count()

    nao_verificados = Verificacao.query.filter_by(status='pendente').count()
    aptos = Verificacao.query.filter_by(status='apto').count()

    # Atrasados: pendentes com data anterior a hoje
    atrasados = Verificacao.query.filter(
        Verificacao.status == 'pendente',
        Verificacao.data_verificacao < hoje
    ).count()

    ultimos_logs = LogAutomacao.query.order_by(
        LogAutomacao.executado_em.desc()
    ).limit(8).all()

    ultimo_log = ultimos_logs[0] if ultimos_logs else None

    from ..scheduler import get_next_run, get_scheduler_status
    next_run = get_next_run()
    scheduler_status = get_scheduler_status()

    # Offset UTC em horas (ex: -3 para America/Sao_Paulo)
    tz_offset = int(agora.utcoffset().total_seconds() // 3600) if agora.utcoffset() else -3

    return render_template('dashboard/index.html',
        active='dashboard',
        total_mes=total_mes,
        aptos_mes=aptos_mes,
        progresso_pct=progresso_pct,
        nao_verificados_hoje=nao_verificados_hoje,
        nao_verificados=nao_verificados,
        aptos=aptos,
        atrasados=atrasados,
        ultimo_log=ultimo_log,
        ultimos_logs=ultimos_logs,
        next_run=next_run,
        scheduler_status=scheduler_status,
        tz_nome=tz_nome,
        data_hoje=hoje.strftime('%d/%m/%Y'),
        hora_atual=agora.strftime('%H:%M'),
        tz_offset=tz_offset,
    )


@dashboard_bp.route('/api/buscar', methods=['POST'])
@login_required
@require_perm('efetuar_busca')
def buscar_manual():
    from ..tasks import executar_coleta, is_running
    from flask import current_app

    if is_running():
        return jsonify({'status': 'ocupado', 'mensagem': 'Uma coleta já está em andamento. Aguarde.'})

    app = current_app._get_current_object()

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
