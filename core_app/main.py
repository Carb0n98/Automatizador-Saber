from core_app.scraper.bot import iniciar_driver, login, navegar
from core_app.scraper.parser import aplicar_filtros, extrair_dados
from core_app.core.processor import processar
from core_app.core.formatter import formatar
from core_app.services.whatsapp import enviar

def main():
    driver = iniciar_driver()

    login(driver)
    navegar(driver)

    aplicar_filtros(driver)

    dados = extrair_dados(driver)

    atrasados, hoje_lista = processar(dados)

    mensagem = formatar(atrasados, hoje_lista)

    print(mensagem)

    enviar(mensagem)

if __name__ == "__main__":
    main()