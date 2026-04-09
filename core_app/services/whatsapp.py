import pywhatkit
from core_app.config.settings import TELEFONE

def enviar(msg):
    pywhatkit.sendwhatmsg_instantly(
        TELEFONE,
        msg,
        wait_time=10,
        tab_close=True
    )