"""Quem verifica o verificador.

O eval julga o comportamento do modelo. Isto julga o EVAL — especificamente a
camada que decide PASSOU/FALHOU (`verificacoes.aplicar`), que é onde um bug
não aparece como erro: aparece como veredito errado, em silêncio, e todo mundo
acredita.

Cada caso é exercido nos DOIS sentidos — a verificação tem de passar quando
deve E falhar quando deve. É o mesmo princípio que o `AGENTS.md` exige de
cenário que nasce verde: asserção que só foi vista passar não provou que
consegue reprovar. Aqui isso vale por construção.

Custo: nenhuma chamada de LLM, nenhum webhook, nenhum evento no Calendar. Só
Postgres — mas Postgres É o `sofia_test`, então **respeita a trava de recurso**:
não rode junto com a suíte do sofia-bot. Segundos, não minutos.

    .venv/bin/python -m sofia_eval.autoteste

Sai 0 se tudo passou, 1 se algo falhou.
"""

import sys
from datetime import timedelta
from pathlib import Path

from . import banco, cenario as mod_cenario, config, datas, relatorio, tenant as mod_tenant, verificacoes

CONTATO = "5511999990100"
TERCEIRO = "5511999990200"
RAIZ = Path(__file__).resolve().parent.parent


def _cenario(verifs: dict):
    c = mod_cenario.Cenario(id="autoteste", caminho=RAIZ, verificacoes=verifs)
    c.contato = CONTATO
    return c


def _semear(conn, tenant, tz):
    """Estado conhecido: um agendamento CANCELADO do contato às 10h, um ATIVO
    de terceiro às 14h, e três respostas da assistente."""
    dia = datas.resolver("+2", tz)
    inicio_contato = datas.instante(dia, "10:00", tz)
    inicio_terceiro = datas.instante(dia, "14:00", tz)
    for telefone, inicio, status in (
        (CONTATO, inicio_contato, "cancelado"),
        (TERCEIRO, inicio_terceiro, "ativo"),
    ):
        conn.execute(
            "INSERT INTO appointments (tenant_id, telefone, inicio, fim, status) "
            "VALUES (%s, %s, %s, %s, %s)",
            (tenant["id"], telefone, inicio, inicio + timedelta(minutes=60), status),
        )
    for i in range(3):
        conn.execute(
            "INSERT INTO messages (tenant_id, contact_phone, role, content) VALUES (%s, %s, %s, %s)",
            (tenant["id"], CONTATO, "assistant", f"resposta {i}"),
        )
    return inicio_contato


def _checar_degradados(conn, tenant) -> list:
    """`banco.turnos_degradados` nos dois sentidos.

    O caso real que originou isto: rate limit (429) devolve corpo com `usage`
    zerado, `openrouter.js` conta a chamada e não soma token. Sem esta
    detecção o cenário PASSAVA — verde observando nada."""
    casos = []
    tid = tenant["id"]

    conn.execute("DELETE FROM ai_usage WHERE tenant_id = %s", (tid,))
    casos.append((banco.turnos_degradados(conn, tid) == 0,
                  "turnos_degradados: sem uso registrado, não acusa", ""))

    conn.execute(
        "INSERT INTO ai_usage (tenant_id, contact_phone, model, prompt_tokens, "
        "completion_tokens, total_tokens, chamadas_ia) VALUES (%s,%s,%s,%s,%s,%s,%s)",
        (tid, CONTATO, "teste/modelo", 4000, 120, 4120, 3),
    )
    casos.append((banco.turnos_degradados(conn, tid) == 0,
                  "turnos_degradados: turno saudável não é acusado", ""))

    conn.execute(
        "INSERT INTO ai_usage (tenant_id, contact_phone, model, prompt_tokens, "
        "completion_tokens, total_tokens, chamadas_ia) VALUES (%s,%s,%s,%s,%s,%s,%s)",
        (tid, CONTATO, "teste/modelo", 0, 0, 0, 1),
    )
    n = banco.turnos_degradados(conn, tid)
    casos.append((n == 1,
                  "turnos_degradados: acusa o degradado MISTURADO com o saudável",
                  "" if n == 1 else f"esperava 1, obtive {n}"))

    conn.execute("DELETE FROM ai_usage WHERE tenant_id = %s", (tid,))
    return casos


