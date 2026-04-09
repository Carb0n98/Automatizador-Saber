from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from core_app.config.settings import URL, USUARIO, SENHA

def iniciar_driver():
    return webdriver.Chrome()

def login(driver):
    wait = WebDriverWait(driver, 15)

    driver.get(URL)

    usuario = wait.until(EC.presence_of_element_located(
        (By.ID, "ContentPlaceHolder1_usuario")
    ))
    senha = driver.find_element(By.ID, "ContentPlaceHolder1_password")

    usuario.send_keys(USUARIO)
    senha.send_keys(SENHA)

    driver.find_element(By.ID, "ContentPlaceHolder1_btnLogin").click()

def navegar(driver):
    wait = WebDriverWait(driver, 15)

    wait.until(EC.element_to_be_clickable(
        (By.ID, "ContentPlaceHolder1_LinkButton1")
    )).click()

    # Sistema
    wait.until(EC.element_to_be_clickable(
        (By.ID, "ContentPlaceHolder1_BtnSistema")
    )).click()

    # Planejamento
    wait.until(EC.element_to_be_clickable(
        (By.ID, "ContentPlaceHolder1_tabPlanejamento")
    )).click()

    # Resumo
    wait.until(EC.element_to_be_clickable(
        (By.ID, "ContentPlaceHolder1_btnResumen")
        
    )).click()