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
import uuid

from . import banco

FINAIS = ("concluida", "erro")


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
