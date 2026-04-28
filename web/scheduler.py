"""
Scheduler APScheduler — coleta diária às 07:00 + envio WhatsApp automático.
"""
import atexit
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

_scheduler = BackgroundScheduler(timezone='America/Sao_Paulo')


def init_scheduler(app):
    from .tasks import executar_coleta

    _scheduler.add_job(
        func=executar_coleta,
        args=[app, 'automatico'],
        trigger=CronTrigger(hour=7, minute=0),
        id='coleta_diaria',
        name='Coleta Diaria SABER',
        replace_existing=True,
        misfire_grace_time=3600,
    )

    # Job de envio WhatsApp — horário lido do banco a cada execução
    _scheduler.add_job(
        func=_whatsapp_job,
        args=[app],
        trigger=CronTrigger(minute='*'),   # roda todo minuto, valida o horário internamente
        id='whatsapp_envio',
        name='WhatsApp - Envio Automatico',
        replace_existing=True,
        misfire_grace_time=300,
    )

    _scheduler.start()
    atexit.register(lambda: _scheduler.shutdown(wait=False))
    print('[SCHEDULER] Coleta diaria agendada para 07:00 (America/Sao_Paulo)')
    print('[SCHEDULER] Job WhatsApp ativo (avalia horario configurado a cada minuto)')


def _whatsapp_job(app):
    """
    Envia o resumo diário via WhatsApp se:
    - agendamento_ativo == True
    - horário atual == horario_envio configurado
    - WhatsApp estiver conectado
    """
    from datetime import datetime
    import pytz

    tz  = pytz.timezone('America/Sao_Paulo')
    now = datetime.now(tz)
    hm  = now.strftime('%H:%M')

    with app.app_context():
        try:
            from .models import WhatsappConfig, Verificacao
            from .extensions import db
            from datetime import timezone

            cfg = WhatsappConfig.query.first()
            if not cfg:
                return
            if not cfg.agendamento_ativo:
                return
            if cfg.horario_envio != hm:
                return
            if not cfg.destinatario_id:
                print('[WA] Horario de envio atingido, mas nenhum destinatario configurado.')
                return

            # Gera resumo
            from .utils import hoje_local
            hoje = hoje_local()
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
                linhas.append('\U0001f6a8 ATRASADO')
                for v in atrasados:
                    dt = v.data_verificacao.strftime('%d/%m/%Y') if v.data_verificacao else '?'
                    linhas.append(f'{v.nome}: {v.cargo or "Sem cargo"} - {v.atividade or "Sem atividade"} ({dt})')
                linhas.append('')
            if vencem_hoje:
                linhas.append('\U0001f4c5 HOJE')
                for v in vencem_hoje:
                    linhas.append(f'{v.nome}: {v.cargo or "Sem cargo"} - {v.atividade or "Sem atividade"}')

            resumo = '\n'.join(linhas).strip()
            if not resumo:
                resumo = 'Nenhuma verificacao pendente ou atrasada hoje. \u2728'

            # Envia
            from .whatsapp import wa_client
            status = wa_client.get_status()
            if not status.get('connected'):
                print('[WA] Tentativa de envio automatico: WhatsApp nao conectado.')
                return

            result = wa_client.send_text(cfg.destinatario_id, resumo)
            cfg.ultimo_envio  = datetime.now(timezone.utc)
            if result.get('ok'):
                cfg.ultimo_status = 'enviado'
                print(f'[WA] Resumo enviado para {cfg.destinatario_nome} as {hm}.')
            elif result.get('needs_reconnect'):
                cfg.ultimo_status = 'sessao_expirada'
                print(f'[WA] AVISO: Sessão expirada. Reconecte o WhatsApp na aba correspondente.')
            else:
                cfg.ultimo_status = 'erro'
                print(f'[WA] Erro no envio: {result.get("error")}')
            db.session.commit()

        except Exception as e:
            print(f'[WA] Erro no job de envio automatico: {e}')


def get_next_run():
    """Retorna a data/hora da próxima execução da coleta agendada."""
    job = _scheduler.get_job('coleta_diaria')
    if job and job.next_run_time:
        return job.next_run_time.strftime('%d/%m/%Y %H:%M')
    return 'N/A'


def get_scheduler_status():
    return 'ativa' if _scheduler.running else 'inativa'
