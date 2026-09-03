"""Carga e validação dos YAML de cenário.

Regra central da spec: **chave desconhecida é erro, não é ignorada**. Uma
verificação escrita errado que passa em silêncio é pior que verificação
nenhuma — então todo dicionário do YAML passa por um esquema fechado, e
qualquer chave fora dele derruba a execução inteira antes de gastar um token.
"""

import uuid
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import datas

# ---- Esquemas fechados -------------------------------------------------
# Cada entrada: nome -> (tipos aceitos, obrigatória?)

RAIZ = {
    "id": (str, True),
    "descricao": (str, False),
    "contato": (str, False),
    "tenant": (dict, False),
    "agenda_ocupada": (list, False),
    "turnos": (list, True),
    "verificacoes": (dict, True),
}

TENANT = {
    "timezone": (str, False),
    "bot_name": (str, False),
    "business_name": (str, False),
    "system_prompt_extra": (str, False),
    # O modelo sob avaliação é AMBIENTE por padrão (herda o .env do sofia-bot),
    # não declarado — achado da guia em 02/09. Declarar aqui só quando o
    # cenário tem teto calibrado contra um modelo específico; sem isso, o
    # tenant nasce com `openrouter_model` NULL (tenant.py PADRAO) e o
    # comportamento de hoje não muda.
    "openrouter_model": (str, False),
    "service_duration_minutes": (int, False),
    "slot_interval_minutes": (int, False),
    "working_days": (str, False),
    "horario_manha_inicio": (str, False),
    "horario_manha_fim": (str, False),
    "horario_tarde_inicio": (str, False),
    "horario_tarde_fim": (str, False),
    "horario_noite_inicio": (str, False),
    "horario_noite_fim": (str, False),
    "profissionais": (list, False),
}

PROFISSIONAL = {
    "nome": (str, True),
    "service_duration_minutes": (int, False),
    "sort_order": (int, False),
    # Profissional inativo continua na tabela e NÃO é oferecido:
    # `carregarProfissionaisAtivos` (sofia-bot, `professionals/professionals.js`)
    # filtra por
    # `active`. Existe para o cenário poder reproduzir a configuração real de
    # um tenant — "4 cadastrados, 3 agendáveis" é diferente de "4 agendáveis",
    # e escrever 4 agendáveis mede outro negócio.
    "ativo": (bool, False),
    # Grade de disponibilidade do profissional — regra recorrente semanal e
    # exceções pontuais (migration_2026-08-15_disponibilidade_profissionais).
    # CUIDADO, é armadilha: `temGradeConfigurada` (availability.js) liga a
    # restrição se houver QUALQUER linha em QUALQUER das duas tabelas. Ou
    # seja, semear UMA regra de segunda deixa o profissional indisponível de
    # terça a domingo. Profissional sem nenhuma linha não é restringido —
    # é o padrão de todos os cenários que não usam estas chaves.
    "grade": (list, False),
    "excecoes": (list, False),
}

GRADE = {
    # 0=domingo .. 6=sábado, mesma convenção de `tenants.working_days`.
    "dia_semana": (int, True),
    "hora_inicio": (str, True),
    "hora_fim": (str, True),
}

EXCECAO = {
    "data": ((str, int), True),
    "hora_inicio": (str, True),
    "hora_fim": (str, True),
    # `bloqueio` fecha horário que a regra recorrente abriria; `liberacao`
    # abre horário que ela não cobre. Exceção SEMPRE vence a regra
    # (availability.js, `slotPermitidoPelaGrade`).
    "tipo": (str, True),
}

OCUPADO = {
    "data": ((str, int), True),
    "horario": (str, True),
    "duracao_minutos": (int, True),
    "titulo": (str, False),
    "profissional": (str, False),
    # Com `telefone`, o compromisso não é só um bloqueio na agenda: vira
    # AGENDAMENTO de verdade daquele número — linha em `appointments` ligada
    # ao evento do Calendar. É o que permite ao cenário `cancelar-de-terceiro`
    # ter um agendamento alheio para tentar (e não conseguir) cancelar.
    "telefone": (str, False),
}

