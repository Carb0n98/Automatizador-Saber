"""
Scheduler APScheduler — executa a coleta diária às 07:00.
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
        name='Coleta Diária SABER',
        replace_existing=True,
        misfire_grace_time=3600,  # 1h grace if server was down at 07:00
    )

    _scheduler.start()
    atexit.register(lambda: _scheduler.shutdown(wait=False))
    print('[SCHEDULER] Coleta diária agendada para 07:00 (America/Sao_Paulo)')


def get_next_run():
    """Retorna a data/hora da próxima execução agendada."""
    job = _scheduler.get_job('coleta_diaria')
    if job and job.next_run_time:
        return job.next_run_time.strftime('%d/%m/%Y %H:%M')
    return 'N/A'


def get_scheduler_status():
    return 'ativa' if _scheduler.running else 'inativa'
