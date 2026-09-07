"""O vocabulário fechado de verificação da v1.

Tudo aqui julga pelo EFEITO no Postgres, nunca pelo texto da resposta — o
texto de LLM não tem igualdade, mas "a linha existe", "a duração é 60" e "o
telefone confere" têm.
"""

import time
from zoneinfo import ZoneInfo

from . import banco, datas

# Janela de espera de `humano_pendente`, em segundos.
#
# A escrita de `contatos_estado` é a ÚLTIMA do turno e não tem nada depois
# dela: `processarBuffer` (`src/server.js`) grava o status terminal de
# `mensagens_pendentes` ANTES do `finally`, e é o `finally` que chama
# `silenciarPorHandoff`. Ou seja: quando o eval acorda — que é no turno da
# assistente aparecer em `messages`, ainda mais cedo — a linha pode não existir
# ainda. A cauda é um upsert; localmente, milissegundos.
#
# Cinco segundos são ordens de grandeza acima da cauda medida, e o custo de
# errar para mais é só tempo em cenário que espera `false`.
ESPERA_HUMANO_S = 5.0


def _checagem(chave, esperado, obtido, ok) -> dict:
    """Uma verificação, estruturada — esperado × obtido × veredito.

    Existe ao lado de `falhas` (que já formata a mesma coisa em texto) porque
    `falhas` só registra o que reprovou: verificação que passa não deixa
    rastro nela. `checagens` traz as duas, e é o que o relatório HTML usa para
    mostrar cada verificação, não só as que falharam."""
    return {"chave": chave, "esperado": esperado, "obtido": obtido, "ok": ok}


def _esperar_humano(conn, tenant_id: int, telefone: str) -> bool:
    """Lê `humano_pendente` sondando até `ESPERA_HUMANO_S`.

    A espera NÃO depende do que o cenário espera encontrar, e isso é
    deliberado: uma sondagem que desistisse cedo quando espera `false` mediria
    a própria expectativa. Aqui a janela é a mesma nos dois sentidos, e o
    retorno antecipado acontece só quando a linha APARECE — nunca quando ela
    falta.

    As quatro combinações, e por que nenhuma mente:
    - espera true, aparece  → volta cedo, PASSA;
    - espera true, não aparece → volta false no fim da janela, REPROVA. Se a
      janela for curta demais, o sintoma é VERMELHO VISÍVEL, não verde falso;
    - espera false, não aparece → esperou a janela inteira antes de afirmar;
    - espera false, aparece → volta cedo, REPROVA.

    A assimetria é o ponto: janela curta demais nunca produz falso verde, só
    falso vermelho — que alguém investiga."""
    limite = time.monotonic() + ESPERA_HUMANO_S
    while True:
        if banco.humano_pendente(conn, tenant_id, telefone):
            return True
        if time.monotonic() >= limite:
            return False
        time.sleep(0.1)


def aplicar(conn, tenant, cenario, ids_antes) -> tuple:
    """Devolve (falhas, custo, checagens). `falhas` vazia = cenário passou."""
    v = cenario.verificacoes
    tz = ZoneInfo(tenant["timezone"])
    ativos = banco.agendamentos_ativos(conn, tenant["id"])
    novos = [a for a in ativos if a["id"] not in ids_antes]
    custo = banco.custo(conn, tenant["id"])
    falhas = []
    checagens = []

    if "agendamentos" in v:
        ok = len(ativos) == v["agendamentos"]
        checagens.append(_checagem("agendamentos", v["agendamentos"], len(ativos), ok))
        if not ok:
            falhas.append(
                f"agendamentos: esperado {v['agendamentos']}, obtido {len(ativos)}"
                + _resumo(ativos, tz)
            )

    if "sem_agendamento_novo" in v:
        esperado = v["sem_agendamento_novo"]
        obtido = not novos
        ok = esperado == obtido
        checagens.append(_checagem("sem_agendamento_novo", esperado, obtido, ok))
        if esperado and novos:
            falhas.append(
                f"sem_agendamento_novo: esperado nenhum agendamento novo, obtido {len(novos)}"
                + _resumo(novos, tz)
            )
        elif not esperado and not novos:
            falhas.append("sem_agendamento_novo: false exige um agendamento novo, obtido nenhum")

    if "agendamento" in v:
        ok = len(novos) == 1
        if not ok:
            checagens.append(
                _checagem("agendamento", "exatamente 1 agendamento novo", len(novos), ok)
            )
            falhas.append(
                f"agendamento: as checagens de linha exigem exatamente 1 agendamento novo, "
                f"obtido {len(novos)}" + _resumo(novos, tz)
            )
        else:
            falhas_linha, checagens_linha = _checar_linha(
                v["agendamento"], novos[0], cenario, tenant, tz
            )
            falhas.extend(falhas_linha)
            checagens.extend(checagens_linha)

    if "chamadas_ia_max" in v:
        ok = custo["chamadas_ia"] <= v["chamadas_ia_max"]
        checagens.append(_checagem("chamadas_ia_max", v["chamadas_ia_max"], custo["chamadas_ia"], ok))
        if not ok:
            falhas.append(
                f"chamadas_ia_max: teto {v['chamadas_ia_max']}, obtido {custo['chamadas_ia']} "
                "chamadas de IA (guarda de custo)"
            )

    if "tokens_prompt_max" in v:
        ok = custo["prompt_tokens"] <= v["tokens_prompt_max"]
        checagens.append(_checagem("tokens_prompt_max", v["tokens_prompt_max"], custo["prompt_tokens"], ok))
        if not ok:
            falhas.append(
                f"tokens_prompt_max: teto {v['tokens_prompt_max']}, obtido {custo['prompt_tokens']} "
                "tokens de prompt (guarda de custo)"
            )

    if "respostas_assistente_max" in v:
        obtido = banco.turnos_da_assistente(conn, tenant["id"], cenario.contato)
        ok = obtido <= v["respostas_assistente_max"]
        checagens.append(_checagem("respostas_assistente_max", v["respostas_assistente_max"], obtido, ok))
        if not ok:
            falhas.append(
                f"respostas_assistente_max: teto {v['respostas_assistente_max']}, obtido {obtido} "
                "respostas da assistente (guarda de comportamento — desengajar)"
            )

    if "humano_pendente" in v:
        esperado = v["humano_pendente"]
        obtido = _esperar_humano(conn, tenant["id"], cenario.contato)
        ok = esperado == obtido
        checagens.append(_checagem("humano_pendente", esperado, obtido, ok))
        if not ok:
            falhas.append(
                "humano_pendente: esperado que a ferramenta de atendente humano "
                f"{'FOSSE' if esperado else 'NÃO fosse'} chamada, e "
                f"`contatos_estado.humano_pendente_desde` "
                f"{'não apareceu' if esperado else 'apareceu'} "
                f"em {ESPERA_HUMANO_S:.0f}s"
                + ("" if esperado else " — o modelo delegou quando devia atender")
            )

    if "agendamento_status" in v:
        falhas_status, checagens_status = _checar_status(conn, v["agendamento_status"], cenario, tenant)
        falhas.extend(falhas_status)
        checagens.extend(checagens_status)

    return falhas, custo, checagens