VERIFICACOES = {
    "agendamentos": (int, False),
    "agendamento": (dict, False),
    "sem_agendamento_novo": (bool, False),
    "chamadas_ia_max": (int, False),
    "tokens_prompt_max": (int, False),
    # Guarda de COMPORTAMENTO (desengajar), não de custo — teto de linhas
    # `messages` com role='assistant' para o contato do cenário. Existe para
    # o achado de tráfego real "bot-a-bot: detectar não basta, tem de
    # desengajar" (31/08): o loop-guard é backstop quantitativo (35 msgs/120s,
    # 60/h) e não pega uma troca de 4 mensagens em 50s. A consulta já existia
    # sem uso — banco.turnos_da_assistente.
    "respostas_assistente_max": (int, False),
    # Estado de linhas ESPECÍFICAS de `appointments`, identificadas por
    # (telefone, data, horario) — ao contrário de `agendamentos` (agregado) e
    # `agendamento` (só a mais nova), afere "a linha CERTA mudou", não só "o
    # total mudou". Existe para cenários com mais de um agendamento em jogo
    # (ex.: cancelamento correto sem mexer no de terceiro) — a mesma
    # categoria de bug que `nome_profissional` e linha órfã, onde o total
    # batia e a linha errada é que estava errada.
    "agendamento_status": (list, False),
}

AGENDAMENTO_STATUS_ITEM = {
    "telefone": (str, True),
    "data": ((str, int), True),
    "horario": (str, True),
    "status": (str, True),
}

AGENDAMENTO = {
    "duracao_minutos": (int, False),
    "profissional": (str, False),
    "telefone": (str, False),
    # `data` e `horario` NÃO estão na tabela da spec. Foram acrescentados
    # porque sem eles o cenário `data-relativa` não afere nada: ele existe pra
    # provar que "depois de amanhã" cai no dia certo, no fuso do tenant, e a
    # única forma de ver isso no banco é olhando `appointments.inicio`.
    "data": ((str, int), False),
    "horario": (str, False),
}


class ErroDeCenario(Exception):
    pass


def _minutos(valor, caminho, onde) -> int:
    """'HH:MM' -> minutos desde meia-noite, ou erro de cenário.

    Existe para as janelas de grade serem conferidas AQUI, e não pelo
    Postgres no meio da execução: as duas tabelas têm `CHECK (hora_fim >
    hora_inicio)`, e violar isso na semeadura estoura com erro cru de driver
    depois que a rodada já começou a gastar token."""
    texto = str(valor).strip()
    # 'HH:MM' e 'HH:MM:SS' — o segundo formato é o que o Postgres devolve, e é
    # o que sai no JSON de evidência; recusá-lo faria copiar um valor da
    # evidência de volta para um YAML quebrar a validação sem motivo.
    partes = texto.split(":")
    if not 2 <= len(partes) <= 3:
        raise ErroDeCenario(
            f"{caminho}: `{onde}` deveria ser um horário 'HH:MM' ou 'HH:MM:SS', veio {valor!r}"
        )
    try:
        h, m = int(partes[0]), int(partes[1])
        s = int(partes[2]) if len(partes) == 3 else 0
    except ValueError:
        raise ErroDeCenario(
            f"{caminho}: `{onde}` deveria ser um horário 'HH:MM' ou 'HH:MM:SS', veio {valor!r}"
        ) from None
    # 24:00 é fim-de-dia válido em TIME do Postgres — aceito só redondo.
    if (h, m, s) == (24, 0, 0):
        return 24 * 60
    if not (0 <= h <= 23 and 0 <= m <= 59 and 0 <= s <= 59):
        raise ErroDeCenario(f"{caminho}: `{onde}` fora de um relógio de 24h: {valor!r}")
    return h * 60 + m


def _validar_janela(bloco, caminho, onde) -> None:
    """`hora_fim > hora_inicio`, o mesmo CHECK das duas tabelas do sofia-bot."""
    inicio = _minutos(bloco["hora_inicio"], caminho, f"{onde}.hora_inicio")
    fim = _minutos(bloco["hora_fim"], caminho, f"{onde}.hora_fim")
    if fim <= inicio:
        raise ErroDeCenario(
            f"{caminho}: `{onde}` tem hora_fim {bloco['hora_fim']!r} <= hora_inicio "
            f"{bloco['hora_inicio']!r} — o banco recusaria (CHECK hora_fim > hora_inicio)"
        )


