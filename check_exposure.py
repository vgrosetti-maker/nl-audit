#!/usr/bin/env python3
"""
check_exposure.py — o que está VERSIONADO num repo e ACESSÍVEL no site publicado.

Nasceu em 27/07/2026, depois de um deploy de pasta inteira, sem lista branca,
publicar arquivos de desenvolvimento junto do site. A causa é sempre a mesma:
o que sobe é a pasta, e ninguém compara o que subiu com o que devia subir.

Uso:
    python check_exposure.py <url> <caminho-do-repo> [--all]

    --all  testa TODOS os arquivos versionados (lento), não só os suspeitos.

Sai com código 1 se achar exposição, para poder virar gate de CI.

Três armadilhas que este script evita de propósito, porque já produziram
diagnóstico errado antes:

1. STATUS NÃO PROVA NADA. Um site com catch-all responde 200 e devolve a home
   para qualquer caminho. Aqui todo 200 é comparado com o corpo da home e com o
   de um caminho garantidamente inexistente; se for igual, não é exposição.
2. DIRETÓRIO SEM INDEX dá 404 mesmo com os arquivos dentro acessíveis. Por isso
   se testa ARQUIVO, nunca pasta.
3. ACESSÍVEL NÃO É EXPOSTO. `index.html`, CSS, imagem e `sitemap.xml` respondem
   200 porque é o trabalho deles. Até 10/08/2026 o modo `--all` chamava isso de
   exposição e saía 1: rodado em site-principal e site-vitrine, acusou 41 e
   70 arquivos, todos públicos por design — um gate assim reprova todo deploy
   saudável e ensina a ignorar o vermelho. Agora só conta como exposição o que
   passa em `suspeito()`; o resto sai como contagem informativa.
"""
import subprocess, sys, urllib.request, urllib.error, hashlib, os, random

SUSPEITOS_EXT = {".sql", ".md", ".csv", ".xlsx", ".xls", ".env", ".sh",
                 ".log", ".bak", ".yml", ".yaml", ".toml", ".lock"}
SUSPEITOS_DIR = {"docs", "tests", "test", "tools", "supabase", "scripts",
                 "sales", "marketing", "crm", "catalog", "_design",
                 "_orquestra", ".impeccable", ".github", "migrations"}
SUSPEITOS_NOME = {"package.json", "package-lock.json", "CLAUDE.md", "AGENTS.md",
                  "README.md", "netlify.toml", ".gitignore", ".gitattributes"}

# Terceira categoria, nascida de um vazamento real: material sobre um NEGÓCIO
# DE TERCEIRO que nunca pediu nada (prévia de proposta, amostra feita para um
# cliente em potencial). Não é interno nosso e não é o site fazendo o trabalho
# dele, então escapava dos dois baldes: uma página assim ficou 44 dias em HTTP
# 200 e as três verificações automáticas que existiam passaram verdes, porque
# todas procuravam material NOSSO. Publicar isso é tratamento de dado de
# terceiro sem base declarada, não é só desleixo de deploy.
TERCEIROS_DIR = {"preview", "previews", "prospect", "prospects", "clientes",
                 "leads", "amostras"}

UA = {"User-Agent": "Mozilla/5.0 (check-exposure)"}


def buscar(url, timeout=15):
    try:
        r = urllib.request.Request(url, headers=UA)
        x = urllib.request.urlopen(r, timeout=timeout)
        return x.getcode(), x.read()
    except urllib.error.HTTPError as e:
        return e.code, b""
    except Exception:
        return 0, b""


def versionados(repo):
    out = subprocess.run(["git", "-C", repo, "ls-files"],
                         capture_output=True, text=True)
    return [l.strip() for l in out.stdout.splitlines() if l.strip()]


