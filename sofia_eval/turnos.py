"""Envio de turnos e espera pelo processamento de verdade.

Dois detalhes do sofia-bot mandam neste arquivo:

1. **O buffer tem atraso.** `BUFFER_DELAY_MS` é 6000ms por padrão (override
   `BUFFER_DELAY_MS_OVERRIDE`), com teto absoluto `BUFFER_MAX_WAIT_MS` de
   12000ms contado da primeira mensagem do lote. Consultar o banco logo depois
   do POST lê estado incompleto — e, pior, mandar o turno seguinte cedo demais
   faria o debounce AGRUPAR os dois num turno só. Por isso cada turno espera o
   anterior terminar, com timeout e falha explícita. Sem sleep fixo.

2. **wamid único por turno.** O dedup tem duas camadas, as duas silenciosas:
   um mapa em memória no servidor (TTL de 10 min, sobrevive entre cenários) e
   o índice único de `mensagens_pendentes.wamid`. Repetir wamid faz a mensagem
   ser descartada sem nenhum sinal — daí o uuid4.
"""

import time
from datetime import datetime, timezone
import uuid

from . import banco

# Os TRÊS estados terminais de `mensagens_pendentes`, não dois.
#
# `'nao_enviada'` faltava aqui até 07/09, e a falta tinha consequência: ele é
# gravado em SEIS pontos de `processarBuffer` (`src/server.js`) — gate do
# silêncio, corte do loop-guard, e cada bloqueio da arbitragem de envio — e o
# próprio código o documenta como terminal ("'nao_enviada' é terminal DE
# PROPÓSITO (P4)"), com a limpeza de 7 dias a tratá-lo junto de `'concluida'`.
#
# Sem ele, um cenário em que o loop-guard corta — o que acontece quando a Sofia
# repete a mesma resposta quatro vezes, que é exactamente o que o fallback "deu
# uma travada" produz quando as iterações esgotam — esperava até o timeout e
# saía ERRO por algo que o cenário não mede. Medido em produção em 07/09:
# "esgotou 5 iterações" ×4 → loop-guard pausou 60 min.
FINAIS = ("concluida", "erro", "nao_enviada")


class TurnoNaoProcessou(Exception):
    pass


def novo_wamid() -> str:
    # Único por processo E por cenário: o dedup em memória do servidor guarda
    # wamids por 10 minutos, atravessando cenários.
    return f"wamid.eval-{uuid.uuid4().hex}"


def enviar_turno(conn, cliente, cfg, tenant_id: int, telefone: str, texto: str, indice: int) -> str:
    """Manda um turno e só volta quando ele saiu de 'pendente'/'processando'."""
    antes = banco.turnos_da_assistente(conn, tenant_id, telefone)
    wamid = novo_wamid()
    cliente.enviar(telefone, texto, wamid)
    _esperar(conn, cfg, wamid, tenant_id, telefone, antes, indice, texto)
    return wamid


def enviar_echo_do_dono(conn, cliente, cfg, tenant_id: int, telefone: str, texto: str, indice: int) -> str:
    """Entrega a fala do dono e espera o EFEITO dela — que não é uma resposta.

    O echo não gera turno da assistente: não é mensagem de cliente, é a Meta a
    devolver o que o dono escreveu pelo app. Quem esperasse resposta aqui
    esperaria até o timeout, e o cenário sairia ERRO por algo que não mede — o
    mesmo mecanismo que obriga a delegação a ser o último turno.

    A barreira é a ÚLTIMA escrita do laço do echo: `registrarFalaDoDono` a pôr
    o texto do dono em `messages` como `assistant`. `silenciarPorEcho` corre
    ANTES dela, então esperar pelo silêncio pararia cedo demais."""
    antes = banco.falas_do_dono(conn, tenant_id, telefone, texto)
    wamid = novo_wamid()
    cliente.enviar_echo_do_dono(telefone, texto, wamid)

    limite = time.monotonic() + cfg.timeout_turno_s
    while time.monotonic() < limite:
        if banco.falas_do_dono(conn, tenant_id, telefone, texto) > antes:
            return wamid
        time.sleep(cfg.poll_intervalo_s)
    raise TurnoNaoProcessou(
        f"turno {indice} (fala do dono): a fala não apareceu em `messages` em "
        f"{cfg.timeout_turno_s:.0f}s. Causas conhecidas, todas do payload ou da "
        "configuração: `field` diferente de `smb_message_echoes`; wamid repetido "
        "(o servidor deduplica); wamid que a própria Sofia emitiu "
        "(`foiEnvioProprio` salta); ou `tenant.coexistencia` esquecido no YAML, "
        "que faz o echo nem ser processado."
    )


