"""
Serviço de agregação de dados para análise mensal de verificações.

Todas as queries usam BETWEEN (data_inicio, data_fim) — amigável ao índice
ix_verif_data_status(data_verificacao, status). Nenhuma chamada a extract()
para evitar full-scans no SQLite.
"""
import calendar
from datetime import date, timedelta
from typing import Dict, List, Tuple

from ..extensions import db
from ..models import Verificacao
from ..constants import VERIFICADO_STATUSES
from sqlalchemy import func, case


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _mes_bounds(ano: int, mes: int) -> Tuple[date, date]:
    """Retorna (primeiro_dia, último_dia) do mês."""
    ultimo_dia = calendar.monthrange(ano, mes)[1]
    return date(ano, mes, 1), date(ano, mes, ultimo_dia)


def _mes_anterior(ano: int, mes: int) -> Tuple[int, int]:
    """Retorna (ano, mes) do mês anterior."""
    if mes == 1:
        return ano - 1, 12
    return ano, mes - 1


MESES_PT = [
    '', 'Janeiro', 'Fevereiro', 'Março', 'Abril', 'Maio', 'Junho',
    'Julho', 'Agosto', 'Setembro', 'Outubro', 'Novembro', 'Dezembro',
]

DIAS_SEMANA_PT = ['Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb', 'Dom']


# ─── Service principal ────────────────────────────────────────────────────────

def get_dados_mensais(ano: int, mes: int) -> Dict:
    """
    Retorna todas as métricas do mês para o dashboard de análise.

    Queries executadas (todas indexadas):
      1. Totais do mês (total, verificados, pendentes, atrasados)
      2. Totais do mês anterior (para delta)
      3. Breakdown por cargo (GROUP BY cargo)
      4. Distribuição por dia (GROUP BY data_verificacao)
      5. Indicadores de atrasos

    Returns:
        dict com: periodo, totais, vs_mes_anterior, por_cargo, por_dia, atrasos
    """
    d_ini, d_fim = _mes_bounds(ano, mes)
    hoje = date.today()

    # ── 1. Totais do mês ──────────────────────────────────────────────────────
    # Agregação única — conta total, verificados e pendentes em uma query
    row = db.session.query(
        func.count(Verificacao.id).label('total'),
        func.sum(
            case((Verificacao.status.in_(VERIFICADO_STATUSES), 1), else_=0)
        ).label('verificados'),
        func.sum(
            case((Verificacao.status == 'pendente', 1), else_=0)
        ).label('pendentes'),
    ).filter(
        Verificacao.data_verificacao >= d_ini,
        Verificacao.data_verificacao <= d_fim,
    ).first()

    total       = row.total       or 0
    verificados = row.verificados or 0
    pendentes   = row.pendentes   or 0

    # Atrasados: pendentes com data anterior a hoje (dentro do mês)
    atrasados = db.session.query(func.count(Verificacao.id)).filter(
        Verificacao.data_verificacao >= d_ini,
        Verificacao.data_verificacao < min(hoje, d_fim),  # até ontem ou fim do mês
        Verificacao.status == 'pendente',
    ).scalar() or 0

    pct_verificados = round((verificados / total) * 100, 1) if total > 0 else 0.0

    # ── 2. Totais do mês anterior (delta) ─────────────────────────────────────
    ano_ant, mes_ant = _mes_anterior(ano, mes)
    d_ini_ant, d_fim_ant = _mes_bounds(ano_ant, mes_ant)

    row_ant = db.session.query(
        func.count(Verificacao.id).label('total'),
        func.sum(
            case((Verificacao.status.in_(VERIFICADO_STATUSES), 1), else_=0)
        ).label('verificados'),
    ).filter(
        Verificacao.data_verificacao >= d_ini_ant,
        Verificacao.data_verificacao <= d_fim_ant,
    ).first()

    total_ant       = row_ant.total       or 0
    verificados_ant = row_ant.verificados or 0
    pct_ant = round((verificados_ant / total_ant) * 100, 1) if total_ant > 0 else 0.0

    vs_mes_anterior = {
        'ano':               ano_ant,
        'mes':               mes_ant,
        'label':             f'{MESES_PT[mes_ant]} {ano_ant}',
        'total':             total_ant,
        'verificados':       verificados_ant,
        'pct_verificados':   pct_ant,
        'total_delta':       total - total_ant,
        'verificados_delta': verificados - verificados_ant,
        'pct_delta':         round(pct_verificados - pct_ant, 1),
    }

    # ── 3. Breakdown por cargo ─────────────────────────────────────────────────
    rows_cargo = db.session.query(
        func.coalesce(Verificacao.cargo, 'Sem cargo').label('cargo'),
        func.count(Verificacao.id).label('total'),
        func.sum(
            case((Verificacao.status.in_(VERIFICADO_STATUSES), 1), else_=0)
        ).label('verificados'),
    ).filter(
        Verificacao.data_verificacao >= d_ini,
        Verificacao.data_verificacao <= d_fim,
    ).group_by(
        func.coalesce(Verificacao.cargo, 'Sem cargo')
    ).order_by(func.count(Verificacao.id).desc()).all()

    por_cargo = [
        {
            'cargo':       r.cargo,
            'total':       r.total,
            'verificados': r.verificados or 0,
            'pendentes':   r.total - (r.verificados or 0),
            'pct':         round(((r.verificados or 0) / r.total) * 100, 1) if r.total > 0 else 0.0,
        }
        for r in rows_cargo
    ]

    # ── 4. Distribuição por dia ────────────────────────────────────────────────
    rows_dia = db.session.query(
        Verificacao.data_verificacao.label('dia'),
        func.count(Verificacao.id).label('total'),
        func.sum(
            case((Verificacao.status.in_(VERIFICADO_STATUSES), 1), else_=0)
        ).label('verificados'),
    ).filter(
        Verificacao.data_verificacao >= d_ini,
        Verificacao.data_verificacao <= d_fim,
    ).group_by(
        Verificacao.data_verificacao
    ).order_by(Verificacao.data_verificacao).all()

    # Mapa dia→dados para preencher todos os dias do mês
    ultimo_dia = calendar.monthrange(ano, mes)[1]
    dia_map = {r.dia.day: r for r in rows_dia}
    por_dia = [
        {
            'dia':         d,
            'total':       dia_map[d].total        if d in dia_map else 0,
            'verificados': (dia_map[d].verificados or 0) if d in dia_map else 0,
            'pendentes':   (dia_map[d].total - (dia_map[d].verificados or 0)) if d in dia_map else 0,
        }
        for d in range(1, ultimo_dia + 1)
    ]

    # ── 5. Indicadores de Atrasos ──────────────────────────────────────────────
    atrasos = _calcular_atrasos(ano, mes, d_ini, d_fim, hoje, total, por_dia)

    return {
        'periodo': {
            'ano':    ano,
            'mes':    mes,
            'label':  f'{MESES_PT[mes]} {ano}',
            'inicio': d_ini.strftime('%d/%m/%Y'),
            'fim':    d_fim.strftime('%d/%m/%Y'),
        },
        'totais': {
            'total':           total,
            'verificados':     verificados,
            'pendentes':       pendentes,
            'atrasados':       atrasados,
            'pct_verificados': pct_verificados,
        },
        'vs_mes_anterior': vs_mes_anterior,
        'por_cargo':       por_cargo,
        'por_dia':         por_dia,
        'atrasos':         atrasos,
    }