def _validar(bloco, esquema, caminho, onde):
    if not isinstance(bloco, dict):
        raise ErroDeCenario(f"{caminho}: `{onde}` deveria ser um mapa, veio {type(bloco).__name__}")
    desconhecidas = sorted(set(bloco) - set(esquema))
    if desconhecidas:
        raise ErroDeCenario(
            f"{caminho}: chave desconhecida em `{onde}`: "
            + ", ".join(repr(c) for c in desconhecidas)
            + "\nAceitas aqui: "
            + ", ".join(sorted(esquema))
        )
    for nome, (tipos, obrigatoria) in esquema.items():
        if nome not in bloco:
            if obrigatoria:
                raise ErroDeCenario(f"{caminho}: falta a chave obrigatória `{onde}.{nome}`")
            continue
        valor = bloco[nome]
        # bool é subclasse de int em Python — sem esta guarda, `agendamentos: true`
        # passaria como se fosse o número 1.
        if tipos is int and isinstance(valor, bool):
            raise ErroDeCenario(f"{caminho}: `{onde}.{nome}` deveria ser inteiro, veio booleano")
        if not isinstance(valor, tipos):
            esperado = tipos.__name__ if isinstance(tipos, type) else "/".join(t.__name__ for t in tipos)
            raise ErroDeCenario(
                f"{caminho}: `{onde}.{nome}` deveria ser {esperado}, veio {type(valor).__name__}"
            )
    return bloco


@dataclass
class Cenario:
    id: str
    caminho: Path
    descricao: str = ""
    contato: str = None
    tenant: dict = field(default_factory=dict)
    profissionais: list = field(default_factory=list)
    agenda_ocupada: list = field(default_factory=list)
    turnos: list = field(default_factory=list)
    verificacoes: dict = field(default_factory=dict)

    @property
    def timezone(self) -> str:
        return self.tenant.get("timezone", "America/Sao_Paulo")


def carregar(caminho: Path) -> Cenario:
    try:
        bruto = yaml.safe_load(caminho.read_text(encoding="utf-8"))
    except yaml.YAMLError as err:
        raise ErroDeCenario(f"{caminho}: YAML inválido — {err}") from None
    if bruto is None:
        raise ErroDeCenario(f"{caminho}: arquivo vazio")

    _validar(bruto, RAIZ, caminho, "raiz")

    tenant = dict(bruto.get("tenant") or {})
    _validar(tenant, TENANT, caminho, "tenant")
    profissionais = tenant.pop("profissionais", None) or []
    for i, prof in enumerate(profissionais):
        onde_prof = f"tenant.profissionais[{i}]"
        _validar(prof, PROFISSIONAL, caminho, onde_prof)

        for j, regra in enumerate(prof.get("grade") or []):
            onde = f"{onde_prof}.grade[{j}]"
            _validar(regra, GRADE, caminho, onde)
            if not 0 <= regra["dia_semana"] <= 6:
                raise ErroDeCenario(
                    f"{caminho}: `{onde}.dia_semana` deveria estar entre 0 (domingo) e 6 "
                    f"(sábado), veio {regra['dia_semana']}"
                )
            _validar_janela(regra, caminho, onde)

        for j, excecao in enumerate(prof.get("excecoes") or []):
            onde = f"{onde_prof}.excecoes[{j}]"
            _validar(excecao, EXCECAO, caminho, onde)
            # A data é resolvida aqui, no carregamento, e não só na semeadura:
            # `tenant.criar` roda FORA do try/finally de `__main__.rodar`, então
            # um ValueError cru de `datas.resolver` lá derrubaria a execução
            # inteira — sem tabela, sem relatório e sem limpeza, jogando fora os
            # cenários que já custaram token. A armadilha concreta é `data: +2`
            # sem aspas: YAML lê como o inteiro 2, que não é data nenhuma (e
            # `-2`, também sem aspas, funciona — assimetria silenciosa).
            try:
                datas.resolver(excecao["data"], tenant.get("timezone", "America/Sao_Paulo"))
            except ValueError as err:
                raise ErroDeCenario(
                    f"{caminho}: `{onde}.data` — {err}. Datas relativas precisam de aspas "
                    f'no YAML (`data: "+2"`), senão viram número.'
                ) from None
            if excecao["tipo"] not in ("liberacao", "bloqueio"):
                raise ErroDeCenario(
                    f"{caminho}: `{onde}.tipo` deveria ser 'liberacao' ou 'bloqueio' "
                    f"(mesmo CHECK de professional_availability_exceptions), "
                    f"veio {excecao['tipo']!r}"
                )
            _validar_janela(excecao, caminho, onde)

    ocupados = bruto.get("agenda_ocupada") or []
    for i, item in enumerate(ocupados):
        _validar(item, OCUPADO, caminho, f"agenda_ocupada[{i}]")

    verificacoes = dict(bruto["verificacoes"] or {})
    _validar(verificacoes, VERIFICACOES, caminho, "verificacoes")
    if not verificacoes:
        raise ErroDeCenario(f"{caminho}: `verificacoes` está vazio — cenário que não afere nada não é cenário")
    if "agendamento" in verificacoes:
        _validar(verificacoes["agendamento"], AGENDAMENTO, caminho, "verificacoes.agendamento")
        if not verificacoes["agendamento"]:
            raise ErroDeCenario(f"{caminho}: `verificacoes.agendamento` está vazio")

    if "agendamento_status" in verificacoes:
        itens = verificacoes["agendamento_status"]
        if not itens:
            raise ErroDeCenario(f"{caminho}: `verificacoes.agendamento_status` está vazio")
        for i, item in enumerate(itens):
            _validar(item, AGENDAMENTO_STATUS_ITEM, caminho, f"verificacoes.agendamento_status[{i}]")
            if item["status"] not in ("ativo", "cancelado"):
                raise ErroDeCenario(
                    f"{caminho}: `verificacoes.agendamento_status[{i}].status` deveria ser "
                    f"'ativo' ou 'cancelado' (mesmo CHECK de appointments.status no schema), "
                    f"veio {item['status']!r}"
                )

    turnos = bruto["turnos"]
    if not turnos:
        raise ErroDeCenario(f"{caminho}: `turnos` está vazio")
    for i, turno in enumerate(turnos):
        if not isinstance(turno, str) or not turno.strip():
            raise ErroDeCenario(f"{caminho}: `turnos[{i}]` deveria ser um texto não vazio")

    return Cenario(
        id=bruto["id"],
        caminho=caminho,
        descricao=(bruto.get("descricao") or "").strip(),
        contato=bruto.get("contato"),
        tenant=tenant,
        profissionais=profissionais,
        agenda_ocupada=ocupados,
        turnos=turnos,
        verificacoes=verificacoes,
    )


