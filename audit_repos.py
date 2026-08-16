#!/usr/bin/env python3
"""audit_repos.py — invariantes da conta GitHub, repo a repo.

Irmão do check_exposure.py: mesmo contrato de saída (lista legível, RESULTADO
final, exit 1 quando há violação), mesma doutrina — o auditor não adivinha nada,
compara o estado observado com um estado desejado declarado em dado.

Invariantes de hoje, por repo do recorte:
  1. alertas    — alertas de dependência ligados
  2. branch     — o default branch é a branch de produção declarada
  3. gitignore  — repo com função serverless ignora arquivo de segredo
  4. ar         — o endereço canônico do HTML responde 200 no host real
  5. ci         — repo com comando de build/teste tem workflow que EXECUTOU,
                  não apenas arquivo de workflow existente

A conta, o recorte e as exceções são DADO, não código: vivem em
`declaracoes.json` (fora do git). Copie `declaracoes.example.json` e edite.
"""
import base64
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from collections import namedtuple
from pathlib import Path

CONFIG = Path(__file__).with_name("declaracoes.json")
EXEMPLO = Path(__file__).with_name("declaracoes.example.json")

Declaracao = namedtuple("Declaracao", "branch_producao serverless site tem_comando",
                        defaults=(True,))
Observado = namedtuple("Observado",
                       "alertas branch_default gitignore canonical status_ar ci_executado",
                       defaults=(None,))

# O recorte e uma DECISAO, nao a conta inteira. Repo da conta que nao esta declarado
# aparece no relatorio marcado "fora do recorte" e nao condena ninguem: o auditor
# audita o que o dono decidiu auditar, e diz em voz alta o que ficou de fora.
#
# `branch_producao` e DADO DECLARADO, nao `main` presumido: repo antigo usa `master`
# e o auditor que presume `main` acusa a conta inteira de errado.
# `serverless` diz se o repo tem funcao rodando com credencial (ex.: netlify/functions),
# que e o que torna a cobertura de segredo no .gitignore obrigatoria.
# `site` diz se o repo deve ter endereco vivo no ar. E o campo que separa "existe no
# git" de "esta publicado": so em repo de site o endereco canonico do HTML e cobrado
# contra a resposta real do host. App que roda so local, ou backend sem front na raiz,
# declara site=False em vez de virar violacao permanente.
#
# Excecao mora em DADO, com motivo escrito - nunca como condicional escondida no
# codigo. A chave e (repo, invariante): excecao dispensa UM estado aceito, nao abre o
# repo inteiro. Toda excecao aplicada sai impressa no relatorio, e uma excecao que nao
# casa com nenhum repo do recorte derruba a rodada em vez de morrer em silencio.
def carregar(caminho=CONFIG):
    """Le conta, recorte e excecoes do arquivo de declaracao.

    Sem arquivo devolve vazio em vez de explodir: o import tem que continuar
    barato para os testes. Quem cobra a presenca do dado e o main().
    """
    if not caminho.exists():
        return "", {}, {}
    bruto = json.loads(caminho.read_text(encoding="utf-8"))
    declarado = {
        nome: Declaracao(d["branch_producao"], d["serverless"], d["site"],
                         d.get("tem_comando", True))
        for nome, d in bruto.get("declarado", {}).items()
    }
    excecoes = {(e["repo"], e["invariante"]): e["motivo"]
                for e in bruto.get("excecoes", [])}
    return bruto.get("owner", ""), declarado, excecoes


OWNER, DECLARADO, EXCECOES = carregar()

RECORTE = list(DECLARADO)

# Cada tupla e um requisito; os itens dela sao grafias aceitas do mesmo requisito.
# `.env` sozinho nao cobre `.env.local` nem `.env.production`, que sao justamente os
# arquivos que o Netlify CLI e o Vite escrevem na maquina.
PADROES_SEGREDO = ((".env", "*.env", "**/.env"), (".env.*", "*.env.*", "**/.env.*"))

Violacao = namedtuple("Violacao", "repo invariante motivo")
Dispensa = namedtuple("Dispensa", "repo invariante motivo")
Linha = namedtuple("Linha", "repo no_recorte alertas branch gitignore canonical ci")
Relatorio = namedtuple("Relatorio", "violacoes dispensados linhas")

DESCONHECIDO = Observado(alertas=None, branch_default=None, gitignore=None,
                         canonical=None, status_ar=None, ci_executado=None)


def falta_cobertura(texto):
    """Devolve os padroes de segredo que o .gitignore NAO cobre (lista vazia = ok)."""
    linhas = {l.strip() for l in texto.splitlines()
              if l.strip() and not l.strip().startswith("#")}
    return [alts[0] for alts in PADROES_SEGREDO if not linhas.intersection(alts)]


