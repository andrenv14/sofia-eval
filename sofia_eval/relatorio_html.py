"""Relatório HTML por rodada — degrau 1 do item 8 de docs/contexto/fila.md
(sofia-bot): "saída visual do eval".

O eval julga pelo EFEITO no banco, e a tabela do terminal só mostra o
veredito — não o que a Sofia respondeu. O texto já está gravado em `messages`
(sofia-eval.md: "a v2 pode usá-lo sem mudança de arquitetura"); este módulo só
lê e formata, sem tocar em nenhuma verificação.

Limites duros, da spec do eval: é ARQUIVO, um por execução, HTML estático
auto-contido (CSS inline, zero JS obrigatório). Não muda o julgamento de
nenhum cenário nem a saída do terminal, além da linha final com o caminho.
Zero dependência nova — só `html.escape` e f-strings da biblioteca padrão.

Nunca fatal: like `evidencia.capturar`, uma falha aqui avisa e segue. O
relatório é conveniência de leitura, não parte do portão 0/1.
"""

import subprocess
from datetime import datetime
from html import escape
from pathlib import Path

from . import banco, relatorio

PASTA = Path("~/para-revisao")

# Ferramentas chamadas: fora da v1 deste relatório. Verificado no sofia-bot —
# `pushTurn` grava só role 'user'/'assistant' (sessionStore.js), `ai_usage`
# não tem coluna de nome de ferramenta, e a lista que existe em memória
# (`toolsChamadas`, openrouter.js) só é impressa em stderr no caminho de
# esgotamento de iteração, nunca gravada no Postgres. Sem fonte no banco, não
# inventamos uma — a nota abaixo é o que aparece no lugar.
NOTA_FERRAMENTAS = (
    "Fora da v1 deste relatório: o banco não guarda quais ferramentas foram "
    "chamadas. `pushTurn` grava só as mensagens final de user/assistant "
    "(sessionStore.js), e `ai_usage` não tem coluna de nome de ferramenta — a "
    "lista existe apenas em memória durante o processamento (openrouter.js) e "
    "nunca chega ao Postgres. Ver SUPOSIÇÃO no plano da fatia."
)


def coletar_dossie(conn, tenant, cenario, checagens) -> dict:
    """Reúne o que o relatório precisa e que só existe enquanto a conexão está
    viva, ANTES da limpeza do próximo cenário — mesma janela de tempo e mesmo
    motivo de `evidencia.capturar`. Nunca fatal: falha aqui vira aviso, e o
    dossiê fica None (o HTML mostra o cenário sem transcript)."""
    try:
        return {
            "checagens": checagens,
            "transcricao": banco.transcricao(conn, tenant["id"], cenario.contato),
            "modelos": banco.modelos(conn, tenant["id"]),
        }
    except Exception as err:
        print(
            relatorio.amarelo(f"    (não consegui coletar o dossiê de {cenario.id} para o relatório: {err})"),
            flush=True,
        )
        return None


def gerar(resultados: list, cfg) -> str:
    """Escreve o HTML e devolve o caminho amigável (com ~), ou None se não deu.

    Nunca fatal — chamado depois de `relatorio.imprimir`, o exit code do eval
    não depende disto."""
    try:
        destino = _caminho()
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(_render(resultados, cfg), encoding="utf-8")
        return _amigavel(destino)
    except Exception as err:
        print(relatorio.amarelo(f"(não consegui gerar o relatório HTML: {err})"), flush=True)
        return None


def _caminho() -> Path:
    carimbo = datetime.now().strftime("%Y%m%d-%H%M%S")
    return PASTA.expanduser() / f"relatorio-eval-{carimbo}.html"


def _amigavel(destino: Path) -> str:
    try:
        return f"~/{destino.relative_to(Path.home())}"
    except ValueError:
        return str(destino)


