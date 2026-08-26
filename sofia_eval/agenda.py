"""Semeia `agenda_ocupada` — o estado que já existia quando o contato escreveu.

Compromisso sem `telefone` é só bloqueio na agenda (o dono marcou algo à mão).
Com `telefone`, é agendamento de OUTRA pessoa: evento no Calendar mais linha em
`appointments`, ligados pelo google_event_id, exatamente como `createEvent`
deixaria.
"""

from datetime import timedelta

from . import datas


class ErroDeSemeadura(Exception):
    pass


def semear(conn, calendario, cenario, tenant) -> int:
    tz = tenant["timezone"]
    criados = 0
    for i, item in enumerate(cenario.agenda_ocupada):
        try:
            dia = datas.resolver(item["data"], tz)
        except ValueError as err:
            raise ErroDeSemeadura(f"agenda_ocupada[{i}]: {err}") from None

        inicio = datas.instante(dia, item["horario"], tz)
        fim = inicio + timedelta(minutes=item["duracao_minutos"])
        titulo = item.get("titulo") or "Compromisso existente"

        # `cancelEvent` do sofia-bot só reconhece um evento como sendo de
        # alguém quando o telefone aparece na DESCRIÇÃO — é assim que
        # `createEvent` grava ("Contato: <telefone>"). Sem isso, o cenário
        # `cancelar-de-terceiro` passaria de graça: o evento não pertenceria a
        # ninguém, e a recusa não provaria nada sobre a trava de dono.
        descricao = "Semeado pelo sofia-eval (agenda_ocupada)."
        if item.get("telefone"):
            descricao = f"Serviço: Atendimento\nContato: {item['telefone']}\n" + descricao

        evento = calendario.criar(inicio, fim, titulo, tz, descricao)
        criados += 1

        if not item.get("telefone"):
            continue

        professional_id = None
        if item.get("profissional"):
            linha = conn.execute(
                "SELECT id FROM professionals WHERE tenant_id = %s AND lower(name) = lower(%s)",
                (tenant["id"], item["profissional"]),
            ).fetchone()
            if not linha:
                raise ErroDeSemeadura(
                    f"agenda_ocupada[{i}]: profissional {item['profissional']!r} não existe "
                    "em tenant.profissionais"
                )
            professional_id = linha["id"]

        conn.execute(
            """
            INSERT INTO appointments (tenant_id, professional_id, telefone, inicio, fim,
                                      google_event_id, google_event_link)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                tenant["id"],
                professional_id,
                item["telefone"],
                inicio,
                fim,
                evento.get("id"),
                evento.get("htmlLink"),
            ),
        )
    return criados