def avaliar(repos_da_conta, observado, recorte, declarado, excecoes):
    """Estado observado + estado declarado -> veredito. Funcao pura, sem I/O."""
    violacoes, dispensados = [], []

    def julga(repo, invariante, ok, motivo, observou=True):
        if ok:
            return
        if not observou:  # "nao sei" nunca conta como "esta ok", e excecao nao cobre
            violacoes.append(Violacao(repo, invariante, motivo))
            return
        if (repo, invariante) in excecoes:
            dispensados.append(Dispensa(repo, invariante, excecoes[(repo, invariante)]))
        else:
            violacoes.append(Violacao(repo, invariante, motivo))

    for nome in recorte:
        if nome not in repos_da_conta:
            violacoes.append(Violacao(nome, "conta", "no recorte, mas nao existe na conta"))
            continue
        est = observado.get(nome, DESCONHECIDO)

        julga(nome, "alertas", est.alertas is True,
              "estado nao observado (o gh nao respondeu)" if est.alertas is None
              else "alertas de dependencia desligados",
              observou=est.alertas is not None)

        if nome not in declarado:
            # Auditar branch sem saber qual e a de producao seria adivinhacao.
            violacoes.append(Violacao(nome, "declaracao",
                                      "no recorte sem declaracao de branch de producao"))
            continue
        dec = declarado[nome]

        julga(nome, "branch", est.branch_default == dec.branch_producao,
              "default branch nao observado (declarada: %s)" % dec.branch_producao
              if est.branch_default is None else
              "default branch e `%s`, a de producao declarada e `%s`"
              % (est.branch_default, dec.branch_producao),
              observou=est.branch_default is not None)

        if dec.serverless:
            faltando = falta_cobertura(est.gitignore or "")
            julga(nome, "gitignore", est.gitignore is not None and not faltando,
                  ".gitignore nao observado" if est.gitignore is None else
                  ".gitignore nao cobre segredo: falta %s" % ", ".join(faltando),
                  observou=est.gitignore is not None)

        # "existe no git" nao e "esta publicado": o endereco sai do HTML do proprio
        # repo (observado, nao presumido) e e conferido contra a resposta do host.
        if dec.site:
            if est.canonical is None:
                julga(nome, "ar", False,
                      "endereco canonico nao observado (o gh nao devolveu o index.html)",
                      observou=False)
            elif not est.canonical:
                julga(nome, "ar", False,
                      "repo de site sem endereco canonico declarado no HTML")
            elif est.status_ar is None:
                julga(nome, "ar", False,
                      "canonical %s: host nao respondeu (DNS, timeout ou recusa)"
                      % est.canonical, observou=False)
            else:
                julga(nome, "ar", est.status_ar == "200",
                      "canonical %s responde %s, nao 200"
                      % (est.canonical, est.status_ar))

        # "arquivo de workflow existe" nao e "CI rodou": a invariante pergunta a API
        # do GitHub se ha execucao registrada, nunca o disco. Repo sem comando de
        # build/teste nao entra em violacao muda - ou tem excecao com motivo escrito,
        # ou aparece nomeado (typo em EXCECOES nao dispensa nada em silencio).
        if not dec.tem_comando:
            julga(nome, "ci", False,
                  "sem comando de build/teste hoje - nada para o CI executar")
        else:
            julga(nome, "ci", est.ci_executado is True,
                  "execucao do workflow nao observada (gh nao respondeu)"
                  if est.ci_executado is None else
                  "workflow tem arquivo mas nenhuma execucao registrada",
                  observou=est.ci_executado is not None)

    linhas = []
    for nome in sorted(repos_da_conta):
        no_recorte = nome in recorte
        est = observado.get(nome, DESCONHECIDO)
        dec = declarado.get(nome)
        if not no_recorte:
            linhas.append(Linha(nome, False, "nao observado", "-", "-", "-", "-"))
            continue
        alertas = {True: "alertas ON", False: "alertas OFF"}.get(est.alertas, "nao observado")
        if dec is None:
            branch = "%s (sem declaracao)" % (est.branch_default or "?")
        elif est.branch_default is None:
            branch = "nao observado (quer %s)" % dec.branch_producao
        elif est.branch_default == dec.branch_producao:
            branch = est.branch_default
        else:
            branch = "%s != %s" % (est.branch_default, dec.branch_producao)
        if dec is None:
            gitignore = "?"
        elif not dec.serverless:
            gitignore = "n/a (sem serverless)"
        elif est.gitignore is None:
            gitignore = "nao observado"
        else:
            faltando = falta_cobertura(est.gitignore)
            gitignore = "ok" if not faltando else "falta %s" % ", ".join(faltando)
        if dec is None:
            canonical = "?"
        elif not dec.site:
            canonical = "n/a (nao e site)"
        elif est.canonical is None:
            canonical = "nao observado"
        elif not est.canonical:
            canonical = "sem canonical no HTML"
        else:
            host = est.canonical.split("//")[-1].strip("/")
            canonical = "%s %s" % (est.status_ar or "sem resposta", host)
        if dec is None:
            ci = "?"
        elif not dec.tem_comando:
            ci = "n/a (sem comando)"
        elif est.ci_executado is None:
            ci = "nao observado"
        else:
            ci = "executou" if est.ci_executado else "nunca executou"
        linhas.append(Linha(nome, True, alertas, branch, gitignore, canonical, ci))

    return Relatorio(violacoes=violacoes, dispensados=dispensados, linhas=linhas)


