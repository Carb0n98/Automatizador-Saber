from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select
import re

from core_app.config.settings import RESTAURANTE, MES

def aplicar_filtros(driver):
    wait = WebDriverWait(driver, 15)

    select_rest = Select(wait.until(EC.presence_of_element_located(
        (By.ID, "ContentPlaceHolder1_idrestaurante")
    )))
    select_rest.select_by_visible_text(RESTAURANTE)

    select_mes = Select(driver.find_element(
        By.ID, "ContentPlaceHolder1_fecharegistro"
    ))
    select_mes.select_by_visible_text(MES)

    botao_pesquisar = driver.find_element(By.ID, "ContentPlaceHolder1_btnConsulta")
    botao_pesquisar.click()

    wait.until(EC.presence_of_element_located(
        (By.TAG_NAME, "table"))
    )


def extrair_dados(driver):
    wait = WebDriverWait(driver, 15)

    todos_dados = []

    while True:
        tabela = wait.until(EC.presence_of_element_located(
            (By.ID, "example")
        ))

        linhas = tabela.find_elements(By.CSS_SELECTOR, "tbody tr")

        primeiro_nome = linhas[0].text if linhas else ""

        for linha in linhas:
            colunas = linha.find_elements(By.TAG_NAME, "td")

            if len(colunas) < 6:
                continue

            # Verificação (ícone)
            tem_icone = linha.find_elements(
                By.CSS_SELECTOR, "i.fas.fa-external-link-square-alt"
            )

            if not tem_icone:
                continue

            # Status (APTO = já feito)
            status_verificacao = colunas[5].text.strip().upper()

            if status_verificacao == "APTO":
                continue

            # 🔥 Só entra aqui quem precisa de treinamento
            todos_dados.append({
                "nome": colunas[1].text,
                "cargo": colunas[2].text,
                "atividade": colunas[3].text,
                "data": colunas[4].text,
                "status": colunas[5].text
            })

        print(f"Página coletada... Total filtrado: {len(todos_dados)}")

        try:
            proximo = driver.find_element(By.ID, "example_next")

            if "disabled" in proximo.get_attribute("class"):
                break

            proximo.click()

            wait.until(lambda d: d.find_elements(By.CSS_SELECTOR, "tbody tr")[0].text != primeiro_nome)

        except:
            break

    return todos_dados