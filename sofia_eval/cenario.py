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
        _validar(prof, PROFISSIONAL, caminho, f"tenant.profissionais[{i}]")

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