def _checar_degradados_por_texto(conn, tenant) -> list:
    """`banco.turnos_degradados_por_texto` nos dois sentidos, mais o
    quase-acerto.

    O quase-acerto não é zelo: é a fragilidade do contorno virando teste. A
    detecção casa uma string LITERAL de `openrouter.js`, então um texto
    PARECIDO tem de NÃO acusar — e é exatamente por isso que mudar aquele
    texto cega o eval em silêncio. Se este caso um dia falhar, alguém
    afrouxou o casamento, e aí a detecção passa a acusar resposta legítima."""
    casos = []
    tid = tenant["id"]

    def _msg(texto):
        conn.execute(
            "INSERT INTO messages (tenant_id, contact_phone, role, content) "
            "VALUES (%s, %s, %s, %s)",
            (tid, CONTATO, "assistant", texto),
        )

    conn.execute("DELETE FROM messages WHERE tenant_id = %s", (tid,))
    _msg("Claro! Consigo às 9h com a Helena. Confirmo?")
    casos.append((banco.turnos_degradados_por_texto(conn, tid) == 0,
                  "degradado por texto: resposta legítima não é acusada", ""))

    _msg(banco.TEXTO_DEGRADADO)
    n = banco.turnos_degradados_por_texto(conn, tid)
    casos.append((n == 1,
                  "degradado por texto: acusa a desculpa MISTURADA com a legítima",
                  "" if n == 1 else f"esperava 1, obtive {n}"))

    _msg(banco.TEXTO_DEGRADADO)
    n = banco.turnos_degradados_por_texto(conn, tid)
    casos.append((n == 2,
                  "degradado por texto: conta um por TURNO, não um por cenário",
                  "" if n == 2 else f"esperava 2, obtive {n}"))

    # Mesma frase sem o acento de "última": o casamento é por igualdade, então
    # isto NÃO pode acusar. É a fragilidade do contorno, medida.
    _msg("Desculpa, deu uma travada aqui. Pode repetir sua ultima mensagem?")
    n = banco.turnos_degradados_por_texto(conn, tid)
    casos.append((n == 2,
                  "degradado por texto: quase-acerto NÃO acusa (a fragilidade, medida)",
                  "" if n == 2 else f"esperava 2, obtive {n}"))

    conn.execute("DELETE FROM messages WHERE tenant_id = %s", (tid,))
    return casos


def _checar_tetos(conn, tenant, ids_antes) -> list:
    """`chamadas_ia_max` e `tokens_prompt_max` nos DOIS sentidos.

    Existe porque havia um buraco: até 05/09 o autoteste exercitava
    `chamadas_ia_max` só no sentido que PASSA ("sem uso registrado, 0 <= 5") e
    `tokens_prompt_max` em sentido nenhum. Teto que nunca foi visto reprovar
    não provou que consegue reprovar — e nesse dia oito cenários da leva 2
    ganharam teto medido de uma vez, o que fez do buraco um risco real: um teto
    quebrado não apareceria como erro, apareceria como cenário passando por
    engano.

    O caso do LIMITE EXATO não é zelo: teto é "no máximo isto", então gastar
    exatamente o teto tem de PASSAR. Se um dia virar `<` em vez de `<=`, todo
    cenário calibrado reprova por calibração no seu próprio máximo medido."""
    casos = []
    tid = tenant["id"]

    conn.execute("DELETE FROM ai_usage WHERE tenant_id = %s", (tid,))
    conn.execute(
        "INSERT INTO ai_usage (tenant_id, contact_phone, model, prompt_tokens, "
        "completion_tokens, total_tokens, chamadas_ia) VALUES (%s,%s,%s,%s,%s,%s,%s)",
        (tid, CONTATO, "teste/modelo", 50000, 1200, 51200, 10),
    )

    for verifs, espera_reprovar, descricao in (
        ({"chamadas_ia_max": 11},    False, "chamadas_ia_max: 10 <= 11, não acusa"),
        ({"chamadas_ia_max": 10},    False, "chamadas_ia_max: LIMITE EXATO passa (10 <= 10)"),
        ({"chamadas_ia_max": 9},     True,  "chamadas_ia_max: acusa o estouro (10 > 9)"),
        ({"tokens_prompt_max": 50001}, False, "tokens_prompt_max: 50.000 <= 50.001, não acusa"),
        ({"tokens_prompt_max": 50000}, False, "tokens_prompt_max: LIMITE EXATO passa"),
        ({"tokens_prompt_max": 49999}, True,  "tokens_prompt_max: acusa o estouro"),
    ):
        falhas, _, _ = verificacoes.aplicar(conn, tenant, _cenario(verifs), ids_antes)
        ok = bool(falhas) == espera_reprovar
        motivo = "" if ok else (
            f"esperava {'reprovar' if espera_reprovar else 'passar'}, "
            f"obtive {falhas or 'nenhuma falha'}")
        casos.append((ok, descricao, falhas[0] if (ok and falhas) else motivo))

    conn.execute("DELETE FROM ai_usage WHERE tenant_id = %s", (tid,))
    return casos


