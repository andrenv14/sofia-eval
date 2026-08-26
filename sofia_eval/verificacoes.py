"""O vocabulário fechado de verificação da v1.

Tudo aqui julga pelo EFEITO no Postgres, nunca pelo texto da resposta — o
texto de LLM não tem igualdade, mas "a linha existe", "a duração é 60" e "o
telefone confere" têm.
"""

from zoneinfo import ZoneInfo

from . import banco, datas


def aplicar(conn, tenant, cenario, ids_antes) -> tuple:
    """Devolve (falhas, custo). `falhas` vazia = cenário passou."""
    v = cenario.verificacoes
    tz = ZoneInfo(tenant["timezone"])
    ativos = banco.agendamentos_ativos(conn, tenant["id"])
    novos = [a for a in ativos if a["id"] not in ids_antes]
    custo = banco.custo(conn, tenant["id"])
    falhas = []

    if "agendamentos" in v:
        if len(ativos) != v["agendamentos"]:
            falhas.append(
                f"agendamentos: esperado {v['agendamentos']}, obtido {len(ativos)}"
                + _resumo(ativos, tz)
            )

    if "sem_agendamento_novo" in v:
        if v["sem_agendamento_novo"] and novos:
            falhas.append(
                f"sem_agendamento_novo: esperado nenhum agendamento novo, obtido {len(novos)}"
                + _resumo(novos, tz)
            )
        elif not v["sem_agendamento_novo"] and not novos:
            falhas.append("sem_agendamento_novo: false exige um agendamento novo, obtido nenhum")

    if "agendamento" in v:
        if len(novos) != 1:
            falhas.append(
                f"agendamento: as checagens de linha exigem exatamente 1 agendamento novo, "
                f"obtido {len(novos)}" + _resumo(novos, tz)
            )
        else:
            falhas.extend(_checar_linha(v["agendamento"], novos[0], cenario, tenant, tz))

    if "chamadas_ia_max" in v and custo["chamadas_ia"] > v["chamadas_ia_max"]:
        falhas.append(
            f"chamadas_ia_max: teto {v['chamadas_ia_max']}, obtido {custo['chamadas_ia']} "
            "chamadas de IA (guarda de custo)"
        )

    if "tokens_prompt_max" in v and custo["prompt_tokens"] > v["tokens_prompt_max"]:
        falhas.append(
            f"tokens_prompt_max: teto {v['tokens_prompt_max']}, obtido {custo['prompt_tokens']} "
            "tokens de prompt (guarda de custo)"
        )

    return falhas, custo


def _checar_linha(esperado, linha, cenario, tenant, tz) -> list:
    falhas = []

    if "duracao_minutos" in esperado:
        obtido = int((linha["fim"] - linha["inicio"]).total_seconds() // 60)
        if obtido != esperado["duracao_minutos"]:
            falhas.append(
                f"agendamento.duracao_minutos: esperado {esperado['duracao_minutos']}, obtido {obtido}"
            )

    if "profissional" in esperado:
        obtido = linha["profissional"]
        if (obtido or "").strip().casefold() != esperado["profissional"].strip().casefold():
            falhas.append(
                f"agendamento.profissional: esperado {esperado['profissional']!r}, obtido {obtido!r}"
            )

    if "telefone" in esperado:
        # `contato` = o telefone que mandou as mensagens neste cenário.
        alvo = cenario.contato if esperado["telefone"] == "contato" else esperado["telefone"]
        if linha["telefone"] != alvo:
            rotulo = f"contato ({alvo})" if esperado["telefone"] == "contato" else alvo
            falhas.append(f"agendamento.telefone: esperado {rotulo}, obtido {linha['telefone']}")

    if "data" in esperado:
        alvo = datas.resolver(esperado["data"], tenant["timezone"])
        obtida = linha["inicio"].astimezone(tz).date()
        if obtida != alvo:
            falhas.append(
                f"agendamento.data: esperado {alvo.isoformat()}, obtido {obtida.isoformat()} "
                f"(fuso {tenant['timezone']})"
            )

    if "horario" in esperado:
        obtido = linha["inicio"].astimezone(tz).strftime("%H:%M")
        if obtido != esperado["horario"]:
            falhas.append(f"agendamento.horario: esperado {esperado['horario']}, obtido {obtido}")

    return falhas


def _resumo(linhas, tz) -> str:
    if not linhas:
        return ""
    partes = []
    for a in linhas:
        inicio = a["inicio"].astimezone(tz)
        minutos = int((a["fim"] - a["inicio"]).total_seconds() // 60)
        prof = f", {a['profissional']}" if a["profissional"] else ""
        partes.append(f"{inicio:%Y-%m-%d %H:%M} ({minutos}min, {a['telefone']}{prof})")
    return " — no banco: " + "; ".join(partes)
