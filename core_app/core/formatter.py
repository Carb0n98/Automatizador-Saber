def formatar(atrasados, hoje_lista):
    msg = ""

    if atrasados:
        msg += "🚨 ATRASADO\n"
        for a in atrasados:
            msg += f"{a['nome']}: {a['atividade']} ({a['data']})\n"

    if hoje_lista:
        msg += "\n📅 HOJE\n"
        for h in hoje_lista:
            msg += f"{h['nome']}: {h['atividade']}\n"

    return msg if msg else "Nenhum treinamento pendente hoje ✅"