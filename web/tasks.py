"""
Tarefa de coleta de dados do sistema SABER via Selenium.
Chamada pelo scheduler diário (07:00) e pelo botão de busca manual.

Correções implementadas:
- Deduplicação em memória para evitar INSERTs duplicados na mesma sessão
- Chave de identidade expandida: (nome, data_verificacao, atividade)
- Filtro de mês atual: ignora registros de meses anteriores na coleta automática
- Fallback de data removido: registros com data inválida são descartados com log
- Lock protegido com try/finally para evitar deadlock
- Logs detalhados de cada etapa da coleta
"""
from datetime import datetime
import threading

# Lock to prevent concurrent scraping runs
_scraping_lock = threading.Lock()
_is_running = False

# Selenium page load timeout (seconds)
_SELENIUM_TIMEOUT = 60


def get_mes_atual_pt():
    """Retorna o nome do mês atual em português."""
    meses = {
        1: 'Janeiro', 2: 'Fevereiro', 3: 'Março', 4: 'Abril',
        5: 'Maio', 6: 'Junho', 7: 'Julho', 8: 'Agosto',
        9: 'Setembro', 10: 'Outubro', 11: 'Novembro', 12: 'Dezembro'
    }
    return meses[datetime.now().month]


def is_running():
    return _is_running


def executar_coleta(app, origem='automatico'):
    """
    Executa a coleta de dados do SABER e salva no banco.
    Roda dentro do app context do Flask.
    """
    global _is_running

    # Usar o lock para evitar race condition
    if not _scraping_lock.acquire(blocking=False):
        return {'status': 'ocupado', 'mensagem': 'Uma coleta já está em andamento.'}

    _is_running = True

    try:
        return _executar_coleta_interno(app, origem)
    finally:
        _is_running = False
        _scraping_lock.release()


