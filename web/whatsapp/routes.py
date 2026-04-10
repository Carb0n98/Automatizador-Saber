from flask import Blueprint, render_template, request, jsonify
from flask_login import login_required, current_user
from datetime import datetime, timezone, date
from ..models import WhatsappConfig, Verificacao
from ..extensions import db

whatsapp_bp = Blueprint('whatsapp', __name__, url_prefix='/whatsapp')


def _gerar_resumo() -> str:
    """Gera o texto de resumo diário de atrasados e pendentes — mesma lógica de mensagens."""
    hoje = date.today()

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
            cargo = v.cargo or 'Sem cargo'
            ativ  = v.atividade or 'Sem atividade'
            linhas.append(f'{v.nome}: {cargo} - {ativ} ({dt})')
        linhas.append('')

    if vencem_hoje:
        linhas.append('\U0001f4c5 HOJE')
        for v in vencem_hoje:
            cargo = v.cargo or 'Sem cargo'
            ativ  = v.atividade or 'Sem atividade'
            linhas.append(f'{v.nome}: {cargo} - {ativ}')

    resumo = '\n'.join(linhas).strip()
    return resumo if resumo else 'Nenhuma verificacao pendente ou atrasada hoje. \u2728'


# ─── Página principal ────────────────────────────────────────────────────

@whatsapp_bp.route('/')
@login_required
def index():
    from . import wa_client

    # Protecao: se a tabela ainda nao existir no banco antigo, cria sem crash
    try:
        cfg = WhatsappConfig.get_config()
    except Exception:
        db.create_all()
        cfg = WhatsappConfig.get_config()

    # Evolution API pode estar offline -- nunca deixar crashar a pagina
    try:
        wa_status = wa_client.get_status()
    except Exception:
        wa_status = {'connected': False, 'status': 'evolution_offline'}

    try:
        evo_ok = wa_client.is_evolution_online()
    except Exception:
        evo_ok = False

    resumo = _gerar_resumo()
    return render_template('whatsapp/index.html',
        active='whatsapp',
        cfg=cfg,
        wa_status=wa_status,
        resumo=resumo,
        evo_online=evo_ok,
    )


# ─── API: Status ─────────────────────────────────────────────────────────

@whatsapp_bp.route('/api/status')
@login_required
def api_status():
    from . import wa_client
    return jsonify(wa_client.get_status())


# ─── API: Conectar / QR Code ─────────────────────────────────────────────

@whatsapp_bp.route('/api/conectar', methods=['POST'])
@login_required
def api_conectar():
    from . import wa_client
    try:
        wa_client.ensure_instance()
        qr = wa_client.get_qr()
        return jsonify({'status': 'ok', 'qr': qr})
    except Exception as e:
        return jsonify({'status': 'erro', 'mensagem': str(e)}), 500


@whatsapp_bp.route('/api/qr')
@login_required
def api_qr():
    """Polling do QR code — chamado a cada 20s pela UI."""
    from . import wa_client
    try:
        status = wa_client.get_status()
        if status.get('connected'):
            return jsonify({'connected': True})
        qr = wa_client.get_qr()
        return jsonify({'connected': False, 'qr': qr})
    except Exception as e:
        return jsonify({'connected': False, 'error': str(e)}), 200


# ─── API: Desconectar ─────────────────────────────────────────────────────

@whatsapp_bp.route('/api/desconectar', methods=['POST'])
@login_required
def api_desconectar():
    from . import wa_client
    ok = wa_client.disconnect()
    return jsonify({'status': 'ok' if ok else 'erro'})


# ─── API: Listar chats / grupos ───────────────────────────────────────────

@whatsapp_bp.route('/api/chats')
@login_required
def api_chats():
    from . import wa_client
    try:
        chats = wa_client.get_chats()
        return jsonify({'status': 'ok', 'chats': chats})
    except Exception as e:
        return jsonify({'status': 'erro', 'mensagem': str(e), 'chats': []}), 200


# ─── API: Salvar configuração ─────────────────────────────────────────────

@whatsapp_bp.route('/api/salvar-config', methods=['POST'])
@login_required
def api_salvar_config():
    data = request.get_json()
    cfg = WhatsappConfig.get_config()
    cfg.destinatario_id   = (data.get('destinatario_id')   or '').strip()
    cfg.destinatario_nome = (data.get('destinatario_nome') or '').strip()
    cfg.destinatario_tipo = (data.get('destinatario_tipo') or '').strip()
    cfg.agendamento_ativo = bool(data.get('agendamento_ativo', False))
    cfg.horario_envio     = (data.get('horario_envio') or '08:00').strip()[:5]
    cfg.atualizado_em     = datetime.now(timezone.utc)
    db.session.commit()
    return jsonify({'status': 'ok', 'mensagem': 'Configuracoes salvas.'})


# ─── API: Resumo ─────────────────────────────────────────────────────────

@whatsapp_bp.route('/api/resumo')
@login_required
def api_resumo():
    return jsonify({'resumo': _gerar_resumo()})


# ─── API: Enviar agora ────────────────────────────────────────────────────

@whatsapp_bp.route('/api/enviar', methods=['POST'])
@login_required
def api_enviar():
    from . import wa_client
    data = request.get_json()
    to   = (data.get('to')    or '').strip()
    text = (data.get('texto') or '').strip()

    if not to:
        return jsonify({'status': 'erro', 'mensagem': 'Destinatario nao informado.'}), 400
    if not text:
        return jsonify({'status': 'erro', 'mensagem': 'Mensagem vazia.'}), 400

    status = wa_client.get_status()
    if not status.get('connected'):
        return jsonify({'status': 'erro', 'mensagem': 'WhatsApp nao esta conectado.'}), 400

    result = wa_client.send_text(to, text)
    if result.get('ok'):
        # Registra último envio
        cfg = WhatsappConfig.get_config()
        cfg.ultimo_envio  = datetime.now(timezone.utc)
        cfg.ultimo_status = 'enviado'
        db.session.commit()
        return jsonify({'status': 'ok', 'mensagem': 'Mensagem enviada com sucesso!'})
    else:
        return jsonify({'status': 'erro', 'mensagem': result.get('error', 'Falha no envio.')}), 500