def render(rel):
    saida = ["repos da conta: %d | no recorte: %d\n"
             % (len(rel.linhas), sum(1 for l in rel.linhas if l.no_recorte)),
             "%-18s %-24s %-14s %-30s %-22s %-28s %s"
             % ("", "repo", "alertas", "branch (default/producao)", "gitignore",
                "canonical (status/host)", "ci")]
    for l in rel.linhas:
        marca = "  [recorte]" if l.no_recorte else "  fora do recorte"
        saida.append("%-18s %-24s %-14s %-30s %-22s %-28s %s"
                     % (marca, l.repo, l.alertas, l.branch, l.gitignore, l.canonical, l.ci))

    if rel.dispensados:
        saida.append("\nExcecoes declaradas (nao viram violacao, mas nao somem):")
        for d in rel.dispensados:
            saida.append("  DISPENSADO  %-24s %-12s %s" % (d.repo, d.invariante, d.motivo))

    saida.append("")
    if rel.violacoes:
        for v in rel.violacoes:
            saida.append("  VIOLACAO  %-24s %-12s %s" % (v.repo, v.invariante, v.motivo))
        saida.append("\nRESULTADO: %d violacao(oes) em %d repo(s) do recorte."
                     % (len(rel.violacoes), len({v.repo for v in rel.violacoes})))
    else:
        saida.append("RESULTADO: os %d repos do recorte estao no estado desejado."
                     % sum(1 for l in rel.linhas if l.no_recorte))
    return "\n".join(saida)


def codigo_saida(rel):
    return 1 if rel.violacoes else 0


# --- adapter: fala com o gh. Nao decide nada aqui dentro, so coleta. -----------

class ColetaFalhou(Exception):
    """Nao deu para observar. Quem decide o que fazer com isso e o main(), nao o adapter."""


def _gh(args):
    try:
        return subprocess.run(["gh"] + args, capture_output=True, text=True, timeout=60)
    except FileNotFoundError:
        raise ColetaFalhou("o comando `gh` nao esta no PATH")
    except subprocess.TimeoutExpired:
        raise ColetaFalhou("o `gh` travou (60s) em: gh %s" % " ".join(args))


def repos_da_conta(owner=OWNER):
    """{nome: default branch} da conta inteira - uma chamada, nao uma por repo."""
    r = _gh(["repo", "list", owner, "--limit", "1000", "--json", "name,defaultBranchRef"])
    if r.returncode != 0:
        raise ColetaFalhou("gh nao respondeu (autenticado?): %s" % r.stderr.strip())
    return {x["name"]: (x.get("defaultBranchRef") or {}).get("name")
            for x in json.loads(r.stdout)}


STATUS = re.compile(r"^HTTP/[\d.]+\s+(\d{3})")


def _status(saida):
    """Le o status line de verdade, nao procura o numero solto na mensagem - assim
    mudanca de texto do gh vira "nao observado" (que grita) e nao um falso verde."""
    for linha in (saida or "").splitlines():
        m = STATUS.match(linha.strip())
        if m:
            return m.group(1)
    return None


def alertas_ligados(repo, owner=OWNER):
    """204 = ligado, 404 = desligado. Qualquer outra coisa e None: nao observado."""
    r = _gh(["api", "-i", "repos/%s/%s/vulnerability-alerts" % (owner, repo)])
    return {"204": True, "404": False}.get(_status(r.stdout))


def _conteudo(caminho, repo, owner=OWNER):
    """Texto de um arquivo do default branch. 404 = "" (observado: nao existe).

    Qualquer outro status vira None: falha de coleta grita, nao vira verde.
    """
    r = _gh(["api", "-i", "repos/%s/%s/contents/%s" % (owner, repo, caminho)])
    status = _status(r.stdout)
    if status == "404":
        return ""
    if status != "200":
        return None
    corpo = (r.stdout or "").split("\r\n\r\n", 1)[-1].split("\n\n", 1)[-1]
    try:
        return base64.b64decode(json.loads(corpo)["content"]).decode("utf-8", "replace")
    except (ValueError, KeyError):
        return None