# (descrição, verificacoes, espera_reprovar)
CASOS = (
    ("agendamentos: conta só os ativos", {"agendamentos": 1}, False),
    ("agendamentos: acusa contagem errada", {"agendamentos": 2}, True),
    ("sem_agendamento_novo: nada novo desde ids_antes", {"sem_agendamento_novo": True}, False),
    (
        "agendamento_status: acha as duas linhas com o status certo",
        {"agendamento_status": [
            {"telefone": "contato", "data": "+2", "horario": "10:00", "status": "cancelado"},
            {"telefone": TERCEIRO, "data": "+2", "horario": "14:00", "status": "ativo"},
        ]},
        False,
    ),
    (
        "agendamento_status: acusa status divergente",
        {"agendamento_status": [
            {"telefone": "contato", "data": "+2", "horario": "10:00", "status": "ativo"}]},
        True,
    ),
    (
        "agendamento_status: acusa linha que não existe (não passa em branco)",
        {"agendamento_status": [
            {"telefone": "contato", "data": "+2", "horario": "16:00", "status": "cancelado"}]},
        True,
    ),
    ("respostas_assistente_max: dentro do teto (3 <= 3)", {"respostas_assistente_max": 3}, False),
    ("respostas_assistente_max: acima do teto (3 > 2)", {"respostas_assistente_max": 2}, True),
    ("chamadas_ia_max: sem uso registrado, 0 <= 5", {"chamadas_ia_max": 5}, False),
)


def main() -> int:
    try:
        cfg = config.carregar()
    except config.ErroDeConfig as err:
        config.erro_fatal(str(err))

    print(f"autoteste do verificador · banco {config.BANCO_EXIGIDO} · nenhuma chamada de LLM\n")
    resultados = []

    with banco.conectar(cfg.database_url) as conn:
        mod_tenant.limpar(conn)
        modelo = mod_cenario.Cenario(id="autoteste", caminho=RAIZ, verificacoes={"agendamentos": 0})
        modelo.contato = CONTATO
        tenant = mod_tenant.criar(conn, modelo, cfg)
        try:
            _semear(conn, tenant, tenant["timezone"])
            ids_antes = {a["id"] for a in banco.agendamentos_ativos(conn, tenant["id"])}

            for descricao, verifs, espera_reprovar in CASOS:
                falhas, _, _ = verificacoes.aplicar(conn, tenant, _cenario(verifs), ids_antes)
                ok = bool(falhas) == espera_reprovar
                resultados.append((ok, descricao, falhas[0] if falhas else ""))

            # Ambiguidade: duas linhas idênticas têm de derrubar, nunca escolher uma.
            tz = tenant["timezone"]
            dia = datas.resolver("+2", tz)
            conn.execute(
                "INSERT INTO appointments (tenant_id, telefone, inicio, fim, status) "
                "VALUES (%s, %s, %s, %s, %s)",
                (tenant["id"], CONTATO,
                 datas.instante(dia, "10:00", tz),
                 datas.instante(dia, "11:00", tz),
                 "cancelado"),
            )
            falhas, _, _ = verificacoes.aplicar(
                conn, tenant,
                _cenario({"agendamento_status": [
                    {"telefone": "contato", "data": "+2", "horario": "10:00", "status": "cancelado"}]}),
                ids_antes,
            )
            resultados.append((bool(falhas), "agendamento_status: recusa por ambiguidade (2 linhas)",
                               falhas[0] if falhas else ""))

            resultados.extend(_checar_degradados(conn, tenant))
            resultados.extend(_checar_degradados_por_texto(conn, tenant))
            resultados.extend(_checar_tetos(conn, tenant, ids_antes))
        finally:
            mod_tenant.limpar(conn)

    passaram = sum(1 for ok, _, _ in resultados if ok)
    for ok, descricao, motivo in resultados:
        marca = relatorio.verde("ok  ") if ok else relatorio.vermelho("ERRO")
        print(f"  [{marca}] {descricao}")
        if motivo:
            print(f"          {motivo[:100]}")
    print(f"\n{passaram}/{len(resultados)} casos corretos")
    return 0 if passaram == len(resultados) else 1


if __name__ == "__main__":
    sys.exit(main())
