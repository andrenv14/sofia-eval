"""Um comando: roda os cenários, imprime a tabela, devolve código de saída."""

import argparse
import sys
import time
from pathlib import Path

from . import (
    agenda,
    banco,
    cenario as mod_cenario,
    config,
    datas,
    evidencia,
    google_calendar,
    relatorio,
    relatorio_html,
    tenant as mod_tenant,
    turnos,
    verificacoes,
    webhook,
)

PASTA_PADRAO = Path(__file__).resolve().parent.parent / "cenarios"


def argumentos():
    p = argparse.ArgumentParser(
        prog="sofia-eval",
        description="Avalia o COMPORTAMENTO do modelo do sofia-bot contra um servidor local, "
        "julgando pelo que ficou no banco.",
    )
    p.add_argument("--cenario", action="append", metavar="ID",
                   help="roda só este cenário (pode repetir)")
    p.add_argument("--pasta", type=Path, default=PASTA_PADRAO,
                   help=f"pasta dos YAML (padrão: {PASTA_PADRAO})")
    p.add_argument("--lista", action="store_true", help="lista os cenários e sai")
    return p.parse_args()


def main() -> int:
    args = argumentos()

    # Validação primeiro, sempre: YAML errado tem de derrubar a execução ANTES
    # de gastar token com o modelo.
    try:
        cenarios = mod_cenario.carregar_todos(args.pasta, args.cenario)
    except mod_cenario.ErroDeCenario as err:
        config.erro_fatal(f"cenário inválido:\n{err}")

    # Telefone próprio por cenário, senão o loop-guard pausa o contato no meio
    # da execução — ver atribuir_contatos().
    mod_cenario.atribuir_contatos(cenarios)

    if args.lista:
        for c in cenarios:
            print(f"{c.id}\n    {c.descricao or '(sem descrição)'}")
        return 0

    try:
        cfg = config.carregar()
    except config.ErroDeConfig as err:
        config.erro_fatal(str(err))

    cliente = webhook.Cliente(cfg)
    try:
        cliente.conferir_servidor()
    except webhook.ServidorForaDoAr as err:
        config.erro_fatal(str(err))

    calendario = google_calendar.Calendario(cfg)
    try:
        conta = calendario.conferir_conta()
    except google_calendar.ErroDeCalendario as err:
        config.erro_fatal(f"Google Calendar: {err}")

    print(f"sofia-eval · servidor {cfg.sofia_url} · banco {config.BANCO_EXIGIDO} · agenda {conta}")
    print(f"{len(cenarios)} cenário(s) — cada turno é uma chamada real de LLM, isto leva alguns minutos.")

    try:
        conexao = banco.conectar(cfg.database_url)
    except Exception as err:
        config.erro_fatal(
            f"não consegui conectar no banco {config.BANCO_EXIGIDO}: {err}\n"
            "O PostgreSQL está de pé? A DATABASE_URL do eval é a mesma que o sofia-bot usa?"
        )

    resultados = []
    fatal = None
    with conexao as conn:
        for c in cenarios:
            try:
                resultados.append(rodar(conn, cliente, calendario, cfg, c))
            except (google_calendar.ErroDeCalendario, webhook.ServidorForaDoAr) as err:
                # Infraestrutura: seguir daqui produziria veredito mentiroso
                # nos cenários seguintes. Para na hora.
                fatal = f"{type(err).__name__} no cenário {c.id}:\n{err}"
                break

        # Não deixar sobra em mensagens_pendentes: linhas 'erro' continuam
        # elegíveis pro job de recuperação do sofia-bot, que roda ao subir o
        # servidor. Com o RESTART IDENTITY do TRUNCATE, os ids que esse job
        # carrega em memória colidem com linhas NOVAS de uma execução seguinte,
        # e ele carimba status na linha errada. Limpar no fim fecha isso.
        mod_tenant.limpar(conn)

    codigo = relatorio.imprimir(resultados) if resultados else 1
    if resultados:
        caminho_html = relatorio_html.gerar(resultados, cfg)
        if caminho_html:
            print(relatorio.cinza(f"relatório HTML: {caminho_html}"))
    if fatal:
        print(f"\n\033[31mexecução interrompida — {fatal}\033[0m\n", file=sys.stderr)
        return 2
    return codigo