def _checar_status(conn, itens, cenario, tenant) -> tuple:
    falhas = []
    checagens = []
    for i, item in enumerate(itens):
        chave = f"agendamento_status[{i}]"
        alvo = cenario.contato if item["telefone"] == "contato" else item["telefone"]
        rotulo_tel = f"contato ({alvo})" if item["telefone"] == "contato" else alvo
        dia = datas.resolver(item["data"], tenant["timezone"])
        inicio = datas.instante(dia, item["horario"], tenant["timezone"])
        onde = f"{rotulo_tel}, {dia.isoformat()} {item['horario']}"

        linhas = banco.agendamento_por(conn, tenant["id"], alvo, inicio)
        if len(linhas) != 1:
            checagens.append(
                _checagem(chave, f"exatamente 1 linha ({onde})", f"{len(linhas)} linha(s)", False)
            )
            falhas.append(f"{chave}: esperava exatamente 1 linha para {onde}, achei {len(linhas)}")
            continue

        obtido = linhas[0]["status"]
        ok = obtido == item["status"]
        checagens.append(_checagem(chave, f"{item['status']} ({onde})", obtido, ok))
        if not ok:
            falhas.append(f"{chave}: esperado status {item['status']!r} para {onde}, obtido {obtido!r}")

    return falhas, checagens


def _checar_linha(esperado, linha, cenario, tenant, tz) -> tuple:
    falhas = []
    checagens = []

    if "duracao_minutos" in esperado:
        obtido = int((linha["fim"] - linha["inicio"]).total_seconds() // 60)
        ok = obtido == esperado["duracao_minutos"]
        checagens.append(_checagem("agendamento.duracao_minutos", esperado["duracao_minutos"], obtido, ok))
        if not ok:
            falhas.append(
                f"agendamento.duracao_minutos: esperado {esperado['duracao_minutos']}, obtido {obtido}"
            )

    if "profissional" in esperado:
        obtido = linha["profissional"]
        ok = (obtido or "").strip().casefold() == esperado["profissional"].strip().casefold()
        checagens.append(_checagem("agendamento.profissional", esperado["profissional"], obtido, ok))
        if not ok:
            falhas.append(
                f"agendamento.profissional: esperado {esperado['profissional']!r}, obtido {obtido!r}"
            )

    if "telefone" in esperado:
        # `contato` = o telefone que mandou as mensagens neste cenário.
        alvo = cenario.contato if esperado["telefone"] == "contato" else esperado["telefone"]
        obtido = linha["telefone"]
        ok = obtido == alvo
        rotulo = f"contato ({alvo})" if esperado["telefone"] == "contato" else alvo
        checagens.append(_checagem("agendamento.telefone", rotulo, obtido, ok))
        if not ok:
            falhas.append(f"agendamento.telefone: esperado {rotulo}, obtido {obtido}")

    if "data" in esperado:
        alvo = datas.resolver(esperado["data"], tenant["timezone"])
        obtida = linha["inicio"].astimezone(tz).date()
        ok = obtida == alvo
        checagens.append(_checagem("agendamento.data", alvo.isoformat(), obtida.isoformat(), ok))
        if not ok:
            falhas.append(
                f"agendamento.data: esperado {alvo.isoformat()}, obtido {obtida.isoformat()} "
                f"(fuso {tenant['timezone']})"
            )

    if "horario" in esperado:
        obtido = linha["inicio"].astimezone(tz).strftime("%H:%M")
        ok = obtido == esperado["horario"]
        checagens.append(_checagem("agendamento.horario", esperado["horario"], obtido, ok))
        if not ok:
            falhas.append(f"agendamento.horario: esperado {esperado['horario']}, obtido {obtido}")

    return falhas, checagens


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