def esperar_silencio_passar(conn, cfg, tenant_id: int, telefone: str) -> None:
    """Espera o silêncio do dono expirar antes do próximo turno do paciente.

    Sem isto, a mensagem do paciente é gravada em `messages` e NUNCA respondida
    nem enfileirada — o gate do `sofia-bot` corta antes da IA —, e o
    `enviar_turno` seguinte morre no timeout.

    Lê `contatos_estado.silenciado_ate` da COLUNA em vez de dormir um tempo
    fixo, porque a duração vem de `SILENCIO_DONO_MS_OVERRIDE`, que é lido uma
    vez no arranque do servidor: o eval não a conhece e não deve adivinhá-la."""
    limite = time.monotonic() + cfg.timeout_turno_s
    while time.monotonic() < limite:
        ate = banco.silenciado_ate(conn, tenant_id, telefone)
        if ate is None or ate <= datetime.now(timezone.utc):
            return
        time.sleep(cfg.poll_intervalo_s)
    raise TurnoNaoProcessou(
        f"o silêncio do dono não expirou em {cfg.timeout_turno_s:.0f}s. Suba o "
        "servidor com `SILENCIO_DONO_MS_OVERRIDE` pequeno (a suíte do sofia-bot "
        "usa 1500); o valor por omissão é de 15 minutos e nenhum cenário espera isso."
    )


def _esperar(conn, cfg, wamid: str, tenant_id: int, telefone: str, antes: int, indice: int, texto: str):
    """Espera o EFEITO do turno, não o status da fila.

    O sinal de sucesso é o turno da assistente aparecer em `messages`, gravado
    por `pushTurn` DENTRO de handleUserMessage — ou seja, depois de todas as
    ferramentas terem rodado e antes do envio pra Meta. Quando ele existe, tudo
    que o eval julga (appointments, ai_usage) já está no banco.

    O status da fila é sinal SECUNDÁRIO, e de propósito. Dois motivos:

    1. 'erro' é o estado normal aqui — o token da Meta é falso, `sendText`
       sempre estoura 401 e `processarBuffer` marca 'erro'. A spec já previu
       isso: o envio falhar é irrelevante pro julgamento.
    2. O status pode ser carimbado por um job ALHEIO. Se o servidor subir com
       sobras em `mensagens_pendentes` (linhas 'erro' continuam elegíveis pra
       reprocessar), o job de recuperação guarda os ids delas em memória; o
       TRUNCATE ... RESTART IDENTITY do eval faz uma linha NOVA nascer com o
       mesmo id, e o UPDATE final do job velho carimba a linha nova. Confiar só
       no status faz o eval julgar um turno que ainda está rodando.

    Por isso 'erro' só vira falha depois de uma carência sem o turno aparecer.
    """
    limite = time.monotonic() + cfg.timeout_turno_s
    apareceu = False
    status_final_em = None
    while time.monotonic() < limite:
        if banco.turnos_da_assistente(conn, tenant_id, telefone) > antes:
            return

        status = banco.status_pendente(conn, wamid)
        if status is not None:
            apareceu = True
        if status in FINAIS:
            if status_final_em is None:
                status_final_em = time.monotonic()
            elif time.monotonic() - status_final_em >= cfg.carencia_status_s:
                raise TurnoNaoProcessou(
                    f"turno {indice} ({texto!r}): a fila marcou {status!r} e a assistente não "
                    f"gravou resposta nenhuma em `messages` em {cfg.carencia_status_s:.0f}s.\n"
                    "A exceção veio antes do envio — veja o log do servidor.\n"
                    "Causas comuns: OpenRouter fora do ar, chave sem saldo, ou modelo inválido."
                )
        time.sleep(cfg.poll_intervalo_s)

    if not apareceu:
        raise TurnoNaoProcessou(
            f"turno {indice} ({texto!r}): a mensagem nunca chegou em mensagens_pendentes "
            f"em {cfg.timeout_turno_s:.0f}s.\n"
            "O webhook aceitou o POST, então o servidor está de pé. O que costuma explicar isto:\n"
            "  - nenhum tenant cadastrado para o phone_number_id enviado (veja o log do sofia-bot);\n"
            "  - o loop-guard pausou o contato — nesse caso a mensagem é descartada antes da fila;\n"
            "  - o wamid foi recusado pelo dedup (não deveria acontecer: cada turno gera um novo)."
        )
    raise TurnoNaoProcessou(
        f"turno {indice} ({texto!r}): a assistente não gravou resposta em "
        f"{cfg.timeout_turno_s:.0f}s.\n"
        "O buffer disparou mas a resposta não fechou. Normalmente é a chamada de LLM demorando "
        "mais que o teto — suba EVAL_TIMEOUT_TURNO_S, ou veja o log do servidor."
    )
