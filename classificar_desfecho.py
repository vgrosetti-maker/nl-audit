"""Classifica o codigo de saida de audit_repos.py em tres desfechos distintos.

O auditor sozinho ja sabe distinguir (audit_repos.py: 0=limpo, 1=violacao real,
2=nao deu para observar). O que faltava era o WORKFLOW parar de tratar todo
codigo != 0 como "vermelho" sem dizer qual dos tres era. Um auditor que fica
vermelho por segredo ausente e um auditor que fica vermelho porque o alvo esta
quebrado sao coisas diferentes, e so a segunda deve reprovar o alvo.

Mapa (codigo de saida -> desfecho):
  0        -> limpo             (nenhuma violacao, nada a fazer)
  1        -> achado-real       (o ALVO auditado esta com problema; reprova)
  2        -> falha-de-credencial  (segredo ausente/expirado; infra do auditor)
  qualquer -> falha-do-auditor  (job quebrou: dependencia, sintaxe, timeout...)
outro valor

So achado-real deve reprovar o alvo. Os outros dois sao visiveis (warning +
resumo do job) mas nao mentem que o alvo esta quebrado quando quem quebrou foi
o proprio auditor.
"""
import sys

DESFECHOS = {
    0: "limpo",
    1: "achado-real",
    2: "falha-de-credencial",
}


def classificar(codigo):
    """codigo (int) -> nome do desfecho (str). Qualquer codigo fora do mapa
    (3, 127, 139 de segfault, etc.) cai em 'falha-do-auditor': o job quebrou
    de um jeito que audit_repos.py nem chegou a classificar sozinho."""
    return DESFECHOS.get(codigo, "falha-do-auditor")


def titulo(desfecho, codigo):
    return {
        "limpo": "Auditor limpo - nenhuma violacao (codigo 0)",
        "achado-real": "ACHADO-REAL - o alvo auditado esta com problema (codigo 1)",
        "falha-de-credencial": "FALHA-DE-CREDENCIAL - segredo ausente/expirado,"
                                " nao e achado (codigo 2)",
        "falha-do-auditor": "FALHA-DO-AUDITOR - o job quebrou (codigo %s),"
                             " nao e achado" % codigo,
    }[desfecho]


def main(argv):
    if not argv:
        print("uso: classificar_desfecho.py <codigo> [--resumo]", file=sys.stderr)
        return 2
    codigo = int(argv[0])
    desfecho = classificar(codigo)
    if "--resumo" in argv[1:]:
        print("## Desfecho: %s" % titulo(desfecho, codigo))
        print("")
        print("Codigo de saida do audit_repos.py: `%s`" % codigo)
    else:
        print("desfecho=%s" % desfecho)
        print("codigo=%s" % codigo)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