def rodar(conn, cliente, calendario, cfg, c) -> relatorio.Resultado:
    print(f"\n▸ {c.id} … ", end="", flush=True)
    comeco = time.monotonic()

    hoje = datas.hoje(c.timezone)
    # Antes: o cenário não pode herdar evento do anterior.
    calendario.limpar(hoje, cfg.janela_limpeza_dias)
    mod_tenant.limpar(conn)
    tenant = mod_tenant.criar(conn, c, cfg)

    resultado = None
    checagens = []
    try:
        try:
            agenda.semear(conn, calendario, c, tenant)
        except agenda.ErroDeSemeadura as err:
            print(relatorio.ERRO)
            # Sai pelo `finally` como qualquer outro caminho, para a evidência
            # ser capturada também quando a semeadura é que quebrou.
            resultado = relatorio.Resultado(
                c.id, relatorio.ERRO, [str(err)], {}, time.monotonic() - comeco
            )
            return resultado

        ids_antes = {a["id"] for a in banco.agendamentos_ativos(conn, tenant["id"])}

        try:
            for i, texto in enumerate(c.turnos, start=1):
                turnos.enviar_turno(conn, cliente, cfg, tenant["id"], c.contato, texto, i)
        except (turnos.TurnoNaoProcessou, webhook.ErroDeWebhook) as err:
            if isinstance(err, webhook.ServidorForaDoAr):
                raise
            custo = banco.custo(conn, tenant["id"])
            resultado = relatorio.Resultado(
                c.id, relatorio.ERRO, [str(err)], custo, time.monotonic() - comeco,
                banco.resposta_da_assistente(conn, tenant["id"], c.contato),
            )
        else:
            # Turno degradado é ERRO, não veredito — e tem de ser conferido
            # ANTES das verificações, senão vira VERDE FALSO: sem resposta do
            # modelo, `agendamentos: 0` e `sem_agendamento_novo: true` ficam
            # satisfeitos por vacuidade. Medido em 03/09 com o modelo sob
            # rate limit (429): 2 chamadas, 0 tokens, cenário PASSOU.
            # DOIS detectores, e um NÃO substitui o outro: a assinatura pega o
            # 429 que mata a iteração 1; o texto sentinela pega o turno
            # PARCIALMENTE degradado, que a assinatura não distingue de um
            # saudável. Medido em 03/09 — a forma parcial passou verde com a
            # assinatura sozinha. Ver os cabeçalhos das duas funções.
            por_assinatura = banco.turnos_degradados(conn, tenant["id"])
            por_texto = banco.turnos_degradados_por_texto(conn, tenant["id"])
            if por_assinatura or por_texto:
                custo = banco.custo(conn, tenant["id"])
                print(relatorio.ERRO)
                resultado = relatorio.Resultado(
                    c.id, relatorio.ERRO,
                    [f"turno(s) degradado(s) — por assinatura: {por_assinatura}, "
                     f"por texto de desculpa: {por_texto}. A API respondeu sem choice "
                     "utilizável e o modelo não disse o que o cenário mede; qualquer "
                     "veredito aqui seria sobre o silêncio, não sobre o comportamento. "
                     "O motivo estruturado está no log do servidor: procure "
                     "'resposta sem choice utilizável'."],
                    custo, time.monotonic() - comeco,
                    banco.resposta_da_assistente(conn, tenant["id"], c.contato),
                )
                return resultado

            falhas, custo, checagens = verificacoes.aplicar(conn, tenant, c, ids_antes)
            resultado = relatorio.Resultado(
                c.id,
                relatorio.PASSOU if not falhas else relatorio.FALHOU,
                falhas,
                custo,
                time.monotonic() - comeco,
                None if not falhas else banco.resposta_da_assistente(conn, tenant["id"], c.contato),
            )
    finally:
        # Evidência ANTES de qualquer limpeza: o TRUNCATE da entrada do próximo
        # cenário leva junto o estado que explica a falha, e é autocommit — não
        # há como voltar atrás depois. Só no caminho de falha, e nunca fatal.
        if resultado is not None and resultado.veredito != relatorio.PASSOU:
            resultado.evidencia = evidencia.capturar(conn, tenant, c, resultado)
        # Dossiê do relatório HTML: mesma janela de tempo e mesmo motivo da
        # evidência acima — a conexão ainda está viva e `messages`/`ai_usage`
        # ainda não foram apagados pelo cenário seguinte. Ao contrário da
        # evidência, roda para TODOS os veredictos (o relatório mostra também
        # os que passaram); nunca fatal.
        if resultado is not None:
            resultado.dossie = relatorio_html.coletar_dossie(conn, tenant, c, checagens)
        # Depois: mesmo se o cenário explodiu, a agenda volta limpa. Falha aqui
        # sobe e derruba a execução — nunca segue em silêncio.
        calendario.limpar(hoje, cfg.janela_limpeza_dias)

    print(resultado.veredito)
    return resultado


if __name__ == "__main__":
    sys.exit(main())