def gitignore(repo, owner=OWNER):
    """Texto do .gitignore do default branch. 404 = "" (observado: nao existe).

    Erro de outra natureza vira None, que o avaliar() trata como violacao — nunca
    como cobertura em dia.
    """
    return _conteudo(".gitignore", repo, owner)


CANONICAL = re.compile(r"""<link\b[^>]*\brel=["']?canonical["']?[^>]*>""", re.I)
HREF = re.compile(r"""\bhref=["']([^"']+)["']""", re.I)


def canonical_declarado(repo, owner=OWNER):
    """Endereco que o proprio HTML do repo diz ser o dele. "" = nenhum declarado."""
    html = _conteudo("index.html", repo, owner)
    if html is None:
        return None
    tag = CANONICAL.search(html)
    if not tag:
        return ""
    href = HREF.search(tag.group(0))
    return href.group(1).strip() if href else ""


def status_no_ar(url, timeout=15):
    """Resposta real do host. None = nao respondeu (DNS, timeout, recusa) - e isso
    e violacao, nao verde: "nao sei" nunca conta como "esta publicado"."""
    if not url:
        return None
    req = urllib.request.Request(url, headers={"User-Agent": "nl-audit-probe"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return str(r.status)
    except urllib.error.HTTPError as e:   # 401/404/5xx sao resposta, nao falha de coleta
        return str(e.code)
    except (urllib.error.URLError, OSError, ValueError):
        return None


def workflow_executou(repo, owner=OWNER):
    """True se a Actions API registra pelo menos uma execucao do workflow do repo.

    Pergunta a API, nao o disco: arquivo de workflow existente e commitado nao
    prova nada por si so, so prova execucao a corrida que de fato aconteceu.
    Qualquer erro de coleta (gh fora do ar, JSON inesperado) vira None - "nao
    observado", nunca "nao executou" nem "executou".
    """
    # "-f" faz o `gh api` tentar POST por padrao; per_page vai na URL, nao em -f,
    # pra manter o GET (o bug real: 404 de metodo errado virava "gh nao respondeu").
    r = _gh(["api", "repos/%s/%s/actions/runs?per_page=1" % (owner, repo)])
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout).get("total_count", 0) > 0
    except (ValueError, AttributeError):
        return None


def observar(recorte, conta, declarado=DECLARADO):
    obs = {}
    for nome in recorte:
        if nome not in conta:
            continue
        dec = declarado.get(nome)
        url = canonical_declarado(nome) if (dec and dec.site) else None
        obs[nome] = Observado(alertas=alertas_ligados(nome),
                              branch_default=conta[nome],
                              gitignore=gitignore(nome),
                              canonical=url,
                              status_ar=status_no_ar(url) if url else None,
                              ci_executado=workflow_executou(nome)
                                           if (dec and dec.tem_comando) else None)
    return obs


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv

    # Sem declaracao nao ha auditoria: um auditor que roda com recorte vazio sai
    # verde sem ter olhado nada, que e o pior resultado possivel.
    if not OWNER or not DECLARADO:
        print("ERRO: falta %s (conta e recorte). Copie %s e edite."
              % (CONFIG.name, EXEMPLO.name))
        return 2

    recorte = RECORTE
    if "--recorte" in argv:  # audita um subconjunto declarado na linha de comando
        alvo = argv.index("--recorte") + 1
        if alvo >= len(argv):
            print("ERRO: --recorte exige a lista de repos (ex.: --recorte a,b)")
            return 2
        recorte = [x.strip() for x in argv[alvo].split(",") if x.strip()]

    # Typo em excecao nunca dispensaria nada, e em silencio. Confere contra o RECORTE
    # declarado no codigo, nao contra o subconjunto da chamada: `--recorte` filtra a
    # execucao, nao redefine o que e orfa.
    orfas = sorted({r for r, _ in EXCECOES} - set(RECORTE))
    if orfas:
        print("ERRO: excecao declarada para repo fora do recorte: %s" % ", ".join(orfas))
        return 2

    try:
        conta = repos_da_conta()
        observado = observar(recorte, conta)
    except ColetaFalhou as e:
        print("ERRO: %s. Nada foi auditado." % e)
        return 2

    rel = avaliar(conta, observado, recorte, DECLARADO, EXCECOES)
    print(render(rel))
    return codigo_saida(rel)


if __name__ == "__main__":
    sys.exit(main())
