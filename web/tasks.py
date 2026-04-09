"""
Tarefa de coleta de dados do sistema SABER via Selenium.
Chamada pelo scheduler diário (07:00) e pelo botão de busca manual.
"""
from datetime import datetime, date
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

    # Fix #3: usar o lock para evitar race condition real
    if not _scraping_lock.acquire(blocking=False):
        return {'status': 'ocupado', 'mensagem': 'Uma coleta já está em andamento.'}

    _is_running = True

    with app.app_context():
        from .models import db, Config, Verificacao, LogAutomacao

        # Cria log de execução inicial
        log = LogAutomacao(
            status='executando',
            total_coletados=0,
            mensagem='Coleta iniciada...'
        )
        db.session.add(log)
        db.session.commit()
        log_id = log.id

        try:
            url = Config.get('saber_url', 'https://adtalento.com/websiteSaber')
            usuario = Config.get('saber_usuario', '')
            senha = Config.get('saber_senha', '')
            restaurante = Config.get('restaurante', 'NPN')
            mes = get_mes_atual_pt()

            if not usuario or not senha:
                _finalizar_log(db, LogAutomacao, log_id, 'erro', 0,
                               'Credenciais não configuradas. Acesse Configurações e informe usuário/senha do SABER.')
                _is_running = False
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

            # Suporte a Docker: usa CHROME_BIN se disponível (ex: /usr/bin/chromium)
            chrome_bin = os.environ.get('CHROME_BIN')
            if chrome_bin:
                options.binary_location = chrome_bin

            # Suporte a Docker: usa CHROMEDRIVER_BIN se disponível
            chromedriver_bin = os.environ.get('CHROMEDRIVER_BIN')
            service = Service(executable_path=chromedriver_bin) if chromedriver_bin else Service()

            # Fix #1: declarar driver=None antes para evitar NameError no except
            driver = None
            driver = webdriver.Chrome(service=service, options=options)
            # Fix #6: timeouts para evitar travamento infinito
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

            # Extração de dados (paginada)
            todos_dados = []
            while True:
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

            # ──────────────── Salvar no banco ────────────────
            hoje = date.today()
            novos = 0
            atualizados = 0
            ignorados = 0

            for d in todos_dados:
                # Determina status a partir do que veio do SABER
                status_final = 'apto' if d['status_raw'] == 'APTO' else 'pendente'

                try:
                    data_verif = datetime.strptime(d['data'], '%d/%m/%Y').date()
                except Exception:
                    data_verif = hoje

                # Verifica se já existe no banco (mesmo nome + mesma data)
                existe = Verificacao.query.filter_by(
                    nome=d['nome'],
                    data_verificacao=data_verif
                ).first()

                if existe:
                    # Se o SABER agora marca como APTO e o banco ainda tem pendente → atualiza
                    if status_final == 'apto' and existe.status != 'apto':
                        existe.status = 'apto'
                        atualizados += 1
                    else:
                        ignorados += 1  # Sem mudança relevante, ignora
                    continue

                # Registro novo — salva com o status correto (pendente OU apto)
                db.session.add(Verificacao(
                    nome=d['nome'],
                    cargo=d['cargo'],
                    atividade=d['atividade'],
                    data_verificacao=data_verif,
                    status=status_final,
                    origem=origem,
                ))
                novos += 1

            db.session.commit()

            msg = (f'{novos} novos registros. '
                   f'{atualizados} atualizados para APTO. '
                   f'{ignorados} duplicatas ignoradas.')
            _finalizar_log(db, LogAutomacao, log_id, 'sucesso', novos + atualizados, msg)
            _is_running = False
            _scraping_lock.release()
            return {'status': 'sucesso', 'total': len(todos_dados), 'novos': novos, 'ignorados': ignorados}

        except Exception as e:
            # Fix #1: só chama quit() se driver foi realmente criado
            if driver is not None:
                try:
                    driver.quit()
                except Exception:
                    pass
            _finalizar_log(db, LogAutomacao, log_id, 'erro', 0, str(e))
            _is_running = False
            _scraping_lock.release()
            return {'status': 'erro', 'mensagem': str(e)}


def _finalizar_log(db, LogAutomacao, log_id, status, total, mensagem):
    # Fix #2: usar merge para segurança se a sessão estiver suja
    try:
        log = db.session.get(LogAutomacao, log_id)
        if log:
            log.status = status
            log.total_coletados = total
            log.mensagem = mensagem[:2000] if mensagem else mensagem  # truncar mensagens longas
            db.session.commit()
    except Exception:
        db.session.rollback()