def atribuir_contatos(cenarios: list) -> None:
    """Dá um telefone próprio a cada cenário que não fixou um no YAML.

    Sem isto, todos os cenários falam pelo MESMO número — e o loop-guard do
    sofia-bot pausa esse contato no meio da execução. A chave do guard é
    `tenant.id:telefone`, e `TRUNCATE ... RESTART IDENTITY` faz tenant.id
    voltar a 1 sempre; o estado do guard é module-level na memória do
    servidor, então o TRUNCATE não o limpa. O resultado é a chave
    `1:<telefone>` acumulando mensagens ATRAVÉS de cenários e de execuções
    até estourar o teto de 60 msgs/hora — e daí em diante toda mensagem é
    descartada antes da fila, em silêncio.

    É a mesma armadilha que tests/setup.js resolve chamando limparLoopGuard(),
    que um processo externo não tem como chamar. Com um número por cenário,
    cada chave vê no máximo os turnos daquele cenário.

    O número varia também entre EXECUÇÕES, senão rodar o eval duas vezes
    seguidas somaria na mesma chave.
    """
    execucao = uuid.uuid4().int % 1000
    for i, c in enumerate(cenarios):
        if c.contato is None:
            # 13 dígitos, no formato de celular brasileiro (55 + DDD + 9 dígitos),
            # mas com prefixo 9999 repetido de propósito: número sintético, que
            # não deve coincidir com o de ninguém. Nada é enviado à Meta de
            # qualquer forma — o token é falso.
            c.contato = f"55119999{execucao:03d}{i:02d}"


def carregar_todos(pasta: Path, filtro=None) -> list:
    arquivos = sorted(pasta.glob("*.yaml")) + sorted(pasta.glob("*.yml"))
    if not arquivos:
        raise ErroDeCenario(f"nenhum cenário encontrado em {pasta}")
    cenarios = [carregar(a) for a in arquivos]
    vistos = {}
    for c in cenarios:
        if c.id in vistos:
            raise ErroDeCenario(f"id duplicado {c.id!r}: {vistos[c.id]} e {c.caminho}")
        vistos[c.id] = c.caminho
    if filtro:
        escolhidos = [c for c in cenarios if c.id in filtro]
        faltando = sorted(set(filtro) - {c.id for c in escolhidos})
        if faltando:
            raise ErroDeCenario(
                "cenário inexistente: " + ", ".join(faltando)
                + "\nDisponíveis: " + ", ".join(sorted(vistos))
            )
        return escolhidos
    return cenarios