def _calcular_atrasos(ano: int, mes: int, d_ini: date, d_fim: date,
                       hoje: date, total_mes: int, por_dia: List[Dict]) -> Dict:
    """
    Calcula métricas detalhadas de atrasos para o mês.
    Atraso = registro com status 'pendente' e data_verificacao < hoje.
    """
    limite = min(hoje, d_fim)

    # Atrasos por dia (apenas dias com data < hoje dentro do mês)
    rows_atraso_dia = db.session.query(
        Verificacao.data_verificacao.label('dia'),
        func.count(Verificacao.id).label('qtd'),
    ).filter(
        Verificacao.data_verificacao >= d_ini,
        Verificacao.data_verificacao < limite,
        Verificacao.status == 'pendente',
    ).group_by(
        Verificacao.data_verificacao
    ).order_by(Verificacao.data_verificacao).all()

    total_atrasos = sum(r.qtd for r in rows_atraso_dia)

    # Dias úteis passados no mês (para média)
    dias_passados = max(1, (limite - d_ini).days)
    media_diaria = round(total_atrasos / dias_passados, 1)
    pct_do_total = round((total_atrasos / total_mes) * 100, 1) if total_mes > 0 else 0.0

    # Atrasos por dia com info de dia da semana
    atrasos_por_dia = []
    for r in rows_atraso_dia:
        dia_semana_idx = r.dia.weekday()  # 0=seg, 6=dom
        atrasos_por_dia.append({
            'dia':        r.dia.day,
            'data':       r.dia.strftime('%d/%m'),
            'qtd':        r.qtd,
            'dia_semana': DIAS_SEMANA_PT[dia_semana_idx],
        })

    # Ranking dos dias com mais atrasos (top 5)
    ranking_dias = sorted(atrasos_por_dia, key=lambda x: x['qtd'], reverse=True)[:5]

    # Atrasos por dia da semana
    por_dia_semana = {dia: 0 for dia in DIAS_SEMANA_PT}
    for r in atrasos_por_dia:
        por_dia_semana[r['dia_semana']] += r['qtd']

    # Detecção de picos (dias com atrasos > média + 1 desvio padrão)
    picos = []
    if atrasos_por_dia:
        qtds = [r['qtd'] for r in atrasos_por_dia]
        media = sum(qtds) / len(qtds) if qtds else 0
        import math
        variancia = sum((x - media) ** 2 for x in qtds) / len(qtds) if qtds else 0
        desvio = math.sqrt(variancia)
        limiar_pico = media + desvio
        picos = [r for r in atrasos_por_dia if r['qtd'] > limiar_pico]

    # Evolução semanal (atrasos por semana do mês)
    atrasos_por_semana = {}
    for r in atrasos_por_dia:
        semana = (r['dia'] - 1) // 7 + 1
        atrasos_por_semana[semana] = atrasos_por_semana.get(semana, 0) + r['qtd']

    # Insights automáticos
    insights = _gerar_insights(
        total_atrasos, media_diaria, atrasos_por_dia,
        atrasos_por_semana, por_dia_semana, pct_do_total
    )

    # Tendência: comparar primeira metade vs segunda metade do período
    tendencia = _calcular_tendencia(atrasos_por_dia)

    return {
        'total':          total_atrasos,
        'media_diaria':   media_diaria,
        'pct_do_total':   pct_do_total,
        'por_dia':        atrasos_por_dia,
        'ranking_dias':   ranking_dias,
        'por_dia_semana': por_dia_semana,
        'picos':          picos,
        'por_semana':     atrasos_por_semana,
        'tendencia':      tendencia,
        'insights':       insights,
    }


