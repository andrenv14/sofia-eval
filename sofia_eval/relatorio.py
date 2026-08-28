"""Saída: uma tabela no terminal e um código de saída. Nada mais.

Sem gráfico, sem HTML, sem arquivo — a spec é explícita. O que importa é 0 se
tudo passou, 1 se algo falhou, pra servir de portão antes de lançar cliente.
"""

import sys
from dataclasses import dataclass, field

PASSOU = "PASSOU"
FALHOU = "FALHOU"
ERRO = "ERRO"


def _cor(texto, codigo):
    if not sys.stdout.isatty():
        return texto
    return f"\033[{codigo}m{texto}\033[0m"


def verde(t):
    return _cor(t, "32")


def vermelho(t):
    return _cor(t, "31")


def amarelo(t):
    return _cor(t, "33")


def cinza(t):
    return _cor(t, "90")


@dataclass
class Resultado:
    id: str
    veredito: str
    motivos: list = field(default_factory=list)
    custo: dict = field(default_factory=dict)
    segundos: float = 0.0
    resposta: str = None
    # Caminho do JSON com o estado do banco, preenchido só quando o cenário
    # falha — ver evidencia.capturar().
    evidencia: str = None

    @property
    def ok(self):
        return self.veredito == PASSOU


def _n(valor):
    return f"{valor:,}".replace(",", ".")


def imprimir(resultados: list) -> int:
    largura = max([len(r.id) for r in resultados] + [8])
    print()
    print(
        f"{'CENÁRIO'.ljust(largura)}  {'VEREDITO':<9}  {'CHAMADAS':>8}  "
        f"{'TOKENS(P)':>10}  {'TEMPO':>7}"
    )
    print("─" * (largura + 42))
    for r in resultados:
        rotulo = {PASSOU: verde(PASSOU), FALHOU: vermelho(FALHOU), ERRO: amarelo(ERRO)}[r.veredito]
        # ljust antes de colorir: o código ANSI conta como caractere no ljust.
        enfeite = len(rotulo) - len(r.veredito)
        chamadas = _n(r.custo.get("chamadas_ia", 0)) if r.custo else "–"
        prompt = _n(r.custo.get("prompt_tokens", 0)) if r.custo else "–"
        print(
            f"{r.id.ljust(largura)}  {rotulo.ljust(9 + enfeite)}  {chamadas:>8}  "
            f"{prompt:>10}  {r.segundos:>6.1f}s"
        )

    falhos = [r for r in resultados if not r.ok]
    if falhos:
        print()
        for r in falhos:
            titulo = vermelho(r.veredito) if r.veredito == FALHOU else amarelo(r.veredito)
            print(f"{titulo}  {r.id}")
            for motivo in r.motivos:
                primeira, *resto = str(motivo).splitlines()
                print(f"  · {primeira}")
                for linha in resto:
                    print(f"    {linha}")
            if r.resposta:
                texto = r.resposta.replace("\n", " ")
                if len(texto) > 220:
                    texto = texto[:217] + "..."
                print(cinza(f'    última resposta da assistente: "{texto}"'))
            if r.evidencia:
                print(cinza(f"    estado do banco: {r.evidencia}"))
            print()

    passaram = sum(1 for r in resultados if r.ok)
    total = len(resultados)
    agregado = {
        "chamadas_ia": sum(r.custo.get("chamadas_ia", 0) for r in resultados),
        "prompt_tokens": sum(r.custo.get("prompt_tokens", 0) for r in resultados),
        "completion_tokens": sum(r.custo.get("completion_tokens", 0) for r in resultados),
        "total_tokens": sum(r.custo.get("total_tokens", 0) for r in resultados),
    }

    print("─" * (largura + 42))
    resumo = f"{total} cenário(s): {passaram} passaram, {total - passaram} falharam"
    print(verde(resumo) if passaram == total else vermelho(resumo))
    print(
        cinza(
            f"custo agregado: {_n(agregado['chamadas_ia'])} chamadas de IA · "
            f"{_n(agregado['prompt_tokens'])} tokens de prompt · "
            f"{_n(agregado['completion_tokens'])} de resposta · "
            f"{_n(agregado['total_tokens'])} no total"
        )
    )
    print()
    return 0 if passaram == total else 1