def suspeito(caminho):
    partes = caminho.split("/")
    if partes[0] in SUSPEITOS_DIR:
        return True
    nome = os.path.basename(caminho)
    if nome in SUSPEITOS_NOME:
        return True
    # `.env`, `.env.local`, `.env.production`: nome que começa com ponto-env não
    # tem extensão útil pro splitext (`.env` -> ext vazia, `.env.local` -> `.local`),
    # e por isso escapava do filtro até 10/08/2026 — bem o arquivo que mais importa.
    if nome.lower().startswith(".env"):
        return True
    return os.path.splitext(caminho)[1].lower() in SUSPEITOS_EXT


def terceiro(caminho):
    """Material sobre negócio de terceiro (prospecção). Pasta de topo, não substring."""
    return caminho.split("/")[0].lower() in TERCEIROS_DIR


def acessivel(cod, corpo, baselines):
    """200 com corpo próprio (nem home, nem página de erro do catch-all)."""
    return cod == 200 and bool(corpo) and hashlib.md5(corpo).hexdigest() not in baselines


def classificar(acessiveis):
    """[(caminho, bytes)] acessíveis -> (expostos, terceiros, publicos). Pura.

    Três baldes, não dois. Exposto = interno nosso (`suspeito`). Terceiro =
    material de negócio alheio (`terceiro`), que também derruba o gate mas por
    outro motivo e com outro conserto. Público = o site fazendo o trabalho
    dele, não achado."""
    expostos = [(f, n) for f, n in acessiveis if suspeito(f)]
    terceiros = [(f, n) for f, n in acessiveis if not suspeito(f) and terceiro(f)]
    publicos = [(f, n) for f, n in acessiveis
                if not suspeito(f) and not terceiro(f)]
    return expostos, terceiros, publicos


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    todos = "--all" in sys.argv
    if len(args) < 2:
        print(__doc__)
        sys.exit(2)
    base, repo = args[0].rstrip("/"), args[1]

    cod_home, home = buscar(base + "/")
    if cod_home != 200:
        print("AVISO: home respondeu %s" % cod_home)
    _, lixo = buscar("%s/nao-existe-%d" % (base, random.randint(10**6, 10**9)))
    baselines = {hashlib.md5(b).hexdigest() for b in (home, lixo) if b}

    arquivos = versionados(repo)
    # No modo padrão, testa interno NOSSO e material de TERCEIRO. Deixar
    # `terceiro` de fora daqui deixaria o balde novo inalcançável sem `--all`.
    alvo = arquivos if todos else [f for f in arquivos
                                   if suspeito(f) or terceiro(f)]
    print("repo: %s" % repo)
    print("site: %s" % base)
    print("versionados: %d | testando: %d%s\n" %
          (len(arquivos), len(alvo), "" if todos else " (suspeitos; use --all para tudo)"))

    acessiveis = []
    for f in alvo:
        cod, corpo = buscar("%s/%s" % (base, f))
        if acessivel(cod, corpo, baselines):
            acessiveis.append((f, len(corpo)))

    expostos, terceiros, publicos = classificar(acessiveis)
    for f, n in expostos:
        print("  EXPOSTO   %-55s %d bytes" % (f, n))
    for f, n in terceiros:
        print("  TERCEIRO  %-55s %d bytes" % (f, n))

    print()
    if publicos:
        print("acessíveis e esperados (público por design): %d" % len(publicos))
    if expostos:
        print("RESULTADO: %d arquivo(s) interno(s) acessíveis publicamente."
              % len(expostos))
        print("Corrija a lista branca em scripts/build-site.sh e republique.")
    if terceiros:
        print("RESULTADO: %d arquivo(s) de TERCEIRO acessíveis publicamente."
              % len(terceiros))
        print("Material de negócio que não nos pediu nada. Despublique e "
              "confira se o asset remoto (imagem em CDN) também saiu do ar: "
              "404 no HTML não apaga a imagem hospedada fora.")
    if expostos or terceiros:
        sys.exit(1)
    print("RESULTADO: nada interno nem de terceiro acessível entre os %d testados."
          % len(alvo))


if __name__ == "__main__":
    main()