def _calcular_tendencia(atrasos_por_dia: List[Dict]) -> Dict:
    """Calcula tendência comparando primeira metade vs segunda metade."""
    if len(atrasos_por_dia) < 2:
        return {'direcao': 'estavel', 'variacao_pct': 0.0}

    meio = len(atrasos_por_dia) // 2
    primeira = sum(r['qtd'] for r in atrasos_por_dia[:meio])
    segunda = sum(r['qtd'] for r in atrasos_por_dia[meio:])

    if primeira == 0:
        variacao = 100.0 if segunda > 0 else 0.0
    else:
        variacao = round(((segunda - primeira) / primeira) * 100, 1)

    if variacao > 10:
        direcao = 'aumento'
    elif variacao < -10:
        direcao = 'reducao'
    else:
        direcao = 'estavel'

    return {'direcao': direcao, 'variacao_pct': variacao}


def _gerar_insights(total_atrasos: int, media_diaria: float,
                    atrasos_por_dia: List[Dict], atrasos_por_semana: Dict,
                    por_dia_semana: Dict, pct_do_total: float) -> Dict:
    """Gera análises textuais automáticas."""
    insights = {
        'dia_mais_atrasos': None,
        'semana_mais_critica': None,
        'dia_semana_pior': None,
        'resumo': '',
    }

    if not atrasos_por_dia:
        insights['resumo'] = 'Nenhum atraso registrado neste período. Excelente! 🎉'
        return insights

    # Dia com mais atrasos
    pior_dia = max(atrasos_por_dia, key=lambda x: x['qtd'])
    insights['dia_mais_atrasos'] = f"Dia {pior_dia['data']} ({pior_dia['dia_semana']}) com {pior_dia['qtd']} atraso(s)"

    # Semana mais crítica
    if atrasos_por_semana:
        semana_pior = max(atrasos_por_semana, key=atrasos_por_semana.get)
        insights['semana_mais_critica'] = f"Semana {semana_pior} com {atrasos_por_semana[semana_pior]} atraso(s)"

    # Dia da semana com mais atrasos
    dia_semana_vals = {k: v for k, v in por_dia_semana.items() if v > 0}
    if dia_semana_vals:
        pior_ds = max(dia_semana_vals, key=dia_semana_vals.get)
        insights['dia_semana_pior'] = f"{pior_ds} é o dia da semana com mais atrasos ({dia_semana_vals[pior_ds]})"

    # Resumo textual
    if pct_do_total > 30:
        insights['resumo'] = f'⚠️ {pct_do_total}% das verificações estão atrasadas. Ação urgente necessária.'
    elif pct_do_total > 15:
        insights['resumo'] = f'⚡ {pct_do_total}% de atrasos. Atenção recomendada.'
    elif total_atrasos > 0:
        insights['resumo'] = f'📊 {total_atrasos} atraso(s) registrado(s), média de {media_diaria}/dia.'
    else:
        insights['resumo'] = 'Nenhum atraso registrado neste período. Excelente! 🎉'

    return insights


def get_meses_disponiveis() -> List[Dict]:
    """
    Retorna lista de meses/anos que possuem dados, ordenada do mais recente.
    Usado para popular o seletor de mês na UI.
    """
    rows = db.session.query(
        func.strftime('%Y', Verificacao.data_verificacao).label('ano_str'),
        func.strftime('%m', Verificacao.data_verificacao).label('mes_str'),
    ).filter(
        Verificacao.data_verificacao.isnot(None)
    ).group_by(
        func.strftime('%Y', Verificacao.data_verificacao),
        func.strftime('%m', Verificacao.data_verificacao),
    ).order_by(
        func.strftime('%Y', Verificacao.data_verificacao).desc(),
        func.strftime('%m', Verificacao.data_verificacao).desc(),
    ).all()

    result = []
    for r in rows:
        try:
            ano = int(r.ano_str)
            mes = int(r.mes_str)
            result.append({
                'ano':   ano,
                'mes':   mes,
                'label': f'{MESES_PT[mes]} {ano}',
                'value': f'{ano}-{mes:02d}',
            })
        except Exception:
            pass
    return result