def _sha(diretorio: Path) -> str:
    """SHA curto de um repositório git, com `-sujo` se a árvore não estiver
    limpa. `desconhecido` se o git falhar — nunca derruba o relatório."""
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=diretorio, capture_output=True, text=True, timeout=5, check=True,
        ).stdout.strip()
        sujo = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=diretorio, capture_output=True, text=True, timeout=5, check=True,
        ).stdout.strip()
        return f"{sha}-sujo" if sujo else sha
    except Exception:
        return "desconhecido"


def _render(resultados: list, cfg) -> str:
    passaram = sum(1 for r in resultados if r.ok)
    total = len(resultados)
    agregado = {
        chave: sum(r.custo.get(chave, 0) for r in resultados)
        for chave in ("chamadas_ia", "prompt_tokens", "completion_tokens", "total_tokens")
    }
    modelos = sorted({
        m["model"]
        for r in resultados
        if r.dossie
        for m in r.dossie.get("modelos", [])
    })

    corpo = "\n".join(_secao_cenario(r) for r in resultados)

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<title>sofia-eval — relatório</title>
<style>
{_CSS}
</style>
</head>
<body>
<header>
  <h1>sofia-eval — relatório da rodada</h1>
  <dl class="cabecalho">
    <div><dt>sofia-eval</dt><dd><code>{escape(_sha(Path(__file__).resolve().parent.parent))}</code></dd></div>
    <div><dt>sofia-bot</dt><dd><code>{escape(_sha(cfg.sofia_bot_dir))}</code></dd></div>
    <div><dt>data/hora</dt><dd>{escape(datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S %z'))}</dd></div>
    <div><dt>modelo(s)</dt><dd>{escape(', '.join(modelos) or '—')}</dd></div>
    <div><dt>cenários</dt><dd class="{'ok' if passaram == total else 'falha'}">{passaram}/{total} passaram</dd></div>
    <div><dt>chamadas de IA</dt><dd>{relatorio._n(agregado['chamadas_ia'])}</dd></div>
    <div><dt>tokens de prompt</dt><dd>{relatorio._n(agregado['prompt_tokens'])}</dd></div>
  </dl>
</header>
<main>
{corpo}
</main>
</body>
</html>
"""


def _secao_cenario(r) -> str:
    classe = {"PASSOU": "ok", "FALHOU": "falha", "ERRO": "erro"}.get(r.veredito, "erro")
    descricao = ""
    checagens_html = ""
    transcript_html = ""
    if r.dossie:
        checagens_html = _tabela_checagens(r.dossie.get("checagens") or [])
        transcript_html = _transcript(r.dossie.get("transcricao") or [])

    motivos_html = ""
    if r.motivos:
        itens = "".join(f"<li>{escape(str(m))}</li>" for m in r.motivos)
        motivos_html = f'<div class="motivos"><h3>Motivos</h3><ul>{itens}</ul></div>'

    evidencia_html = (
        f'<p class="evidencia">Estado do banco capturado em: <code>{escape(r.evidencia)}</code></p>'
        if r.evidencia else ""
    )

    return f"""<section class="cenario {classe}">
  <h2><span class="veredito">{escape(r.veredito)}</span> {escape(r.id)}</h2>
  <p class="custo">{relatorio._n(r.custo.get('chamadas_ia', 0)) if r.custo else '–'} chamadas de IA ·
     {relatorio._n(r.custo.get('prompt_tokens', 0)) if r.custo else '–'} tokens de prompt ·
     {r.segundos:.1f}s</p>
  {motivos_html}
  {evidencia_html}
  {checagens_html}
  {transcript_html}
  <p class="nota-ferramentas">{escape(NOTA_FERRAMENTAS)}</p>
</section>"""


def _tabela_checagens(checagens: list) -> str:
    if not checagens:
        return ""
    linhas = "\n".join(
        f'<tr class="{"ok" if c["ok"] else "falha"}">'
        f'<td>{escape(str(c["chave"]))}</td>'
        f'<td>{escape(str(c["esperado"]))}</td>'
        f'<td>{escape(str(c["obtido"]))}</td>'
        f'<td>{"✓ PASSOU" if c["ok"] else "✗ FALHOU"}</td>'
        f"</tr>"
        for c in checagens
    )
    return f"""<table class="checagens">
    <caption>Verificações</caption>
    <thead><tr><th>chave</th><th>esperado</th><th>obtido</th><th>veredito</th></tr></thead>
    <tbody>{linhas}</tbody>
  </table>"""


def _transcript(mensagens: list) -> str:
    if not mensagens:
        return ""
    turnos = "\n".join(
        f'<div class="turno {escape(m["role"])}">'
        f'<span class="papel">{escape(m["role"])}</span>'
        f'<span class="texto">{escape(m["content"] or "")}</span>'
        f"</div>"
        for m in mensagens
    )
    return f'<div class="transcript"><h3>Conversa</h3>{turnos}</div>'


_CSS = """
:root { color-scheme: light dark; }
body { font-family: system-ui, sans-serif; margin: 0; padding: 1.5rem;
       background: #fafafa; color: #1a1a1a; line-height: 1.5; }
header { border-bottom: 2px solid #ccc; padding-bottom: 1rem; margin-bottom: 1.5rem; }
h1 { font-size: 1.3rem; margin: 0 0 0.75rem; }
dl.cabecalho { display: flex; flex-wrap: wrap; gap: 1.5rem; margin: 0; }
dl.cabecalho div { min-width: 8rem; }
dl.cabecalho dt { font-size: 0.75rem; text-transform: uppercase; color: #666; margin: 0; }
dl.cabecalho dd { margin: 0; font-weight: 600; }
dd.ok { color: #1a7a3a; }
dd.falha { color: #b3261e; }
section.cenario { border: 1px solid #ddd; border-radius: 6px; padding: 1rem 1.25rem;
                   margin-bottom: 1.25rem; background: #fff; }
section.cenario.falha { border-left: 4px solid #b3261e; }
section.cenario.erro { border-left: 4px solid #c78a00; }
section.cenario.ok, section.cenario.PASSOU { border-left: 4px solid #1a7a3a; }
h2 { font-size: 1.05rem; margin: 0 0 0.5rem; }
span.veredito { font-family: monospace; padding: 0.1rem 0.4rem; border-radius: 4px;
                background: #eee; margin-right: 0.5rem; }
.ok span.veredito { background: #d7f0dd; color: #1a7a3a; }
.falha span.veredito { background: #fbdcd9; color: #b3261e; }
.erro span.veredito { background: #fbe8bf; color: #8a5c00; }
p.custo { color: #555; font-size: 0.9rem; }
table.checagens { border-collapse: collapse; width: 100%; margin: 0.75rem 0; font-size: 0.9rem; }
table.checagens caption { text-align: left; font-weight: 600; margin-bottom: 0.35rem; }
table.checagens th, table.checagens td { border: 1px solid #ddd; padding: 0.35rem 0.5rem; text-align: left; }
table.checagens tr.falha td { background: #fdf1f0; }
table.checagens tr.ok td { background: #f4faf5; }
div.transcript { margin-top: 0.75rem; }
div.turno { border-radius: 8px; padding: 0.5rem 0.75rem; margin: 0.35rem 0; max-width: 80%; }
div.turno.user { background: #eef2fb; margin-right: auto; }
div.turno.assistant { background: #f1f1f1; margin-left: auto; }
span.papel { display: block; font-size: 0.7rem; text-transform: uppercase; color: #777; }
span.texto { white-space: pre-wrap; }
.motivos ul { margin: 0.25rem 0; }
p.evidencia, p.nota-ferramentas { color: #777; font-size: 0.8rem; }
@media (prefers-color-scheme: dark) {
  body { background: #16181c; color: #e6e6e6; }
  section.cenario { background: #1e2126; border-color: #333; }
  span.veredito { background: #2a2d33; }
  table.checagens th, table.checagens td { border-color: #3a3d43; }
  table.checagens tr.falha td { background: #3a2422; }
  table.checagens tr.ok td { background: #1f3226; }
  div.turno.user { background: #23324a; }
  div.turno.assistant { background: #2a2d33; }
}
"""
