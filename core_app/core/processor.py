from datetime import datetime

def processar(dados):
    hoje = datetime.today().date()

    atrasados = []
    hoje_lista = []

    for d in dados:
        try:
            data_item = datetime.strptime(d["data"], "%d/%m/%Y").date()
        except:
            continue

        if d["status"] != "-" and d["status"] != "":
            continue  # já feito

        if data_item < hoje:
            atrasados.append(d)
        elif data_item == hoje:
            hoje_lista.append(d)

    return atrasados, hoje_lista