def _executar_coleta_interno(app, origem):
    """Lógica interna de coleta, protegida pelo lock externo."""

    with app.app_context():
        from .models import db, Config, Verificacao, LogAutomacao
        from .utils import hoje_local
        from .logger import log_info, log_error, log_warn, log_audit

        # Cria log de execução inicial
        log = LogAutomacao(
            status='executando',
            total_coletados=0,
            mensagem='Coleta iniciada...'
        )
        db.session.add(log)
        db.session.commit()
        log_id = log.id
        log_info('Coleta SABER iniciada.', origem='scheduler')

        driver = None

        try:
            url = Config.get('saber_url', 'https://adtalento.com/websiteSaber')
            usuario = Config.get('saber_usuario', '')
            senha = Config.get('saber_senha', '')
            restaurante = Config.get('restaurante', 'NPN')
            mes = get_mes_atual_pt()

            if not usuario or not senha:
                _finalizar_log(db, LogAutomacao, log_id, 'erro', 0,
                               'Credenciais não configuradas. Acesse Configurações e informe usuário/senha do SABER.')
                return {'status': 'erro', 'mensagem': 'Credenciais não configuradas'}

            # ──────────────── Selenium ────────────────
            import os
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options
            from selenium.webdriver.chrome.service import Service
            from selenium.webdriver.common.by import By
            from selenium.webdriver.support.ui import WebDriverWait, Select
            from selenium.webdriver.support import expected_conditions as EC

            options = Options()
            options.add_argument('--headless=new')
            options.add_argument('--no-sandbox')
            options.add_argument('--disable-dev-shm-usage')
            options.add_argument('--disable-gpu')
            options.add_argument('--window-size=1920,1080')
            options.add_argument('--disable-extensions')
            options.add_argument('--disable-software-rasterizer')

            # Suporte a Docker: usa CHROME_BIN se disponível
            chrome_bin = os.environ.get('CHROME_BIN')
            if chrome_bin:
                options.binary_location = chrome_bin

            # Suporte a Docker: usa CHROMEDRIVER_BIN se disponível
            chromedriver_bin = os.environ.get('CHROMEDRIVER_BIN')
            service = Service(executable_path=chromedriver_bin) if chromedriver_bin else Service()

            driver = webdriver.Chrome(service=service, options=options)
            driver.set_page_load_timeout(_SELENIUM_TIMEOUT)
            driver.set_script_timeout(30)
            wait = WebDriverWait(driver, 20)

            # Login
            driver.get(url)
            wait.until(EC.presence_of_element_located((By.ID, 'ContentPlaceHolder1_usuario'))).send_keys(usuario)
            driver.find_element(By.ID, 'ContentPlaceHolder1_password').send_keys(senha)
            driver.find_element(By.ID, 'ContentPlaceHolder1_btnLogin').click()

            # Navegação
            wait.until(EC.element_to_be_clickable((By.ID, 'ContentPlaceHolder1_LinkButton1'))).click()
            wait.until(EC.element_to_be_clickable((By.ID, 'ContentPlaceHolder1_BtnSistema'))).click()
            wait.until(EC.element_to_be_clickable((By.ID, 'ContentPlaceHolder1_tabPlanejamento'))).click()
            wait.until(EC.element_to_be_clickable((By.ID, 'ContentPlaceHolder1_btnResumen'))).click()

            # Filtros
            Select(wait.until(EC.presence_of_element_located(
                (By.ID, 'ContentPlaceHolder1_idrestaurante')
            ))).select_by_visible_text(restaurante)

            Select(driver.find_element(By.ID, 'ContentPlaceHolder1_fecharegistro')
                   ).select_by_visible_text(mes)

            driver.find_element(By.ID, 'ContentPlaceHolder1_btnConsulta').click()
            wait.until(EC.presence_of_element_located((By.TAG_NAME, 'table')))

            # ──────────────── Extração de dados (paginada) ────────────────
            todos_dados = []
            pagina = 0
            while True:
                pagina += 1
                tabela = wait.until(EC.presence_of_element_located((By.ID, 'example')))
                linhas = tabela.find_elements(By.CSS_SELECTOR, 'tbody tr')
                primeiro_nome = linhas[0].text if linhas else ''

                for linha in linhas:
                    colunas = linha.find_elements(By.TAG_NAME, 'td')
                    # Tabela do SABER tem 8 colunas (índices 0-7):
                    # [0]=restaurante [1]=nome [2]=cargo [3]=ícone-link
                    # [4]=data        [5]=ícone [6]=STATUS [7]=supervisor
                    if len(colunas) < 7:
                        continue
                    tem_icone = linha.find_elements(By.CSS_SELECTOR, 'i.fas.fa-external-link-square-alt')
                    if not tem_icone:
                        continue
                    # Status está no índice 6 (não 5 - que é apenas um ícone)
                    status_raw = colunas[6].text.strip().upper()
                    todos_dados.append({
                        'nome': colunas[1].text.strip(),
                        'cargo': colunas[2].text.strip(),
                        'atividade': colunas[3].text.strip(),
                        'data': colunas[4].text.strip(),
                        'status_raw': status_raw,
                    })

                try:
                    prox = driver.find_element(By.ID, 'example_next')
                    if 'disabled' in prox.get_attribute('class'):
                        break
                    prox.click()
                    wait.until(lambda d: d.find_elements(By.CSS_SELECTOR, 'tbody tr')[0].text != primeiro_nome)
                except Exception:
                    break

            driver.quit()
            driver = None

            log_info(f'Extração finalizada: {len(todos_dados)} linhas em {pagina} página(s).', origem='scheduler')

            # ──────────────── Sincronização Inteligente ────────────────
            # Chave de identidade expandida: (nome, data_verificacao, atividade)
            # Tracking em memória para evitar duplicatas na mesma sessão
            hoje = hoje_local()
            novos = 0
            atualizados = 0
            removidos = 0
            ignorados_data = 0
            ignorados_mes = 0
            duplicados_sessao = 0

            from .constants import SABER_STATUS_MAP, is_verificado

            # Set para rastrear chaves já processadas nesta coleta
            chaves_processadas = set()
            # Set com todas as chaves vindas do SABER (para etapa DELETE)
            saber_keys = set()

            for d in todos_dados:
                # Parse da data — sem fallback silencioso
                try:
                    data_verif = datetime.strptime(d['data'], '%d/%m/%Y').date()
                except Exception:
                    ignorados_data += 1
                    log_warn(
                        f'Registro ignorado: data inválida "{d["data"]}" para {d["nome"]}.',
                        origem='scheduler'
                    )
                    continue

                # Filtro de mês atual: coleta automática só processa mês corrente
                if origem == 'automatico':
                    if data_verif.month != hoje.month or data_verif.year != hoje.year:
                        ignorados_mes += 1
                        continue

                atividade = d['atividade'] or ''
                chave = (d['nome'], data_verif, atividade)
                saber_keys.add(chave)

                # Deduplicação em memória: evita processar a mesma chave 2x na mesma coleta
                if chave in chaves_processadas:
                    duplicados_sessao += 1
                    continue
                chaves_processadas.add(chave)

                status_saber = SABER_STATUS_MAP.get(d['status_raw'], 'pendente')

                existe = Verificacao.query.filter_by(
                    nome=d['nome'],
                    data_verificacao=data_verif,
                    atividade=atividade,
                ).first()

                if existe:
                    # Só atualiza status se o SABER mudou para verificado
                    # (nunca rebaixa uma marcação verificada para pendente)
                    mudou = False
                    if is_verificado(status_saber) and not is_verificado(existe.status):
                        existe.status = status_saber
                        mudou = True
                    # Atualiza cargo se mudou
                    if d['cargo'] and d['cargo'] != existe.cargo:
                        existe.cargo = d['cargo']
                        mudou = True
                    if mudou:
                        atualizados += 1
                else:
                    # Novo registro
                    db.session.add(Verificacao(
                        nome=d['nome'],
                        cargo=d['cargo'],
                        atividade=atividade,
                        data_verificacao=data_verif,
                        status=status_saber,
                        origem=origem,
                    ))
                    novos += 1
                    # Flush para que queries subsequentes encontrem este registro
                    db.session.flush()

            # DELETE: remove do banco quem não veio mais no SABER
            # Escopo: apenas registros com data de verificação dentro do mês atual
            import calendar
            primeiro_dia = hoje.replace(day=1)
            ultimo_dia_num = calendar.monthrange(hoje.year, hoje.month)[1]
            ultimo_dia = hoje.replace(day=ultimo_dia_num)

            registros_mes = Verificacao.query.filter(
                Verificacao.data_verificacao >= primeiro_dia,
                Verificacao.data_verificacao <= ultimo_dia,
            ).all()

            for reg in registros_mes:
                chave = (reg.nome, reg.data_verificacao, reg.atividade or '')
                if chave not in saber_keys:
                    db.session.delete(reg)
                    removidos += 1

            db.session.commit()

            # Relatório detalhado
            partes_msg = [f'{novos} novos']
            if atualizados:
                partes_msg.append(f'{atualizados} atualizados')
            if removidos:
                partes_msg.append(f'{removidos} removidos')
            if duplicados_sessao:
                partes_msg.append(f'{duplicados_sessao} duplicados ignorados')
            if ignorados_data:
                partes_msg.append(f'{ignorados_data} com data inválida')
            if ignorados_mes:
                partes_msg.append(f'{ignorados_mes} de outros meses')
            msg = '. '.join(partes_msg) + '.'

            _finalizar_log(db, LogAutomacao, log_id, 'sucesso', novos + atualizados, msg)
            log_info(msg, origem='scheduler')
            log_audit(
                f'Coleta {origem}: {len(todos_dados)} extraídos, {novos} inseridos, '
                f'{atualizados} atualizados, {removidos} removidos.',
                origem='scheduler'
            )

            return {
                'status': 'sucesso',
                'total': len(todos_dados),
                'novos': novos,
                'atualizados': atualizados,
                'removidos': removidos,
                'duplicados_ignorados': duplicados_sessao,
            }

        except Exception as e:
            if driver is not None:
                try:
                    driver.quit()
                except Exception:
                    pass
            import traceback as _tb
            _finalizar_log(db, LogAutomacao, log_id, 'erro', 0, str(e))
            log_error(f'Erro na coleta SABER: {e}', origem='scheduler', detalhe=_tb.format_exc())
            return {'status': 'erro', 'mensagem': str(e)}


def _finalizar_log(db, LogAutomacao, log_id, status, total, mensagem):
    """Atualiza log de automação com resultado final."""
    try:
        log = db.session.get(LogAutomacao, log_id)
        if log:
            log.status = status
            log.total_coletados = total
            log.mensagem = mensagem[:2000] if mensagem else mensagem
            db.session.commit()
    except Exception:
        db.session.rollback()
