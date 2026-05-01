"""
Serviço de agregação de dados para análise mensal de verificações.

Todas as queries usam BETWEEN (data_inicio, data_fim) — amigável ao índice
ix_verif_data_status(data_verificacao, status). Nenhuma chamada a extract()
para evitar full-scans no SQLite.
"""
import calendar
from datetime import date
from typing import Dict, List, Optional, Tuple

from ..extensions import db
from ..models import Verificacao
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


# ─── Service principal ────────────────────────────────────────────────────────

def get_dados_mensais(ano: int, mes: int) -> Dict:
    """
    Retorna todas as métricas do mês para o dashboard de análise.

    Queries executadas (todas indexadas):
      1. Totais do mês (total, aptos, pendentes, atrasados)
      2. Totais do mês anterior (para delta)
      3. Breakdown por cargo (GROUP BY cargo)
      4. Distribuição por dia (GROUP BY data_verificacao)

    Returns:
        dict com: periodo, totais, vs_mes_anterior, por_cargo, por_dia
    """
    d_ini, d_fim = _mes_bounds(ano, mes)
    hoje = date.today()

    # ── 1. Totais do mês ──────────────────────────────────────────────────────
    q_mes = Verificacao.query.filter(
        Verificacao.data_verificacao >= d_ini,
        Verificacao.data_verificacao <= d_fim,
    )

    # Agregação única — conta total, aptos e pendentes em uma query
    row = db.session.query(
        func.count(Verificacao.id).label('total'),
        func.sum(
            case((Verificacao.status == 'apto', 1), else_=0)
        ).label('aptos'),
        func.sum(
            case((Verificacao.status == 'pendente', 1), else_=0)
        ).label('pendentes'),
    ).filter(
        Verificacao.data_verificacao >= d_ini,
        Verificacao.data_verificacao <= d_fim,
    ).first()

    total    = row.total    or 0
    aptos    = row.aptos    or 0
    pendentes= row.pendentes or 0

    # Atrasados: pendentes com data anterior a hoje (dentro do mês)
    atrasados = db.session.query(func.count(Verificacao.id)).filter(
        Verificacao.data_verificacao >= d_ini,
        Verificacao.data_verificacao < min(hoje, d_fim),  # até ontem ou fim do mês
        Verificacao.status == 'pendente',
    ).scalar() or 0

    pct_aptos = round((aptos / total) * 100, 1) if total > 0 else 0.0

    # ── 2. Totais do mês anterior (delta) ─────────────────────────────────────
    ano_ant, mes_ant = _mes_anterior(ano, mes)
    d_ini_ant, d_fim_ant = _mes_bounds(ano_ant, mes_ant)

    row_ant = db.session.query(
        func.count(Verificacao.id).label('total'),
        func.sum(
            case((Verificacao.status == 'apto', 1), else_=0)
        ).label('aptos'),
    ).filter(
        Verificacao.data_verificacao >= d_ini_ant,
        Verificacao.data_verificacao <= d_fim_ant,
    ).first()

    total_ant = row_ant.total or 0
    aptos_ant = row_ant.aptos or 0
    pct_ant   = round((aptos_ant / total_ant) * 100, 1) if total_ant > 0 else 0.0

    vs_mes_anterior = {
        'ano':         ano_ant,
        'mes':         mes_ant,
        'label':       f'{MESES_PT[mes_ant]} {ano_ant}',
        'total':       total_ant,
        'aptos':       aptos_ant,
        'pct_aptos':   pct_ant,
        'total_delta': total - total_ant,
        'aptos_delta': aptos - aptos_ant,
        'pct_delta':   round(pct_aptos - pct_ant, 1),
    }

    # ── 3. Breakdown por cargo ─────────────────────────────────────────────────
    rows_cargo = db.session.query(
        func.coalesce(Verificacao.cargo, 'Sem cargo').label('cargo'),
        func.count(Verificacao.id).label('total'),
        func.sum(
            case((Verificacao.status == 'apto', 1), else_=0)
        ).label('aptos'),
    ).filter(
        Verificacao.data_verificacao >= d_ini,
        Verificacao.data_verificacao <= d_fim,
    ).group_by(
        func.coalesce(Verificacao.cargo, 'Sem cargo')
    ).order_by(func.count(Verificacao.id).desc()).all()

    por_cargo = [
        {
            'cargo':     r.cargo,
            'total':     r.total,
            'aptos':     r.aptos or 0,
            'pendentes': r.total - (r.aptos or 0),
            'pct':       round(((r.aptos or 0) / r.total) * 100, 1) if r.total > 0 else 0.0,
        }
        for r in rows_cargo
    ]

    # ── 4. Distribuição por dia ────────────────────────────────────────────────
    rows_dia = db.session.query(
        Verificacao.data_verificacao.label('dia'),
        func.count(Verificacao.id).label('total'),
        func.sum(
            case((Verificacao.status == 'apto', 1), else_=0)
        ).label('aptos'),
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
            'dia':      d,
            'total':    dia_map[d].total   if d in dia_map else 0,
            'aptos':    (dia_map[d].aptos or 0) if d in dia_map else 0,
            'pendentes':(dia_map[d].total - (dia_map[d].aptos or 0)) if d in dia_map else 0,
        }
        for d in range(1, ultimo_dia + 1)
    ]

    return {
        'periodo': {
            'ano':    ano,
            'mes':    mes,
            'label':  f'{MESES_PT[mes]} {ano}',
            'inicio': d_ini.strftime('%d/%m/%Y'),
            'fim':    d_fim.strftime('%d/%m/%Y'),
        },
        'totais': {
            'total':     total,
            'aptos':     aptos,
            'pendentes': pendentes,
            'atrasados': atrasados,
            'pct_aptos': pct_aptos,
        },
        'vs_mes_anterior': vs_mes_anterior,
        'por_cargo':       por_cargo,
        'por_dia':         por_dia,
    }


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
