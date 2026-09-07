"""Estado limpo por cenário: TRUNCATE + tenant descrito no YAML.

A lista de tabelas é a MESMA de tests/helpers/../tests/setup.js do sofia-bot,
de propósito — se lá muda, aqui tem de mudar junto, senão um cenário herda
estado do anterior.
"""

import hashlib

from . import datas


def phone_number_id_do_cenario(base: str, cenario_id: str) -> str:
    """Um `phone_number_id` POR CENÁRIO, e não é cosmética.

    `getTenantByPhoneNumberId` (`src/tenants.js` do sofia-bot) cacheia por
    `phone_number_id`, com `CACHE_TTL_MS = 30_000`. Enquanto todos os cenários
    partilhavam um id só, o cenário seguinte era atendido com o tenant do
    ANTERIOR até o TTL expirar — o `slug` dele no log e, o que importa, o
    `system_prompt_extra` dele no prompt de sistema.

    MEDIDO EM 07/09, e o custo foi um diagnóstico inteiro: o
    `delegacao-fora-da-grade` corria sem a regra dos três casos do próprio
    tenant e oferecia horário em vez de delegar — 5 chamadas em vez de 2,
    sempre verde quando ABRIA a rodada, vermelho quando vinha depois de outro
    cenário. Passou por regressão da branch `prompt-condicional` até o
    controle contra a `main` reproduzir o mesmo padrão (PASSOU/FALHOU/PASSOU/
    degradado nas duas). Não era da branch: era este cache.

    O TTL de 30s NÃO é defeito de produção — lá cada tenant tem o seu
    `phone_number_id`, e o cache é deliberado. Quem tinha de variar era o eval.

    Numérico como um id real da Meta, e MAIS LONGO que um: o sufixo derivado
    mantém a promessa do comentário em `criar` de que nenhum tenant real usa
    estes números. Determinístico para a rodada ser reproduzível.
    """
    sufixo = int(hashlib.sha1(cenario_id.encode("utf-8")).hexdigest(), 16) % 10**6
    return f"{base}{sufixo:06d}"

# Espelha tests/setup.js do sofia-bot (RESTART IDENTITY CASCADE inclusive).
TABELAS = (
    "coex_invites, appointments, mensagens_pendentes, messages, ai_usage, "
    "reminders_sent, products, professional_availability, "
    "professional_availability_exceptions, professionals, tenants"
)

# Defaults iguais aos de tests/helpers/fixtures.js — o eval não inventa
# configuração de tenant, só troca o que o YAML pedir.
PADRAO = {
    "bot_name": "Sofia",
    "business_name": "Clínica de Teste",
    "system_prompt_extra": None,
    # NULL herda o comportamento de hoje: openrouter.js (linha 43) resolve
    # `tenant.openrouter_model || config.openrouter.model`, então sem valor
    # aqui o modelo é o que estiver no OPENROUTER_MODEL do .env do sofia-bot —
    # e isso não aparecia declarado em lugar nenhum. Cenário que precisa de
    # teto calibrado contra um modelo específico declara `tenant.openrouter_model`
    # no YAML; o efetivo sempre fica carimbado no relatório HTML (banco.modelos,
    # que lê de `ai_usage.model` — o que de fato atendeu, não o que a config dizia).
    "openrouter_model": None,
    "timezone": "America/Sao_Paulo",
    "service_duration_minutes": 60,
    "slot_interval_minutes": 30,
    "working_days": "0,1,2,3,4,5,6",
    "horario_manha_inicio": "08:00",
    "horario_manha_fim": "12:00",
    "horario_tarde_inicio": "13:00",
    "horario_tarde_fim": "18:00",
    "horario_noite_inicio": "18:00",
    "horario_noite_fim": "20:00",
    # Mesmo default da coluna (`false`), de propósito: o cenário que não
    # declara nada continua a medir o caminho comum, que é o de quase todo
    # tenant. Ligar por engano não é neutro — o silêncio por echo do dono
    # passa a existir e pode calar o contato no meio do cenário.
    "coexistencia": False,
}


def limpar(conn) -> None:
    conn.execute(f"TRUNCATE {TABELAS} RESTART IDENTITY CASCADE;")


def criar(conn, cenario, cfg) -> dict:
    """Cria o tenant do cenário e devolve a linha inteira."""
    campos = dict(PADRAO)
    campos.update(cenario.tenant)
    campos.update(
        {
            "slug": f"eval-{cenario.id}",
            # Fictício: nenhum tenant real usa este phone_number_id. Próprio
            # de cada cenário — ver phone_number_id_do_cenario.
            "phone_number_id": phone_number_id_do_cenario(cfg.phone_number_id, cenario.id),
            # Token falso — nada sai pra Meta. A resposta da assistente é lida
            # de `messages`, onde pushTurn grava ANTES do envio.
            "whatsapp_access_token": "token-falso-do-eval",
            "google_client_id": cfg.google_client_id,
            "google_client_secret": cfg.google_client_secret,
            "google_refresh_token": cfg.google_refresh_token,
            "google_calendar_id": cfg.google_calendar_id,
            "active": True,
        }
    )

    colunas = list(campos)
    placeholders = ", ".join(["%s"] * len(colunas))
    linha = conn.execute(
        f"INSERT INTO tenants ({', '.join(colunas)}) VALUES ({placeholders}) RETURNING *",
        [campos[c] for c in colunas],
    ).fetchone()

    for i, prof in enumerate(cenario.profissionais):
        profissional = conn.execute(
            """
            INSERT INTO professionals (tenant_id, name, google_calendar_id, active, sort_order,
                                       service_duration_minutes)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                linha["id"],
                prof["nome"],
                cfg.google_calendar_id,
                # Padrão ativo; `ativo: false` reproduz profissional cadastrado
                # que o bot não oferece.
                prof.get("ativo", True),
                prof.get("sort_order", i),
                # NULL = herda a duração do tenant, igual à fixture do sofia-bot.
                prof.get("service_duration_minutes"),
            ),
        ).fetchone()
        _semear_grade(conn, profissional["id"], prof, linha["timezone"])

    return linha


def _semear_grade(conn, professional_id: int, prof: dict, timezone: str) -> None:
    """Regra recorrente semanal e exceções pontuais de UM profissional.

    As duas tabelas já estão em `TABELAS` (o TRUNCATE de entrada as limpa), e
    a semeadura tem de ficar simétrica com isso: tabela semeada e não
    truncada vaza estado de um cenário para o seguinte.

    Semear QUALQUER linha aqui liga a restrição por grade para este
    profissional — `temGradeConfigurada` (availability.js) só olha se existe
    alguma linha, em qualquer das duas tabelas. Profissional sem grade
    nenhuma continua irrestrito, que é o caso de todos os cenários que não
    usam estas chaves."""
    for regra in prof.get("grade") or []:
        conn.execute(
            """
            INSERT INTO professional_availability (professional_id, dia_semana, hora_inicio, hora_fim)
            VALUES (%s, %s, %s, %s)
            """,
            (professional_id, regra["dia_semana"], regra["hora_inicio"], regra["hora_fim"]),
        )

    for excecao in prof.get("excecoes") or []:
        # Data relativa (`+2`, `hoje`) resolvida no fuso do tenant, igual à de
        # `agenda_ocupada` — cenário com data fixa envelhece.
        dia = datas.resolver(excecao["data"], timezone)
        conn.execute(
            """
            INSERT INTO professional_availability_exceptions
                        (professional_id, data, hora_inicio, hora_fim, tipo)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (professional_id, dia, excecao["hora_inicio"], excecao["hora_fim"], excecao["tipo"]),
        )
