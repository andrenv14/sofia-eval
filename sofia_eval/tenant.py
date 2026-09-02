"""Estado limpo por cenário: TRUNCATE + tenant descrito no YAML.

A lista de tabelas é a MESMA de tests/helpers/../tests/setup.js do sofia-bot,
de propósito — se lá muda, aqui tem de mudar junto, senão um cenário herda
estado do anterior.
"""

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
            # Fictício: nenhum tenant real usa este phone_number_id.
            "phone_number_id": cfg.phone_number_id,
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
        conn.execute(
            """
            INSERT INTO professionals (tenant_id, name, google_calendar_id, active, sort_order,
                                       service_duration_minutes)
            VALUES (%s, %s, %s, true, %s, %s)
            """,
            (
                linha["id"],
                prof["nome"],
                cfg.google_calendar_id,
                prof.get("sort_order", i),
                # NULL = herda a duração do tenant, igual à fixture do sofia-bot.
                prof.get("service_duration_minutes"),
            ),
        )

    return linha
