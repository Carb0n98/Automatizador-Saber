from flask import Blueprint, render_template, request, jsonify
from flask_login import login_required
from datetime import date

from . import analise_bp
from .services import get_dados_mensais, get_meses_disponiveis, MESES_PT
from ..utils import require_perm


@analise_bp.route('/')
@login_required
@require_perm('ver_analise')
def index():
    """Página principal da análise mensal."""
    hoje = date.today()
    ano = int(request.args.get('ano', hoje.year))
    mes = int(request.args.get('mes', hoje.month))
    
    meses_disponiveis = get_meses_disponiveis()
    
    # Garantir que o mês atual/selecionado apareça no dropdown mesmo se não houver dados ainda
    atual_str = f"{ano}-{mes:02d}"
    if not any(m['value'] == atual_str for m in meses_disponiveis):
        meses_disponiveis.insert(0, {
            'ano': ano,
            'mes': mes,
            'label': f'{MESES_PT[mes]} {ano}',
            'value': atual_str
        })
        # Re-ordenar (descendente)
        meses_disponiveis.sort(key=lambda x: (x['ano'], x['mes']), reverse=True)
        
    return render_template(
        'analise/index.html',
        active='analise',
        ano=ano,
        mes=mes,
        meses_disponiveis=meses_disponiveis
    )


@analise_bp.route('/api/mensal')
@login_required
@require_perm('ver_analise')
def api_mensal():
    """API que retorna os dados do mês selecionado para montar o dashboard."""
    try:
        hoje = date.today()
        ano = int(request.args.get('ano', hoje.year))
        mes = int(request.args.get('mes', hoje.month))
        
        dados = get_dados_mensais(ano, mes)
        return jsonify({'status': 'ok', 'data': dados})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'status': 'erro', 'mensagem': str(e)}), 500
