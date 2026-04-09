from flask import Blueprint, render_template, jsonify
from flask_login import login_required, current_user
from datetime import date
import threading
from ..utils import require_perm

dashboard_bp = Blueprint('dashboard', __name__)


@dashboard_bp.route('/')
@login_required
def index():
    from ..models import Verificacao, LogAutomacao
    from ..extensions import db
    from sqlalchemy import extract

    hoje = date.today()

    total_mes = Verificacao.query.filter(
        extract('month', Verificacao.criado_em) == hoje.month,
        extract('year', Verificacao.criado_em) == hoje.year
    ).count()

    aptos_mes = Verificacao.query.filter(
        Verificacao.status == 'apto',
        # Fix #10: usar data_verificacao (n\u00e3o criado_em) para o m\u00eas correto
        extract('month', Verificacao.data_verificacao) == hoje.month,
        extract('year', Verificacao.data_verificacao) == hoje.year
    ).count()

    progresso_pct = int((aptos_mes / total_mes) * 100) if total_mes > 0 else 0

    total_hoje = Verificacao.query.filter(
        Verificacao.data_verificacao == hoje
    ).count()

    pendentes = Verificacao.query.filter_by(status='pendente').count()
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

    return render_template('dashboard/index.html',
        active='dashboard',
        total_mes=total_mes,
        aptos_mes=aptos_mes,
        progresso_pct=progresso_pct,
        total_hoje=total_hoje,
        pendentes=pendentes,
        aptos=aptos,
        atrasados=atrasados,
        ultimo_log=ultimo_log,
        ultimos_logs=ultimos_logs,
        next_run=next_run,
        scheduler_status=scheduler_status,
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
