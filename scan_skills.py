# -*- coding: utf-8 -*-
"""Higiene estática das skills instaladas (front-matter, referências, portabilidade).

Este arquivo substitui o rascunho efêmero que vivia em /tmp/scan_skills.py e que
deu DOIS falsos positivos medidos em 15/08/2026:

1. `superpowers:writing-plans` foi acusado de "referência inexistente:
   exact/path/to/test.py". Era texto ILUSTRATIVO dentro de crase, ensinando o
   formato de um plano. Caminho de exemplo não é dependência.
2. `claude-seo:seo-backlinks` foi acusado de "referência inexistente:
   references/backlink-quality.md". O arquivo EXISTE, em
   `skills/seo/references/backlink-quality.md` — é referência compartilhada entre
   as skills do plugin, declarada assim na própria SKILL.md. O defeito real é
   outro e menor: a linha 121 escreve o caminho relativo ao dir da skill.

Daí as duas regras que este scan carrega: exemplo ilustrativo não é achado, e
"não existe ao lado da skill" não é a mesma coisa que "não existe". Um scan que
não separa os dois transforma doc alheia em fila de trabalho falsa.

Uso:
    python scan_skills.py                 # relatório humano
    python scan_skills.py --json          # saída para outra ferramenta
    python scan_skills.py --raiz <dir>    # limita a uma árvore (usado nos testes)
"""
import argparse
import json
import os
import re
import sys

# O home se resolve em RUNTIME, nunca na importacao: os testes trocam o home
# para uma pasta temporaria e o scan tem que enxergar a troca. E se monta com
# os.path.join porque `r"~\.claude"` no Linux nao e caminho, e uma string com
# contrabarra literal - foi assim que a suite passou verde aqui e vermelha no CI.
def claude_home():
    return os.path.join(os.path.expanduser("~"), ".claude")


def claude_cache():
    return os.path.join(claude_home(), "plugins", "cache")


def raizes_padrao():
    return (os.path.join(claude_home(), "skills"), claude_cache())

# Severidade: 'quebra' = a skill referencia algo que não existe em lugar nenhum.
# 'aviso' = existe, mas o caminho escrito não resolve a partir da skill.
QUEBRA, AVISO = "quebra", "aviso"

# Marcadores de caminho didático. Um ref com qualquer um destes é exemplo, não
# dependência — foi o falso positivo nº 1.
MARCAS_ILUSTRATIVAS = (
    "path/to/", "path\\to\\", "your_", "your-", "<", "${", "$(", "{{",
    "caminho/para/", "example.com", "...",
)
NOMES_ILUSTRATIVOS = {"file.py", "test.py", "foo.py", "bar.py", "example.md",
                      "arquivo.md", "algo.md", "nome.md"}

# Referência dentro de crase simples só conta se apontar para uma pasta de
# recurso de verdade. Fora dessas pastas, é prosa.
PASTAS_DE_RECURSO = ("references/", "scripts/", "assets/", "templates/",
                     "examples/", "data/", "schema/", "hooks/")

# Artefato do SITE que a skill audita, não recurso que a skill carrega.
# `web-quality-skills` cita robots.txt/llms.txt como coisa a conferir no alvo;
# cobrar esses arquivos dentro da pasta da skill é falso positivo.
ARTEFATOS_DO_ALVO = {"robots.txt", "llms.txt", "sitemap.xml", "humans.txt",
                     "ads.txt", "security.txt", "manifest.json", "favicon.ico",
                     "netlify.toml", "package.json", "sw.js"}

# Caminho que a skill GRAVA (relatório, plano, handoff) ou que vive no repo
# ALVO, não recurso empacotado junto da skill. Terceiro falso positivo medido em
# 15/08/2026: `_qa/mobile/...`, `blog/registry.json`, `graphify-out/graph.json`,
# `netlify/functions/prospect-search.js` foram acusados de "ref-ausente" quando
# nenhum deles pode existir antes de a skill rodar.
PASTAS_DE_SAIDA = (
    "_qa/", "_orquestra/", "blog/", "backlog/", "graphify-out/", "plans/",
    ".scratch/", ".claude/", ".superpowers/", "docs/agents/", "netlify/",
)

# Fonte de verdade de qual versão de cada plugin o CLI carrega hoje.
def caminho_installed_json():
    return os.path.join(claude_home(), "plugins", "installed_plugins.json")

RE_REF = re.compile(
    r"`([^`\n]{2,160}?)`|\]\(([^)\s]{2,160}?)\)"
)
# Caminho absoluto de OUTRA maquina e dependencia que nunca vai existir aqui.
# O usuario desta maquina nao pode ser hardcoded: quem roda o scan e quem define
# qual home e "a propria". Override por env para testar sem depender do ambiente.
USUARIO_LOCAL = (os.environ.get("NL_AUDIT_USER")
                 or os.path.basename(os.path.expanduser("~")) or "\x00")
def montar_regex_estranho(usuario):
    eu = re.escape(usuario)
    return re.compile(
        r"(?:/home/(?!" + eu + r"\b)[A-Za-z0-9._-]+"
        r"|/Users/(?!" + eu + r"\b)[A-Za-z0-9._-]+"
        r"|[A-Z]:\\Users\\(?!" + eu + r"\b)[A-Za-z0-9._-]+)"
    )


RE_ABS_ESTRANHO = montar_regex_estranho(USUARIO_LOCAL)


def eh_ilustrativa(ref):
    """Caminho de exemplo em documentação, não dependência real."""
    baixo = ref.lower()
    if any(m in baixo for m in MARCAS_ILUSTRATIVAS):
        return True
    if os.path.basename(baixo) in NOMES_ILUSTRATIVOS:
        return True
    return False


def eh_saida(ref):
    """Artefato produzido pela skill ou arquivo do repo alvo — não dependência.

    Só conta prefixo: `plans/README.md` é saída, mas `skills/x/plans.md` não.
    """
    normal = ref.replace("\\", "/")
    if normal.startswith("./"):   # lstrip("./") comeria o ponto de `.claude/`
        normal = normal[2:]
    return normal.startswith(PASTAS_DE_SAIDA)


def sem_ancora(ref):
    """`references/A11Y-PATTERNS.md#skip-link` aponta para um arquivo que
    EXISTE; a âncora é seção dentro dele. Some antes de qualquer exists()."""
    if not ref or ref.startswith("#"):
        return ref
    return re.split(r"[#?]", ref, 1)[0].rstrip()


def parece_caminho(ref):
    """Distingue `references/x.md` de prosa em crase como `--json` ou `npm run`."""
    if not ref or ref.startswith(("-", "#", "@")) or " " in ref.strip():
        return False
    if "\n" in ref or "://" in ref:
        return False
    normal = ref.replace("\\", "/")
    # pasta de recurso ganha: `assets/robots.txt` é arquivo da skill mesmo
    if normal.startswith(PASTAS_DE_RECURSO):
        return True
    if os.path.basename(normal) in ARTEFATOS_DO_ALVO:
        return False
    # `/llms.txt` é caminho de URL na raiz do site auditado, não recurso local
    if normal.startswith("/"):
        return False
    # nome solto (`robots.txt`, `README.md`) é prosa: recurso de skill mora
    # numa pasta, então exigir pelo menos um separador
    if "/" not in normal:
        return False
    # caminho relativo com extensão de arquivo de recurso
    return bool(re.match(r"^[\w./-]+\.(md|py|json|sh|ps1|txt|ya?ml|js|mjs|css)$", normal))


def extrair_refs(texto):
    """Refs candidatas de uma SKILL.md, sem duplicata e em ordem estável."""
    vistos, saida = set(), []
    for ref, _linha in refs_com_contexto(texto):
        if ref in vistos:
            continue
        vistos.add(ref)
        saida.append(ref)
    return saida


def refs_com_contexto(texto):
    """(ref, trecho da linha ANTES da ref) para cada ocorrência, com repetição.

    O contexto é o que separa `evals/evals.json` em "Save test cases to ..."
    (arquivo que a skill GRAVA) de um recurso que ela carrega. Sem a frase, o
    scan só vê um caminho que não existe no disco e chuta quebra.
    """
    saida = []
    for m in RE_REF.finditer(texto):
        ref = sem_ancora((m.group(1) or m.group(2) or "").strip())
        if not parece_caminho(ref):
            continue
        ini_linha = texto.rfind("\n", 0, m.start()) + 1
        saida.append((ref, texto[ini_linha:m.start()]))
    return saida


# Verbo que aparece ANTES da ref e diz o que a skill faz com o arquivo. O
# último verbo do trecho manda: em "Read `a.md` and save to `b.json`", `a.md` é
# dependência e `b.json` é saída.
VERBOS_ESCRITA = (
    "save", "saves", "write", "writes", "writing", "create", "creates",
    "creating", "generate", "generates", "output", "outputs", "update",
    "updates", "store", "stores", "append", "appends", "draft", "drafts",
    "export", "exports", "record", "records", "grava", "grave", "gravar",
    "escreve", "escreva", "escrever", "salva", "salve", "salvar", "cria",
    "crie", "criar", "gera", "gere", "gerar", "atualiza", "atualize",
    "atualizar",
)
VERBOS_LEITURA = (
    "read", "reads", "load", "loads", "open", "opens", "see", "consult",
    "consults", "check", "checks", "reference", "references", "refer",
    "follow", "follows", "import", "imports", "review", "reviews", "leia",
    "ler", "abra", "abrir", "consulte", "consultar", "veja", "ver", "siga",
    "seguir", "carrega", "carregue", "carregar",
)
RE_VERBO = re.compile(
    r"\b(" + "|".join(VERBOS_ESCRITA + VERBOS_LEITURA) + r")\b", re.I)

# "e.g." / "por exemplo" antes da ref: quem apresenta um caminho COMO exemplo
# está ensinando um formato, não declarando dependência. Foi o falso positivo
# de `feature/bento-grid--material.md` no design-taste-frontend, exemplo do
# molde `blocks/<category>/<name>--<system>.md` escrito na mesma linha.
RE_EXEMPLO = re.compile(
    r"(e\.g\.|i\.e\.|for example|such as|ex\.:|ex:|por exemplo|exemplo:)[^`(\[]*$",
    re.I)


def eh_escrita(trecho):
    """A ref é destino de escrita da skill (último verbo do trecho é de escrita)."""
    achados = RE_VERBO.findall(trecho)
    return bool(achados) and achados[-1].lower() in VERBOS_ESCRITA


def eh_exemplo_em_prosa(trecho):
    """A ref foi introduzida como exemplo ('e.g. `x`'), não como dependência."""
    return bool(RE_EXEMPLO.search(trecho))


def achar_raiz_plugin(skill_dir):
    """Sobe até a raiz do PLUGIN — onde vivem os recursos compartilhados.

    A raiz é marcada por `.claude-plugin/` ou `plugin.json`. Sem esse marco,
    cai no pai do `skills/` mais alto. Parar no primeiro `skills/` erra em
    plugin com extensões (`<raiz>/extensions/x/skills/<skill>`): os refs da
    skill sao relativos a `<raiz>`, nao a `<raiz>/extensions/x`.
    """
    d = os.path.abspath(skill_dir)
    candidato = None
    for _ in range(8):
        pai = os.path.dirname(d)
        if os.path.isdir(os.path.join(d, ".claude-plugin")) or \
                os.path.isfile(os.path.join(d, "plugin.json")):
            return d
        if os.path.basename(d).lower() == "skills":
            candidato = pai
        if pai == d:
            break
        d = pai
    return candidato or os.path.dirname(os.path.abspath(skill_dir))


def classificar_ref(ref, skill_dir, raiz_plugin=None):
    """'ok' | 'ilustrativa' | 'gerado' | 'fora_da_skill' | 'ausente'."""
    if eh_ilustrativa(ref):
        return "ilustrativa"
    if eh_saida(ref) and not os.path.exists(
            os.path.join(skill_dir, ref.replace("\\", "/"))):
        return "gerado"
    alvo = ref.replace("\\", "/").lstrip("./")
    if os.path.exists(os.path.join(skill_dir, alvo)):
        return "ok"
    raiz = raiz_plugin or achar_raiz_plugin(skill_dir)
    # ref relativo à raiz do plugin é convenção legítima, não quebra
    if os.path.exists(os.path.join(raiz, alvo)):
        return "ok"
    # o mesmo recurso, compartilhado por outra skill do mesmo plugin
    for base, _dirs, _arqs in os.walk(raiz):
        if os.path.exists(os.path.join(base, alvo)):
            return "fora_da_skill"
    return "ausente"


def problemas_front_matter(texto):
    """Front-matter ausente/incompleto — o que impede a skill de ser indexada."""
    probs = []
    if not texto.startswith("---"):
        return ["sem front-matter (não carrega)"]
    fm = texto.split("---", 2)[1] if texto.count("---") >= 2 else ""
    if not re.search(r"^name:", fm, re.M):
        probs.append("front-matter sem 'name:'")
    if not re.search(r"^description:", fm, re.M):
        probs.append("front-matter sem 'description:'")
    return probs


def auditar_skill(caminho):
    """Lista de achados (dict) de uma SKILL.md. Vazio = skill limpa."""
    d = os.path.dirname(caminho)
    try:
        with open(caminho, encoding="utf-8", errors="replace") as f:
            texto = f.read()
    except OSError as e:
        return [{"tipo": "ilegivel", "sev": QUEBRA, "detalhe": str(e)}]

    achados = [{"tipo": "front-matter", "sev": QUEBRA, "detalhe": p}
               for p in problemas_front_matter(texto)]
    raiz = achar_raiz_plugin(d)
    # Uma ref só é dispensada pelo contexto se TODAS as ocorrências dela forem
    # escrita ou exemplo: basta uma frase que a carrega para ela voltar a valer.
    so_contexto = {}
    for ref, trecho in refs_com_contexto(texto):
        dispensa = eh_escrita(trecho) or eh_exemplo_em_prosa(trecho)
        so_contexto[ref] = so_contexto.get(ref, True) and dispensa
    for ref in extrair_refs(texto):
        if so_contexto.get(ref):
            continue
        estado = classificar_ref(ref, d, raiz)
        if estado == "ausente":
            achados.append({"tipo": "ref-ausente", "sev": QUEBRA, "detalhe": ref})
        elif estado == "fora_da_skill":
            achados.append({"tipo": "ref-fora-da-skill", "sev": AVISO,
                            "detalhe": f"{ref} (existe no plugin, não ao lado da skill)"})
    for ab in sorted(set(RE_ABS_ESTRANHO.findall(texto))):
        achados.append({"tipo": "caminho-de-outra-maquina", "sev": AVISO, "detalhe": ab})
    return achados


def _semver(nome):
    partes = re.findall(r"\d+", nome)
    return tuple(int(x) for x in partes[:4]) if partes else (-1,)


def versoes_ativas(installed_json=None):
    """Pasta de versão que o CLI carrega hoje, por plugin em cache.

    Fonte de verdade: `installed_plugins.json`. Quando o installPath declarado
    não existe no disco (plugin removido, clone movido), cai no maior semver da
    pasta do plugin. Sem esse filtro o scan lê 10 cópias de plugin-exemplo e
    conta o mesmo achado 10 vezes — inclusive em skills já corrigidas.
    """
    installed_json = installed_json or caminho_installed_json()
    ativos, declarados = set(), set()
    try:
        with open(installed_json, encoding="utf-8") as f:
            dados = json.load(f)
    except (OSError, ValueError):
        dados = {}
    for entradas in (dados.get("plugins") or {}).values():
        for e in entradas or []:
            p = e.get("installPath")
            if not p:
                continue
            declarados.add(os.path.normcase(os.path.dirname(os.path.abspath(p))))
            if os.path.isdir(p):
                ativos.add(os.path.normcase(os.path.abspath(p)))
    # fallback: plugin em cache sem installPath vivo → maior versão no disco
    raiz_cache = claude_cache()
    if os.path.isdir(raiz_cache):
        for mercado in os.listdir(raiz_cache):
            dm = os.path.join(raiz_cache, mercado)
            if not os.path.isdir(dm):
                continue
            for plugin in os.listdir(dm):
                dp = os.path.join(dm, plugin)
                if not os.path.isdir(dp):
                    continue
                chave = os.path.normcase(os.path.abspath(dp))
                if any(a.startswith(chave + os.sep) for a in ativos):
                    continue
                vers = [v for v in os.listdir(dp)
                        if os.path.isdir(os.path.join(dp, v))]
                if vers:
                    melhor = sorted(vers, key=_semver)[-1]
                    ativos.add(os.path.normcase(
                        os.path.abspath(os.path.join(dp, melhor))))
    return ativos


def _versao_obsoleta(caminho, ativos, raiz_cache):
    """SKILL.md dentro de `plugins/cache` mas fora da versão ativa do plugin."""
    p = os.path.normcase(os.path.abspath(caminho))
    if not p.startswith(raiz_cache + os.sep):
        return False          # skill de usuário, fora do cache: sempre varre
    return not any(p.startswith(a + os.sep) for a in ativos)


ALLOWLIST_JSON = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "allowlist.json")
CAMPOS_ALLOW = ("skill", "tipo", "detalhe", "motivo", "issue", "data")


def carregar_allowlist(caminho=ALLOWLIST_JSON):
    """Entradas de defeito conhecido de TERCEIRO, sempre com issue e data.

    Fail-closed: arquivo ilegível ou entrada sem os 6 campos derruba o scan em
    vez de silenciar achado. Allowlist sem issue aberta é esquecimento com cara
    de relatório limpo — o dia em que o upstream conserta ninguém descobre.
    """
    if not os.path.exists(caminho):
        return []
    with open(caminho, encoding="utf-8") as f:
        dados = json.load(f)
    entradas = dados.get("entradas") or []
    for i, e in enumerate(entradas):
        faltando = [c for c in CAMPOS_ALLOW if not str(e.get(c, "")).strip()]
        if faltando:
            raise ValueError(
                f"allowlist[{i}]: campo obrigatório vazio: {', '.join(faltando)}")
        if not str(e["issue"]).startswith("http"):
            raise ValueError(f"allowlist[{i}]: 'issue' precisa ser URL da issue aberta")
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", str(e["data"])):
            raise ValueError(f"allowlist[{i}]: 'data' precisa ser AAAA-MM-DD")
    return entradas


def eh_de_terceiro(caminho):
    """Só skill empacotada por outro dono pode entrar na allowlist.

    Skill nossa (marketplace `marketplace-exemplo`) e skill de usuário em `~/.claude/skills`
    são responsabilidade nossa: defeito ali se conserta, não se declara conhecido.
    """
    p = os.path.normcase(os.path.abspath(caminho)).replace("\\", "/")
    raiz_cache = os.path.normcase(os.path.abspath(
        claude_cache())).replace("\\", "/")
    if not p.startswith(raiz_cache + "/"):
        return False
    mercado = p[len(raiz_cache) + 1:].split("/")[0]
    return mercado != "marketplace-exemplo"


def _casa(entrada, caminho, achado):
    return (entrada["skill"] == os.path.basename(os.path.dirname(caminho))
            and entrada["tipo"] == achado["tipo"]
            and entrada["detalhe"] in achado["detalhe"]
            and eh_de_terceiro(caminho))


def aplicar_allowlist(res, entradas):
    """Separa achados conhecidos. Devolve (res_limpo, silenciados, mortas)."""
    silenciados, usadas = [], set()
    limpo = {}
    for caminho, achados in res.items():
        restantes = []
        for a in achados:
            casou = next((i for i, e in enumerate(entradas)
                          if _casa(e, caminho, a)), None)
            if casou is None:
                restantes.append(a)
            else:
                usadas.add(casou)
                silenciados.append((caminho, a, entradas[casou]))
        limpo[caminho] = restantes
    mortas = [e for i, e in enumerate(entradas) if i not in usadas]
    return limpo, silenciados, mortas


def varrer(raizes, so_ativas=True):
    resultado = {}
    ativos = versoes_ativas() if so_ativas else set()
    raiz_cache = os.path.normcase(
        os.path.abspath(claude_cache()))
    for raiz in raizes:
        if not os.path.isdir(raiz):
            continue
        for base, _dirs, arqs in os.walk(raiz):
            if "SKILL.md" in arqs:
                p = os.path.join(base, "SKILL.md")
                if so_ativas and _versao_obsoleta(p, ativos, raiz_cache):
                    continue
                resultado[p] = auditar_skill(p)
    return resultado


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--raiz", action="append", default=None)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--quebras", action="store_true",
                    help="só o que impede a skill de funcionar")
    ap.add_argument("--todas-versoes", action="store_true",
                    help="varre também versões velhas em cache (ruído)")
    ap.add_argument("--sem-allowlist", action="store_true",
                    help="ignora allowlist.json e mostra o acervo cru")
    a = ap.parse_args(argv)

    res = varrer(a.raiz or raizes_padrao(), so_ativas=not a.todas_versoes)
    if a.quebras:
        res = {k: [x for x in v if x["sev"] == QUEBRA] for k, v in res.items()}
    silenciados, mortas = [], []
    if not a.sem_allowlist:
        res, silenciados, mortas = aplicar_allowlist(res, carregar_allowlist())
    sujas = {k: v for k, v in res.items() if v}

    if a.json:
        print(json.dumps({"total": len(res), "com_achado": len(sujas), "itens": sujas,
                          "conhecidos": [{"caminho": c, "achado": x, "allow": e}
                                         for c, x, e in silenciados],
                          "allowlist_morta": mortas},
                         ensure_ascii=False, indent=2))
    else:
        print(f"SKILL.md varridas: {len(res)} | com achado: {len(sujas)}"
              f" | conhecidos (allowlist): {len(silenciados)}")
        for p, achados in sorted(sujas.items()):
            print(f"\n### {os.path.basename(os.path.dirname(p))}")
            print(f"    {p}")
            for x in achados:
                print(f"    [{x['sev']}] {x['tipo']}: {x['detalhe']}")
        for c, x, e in silenciados:
            print(f"\n[conhecido] {os.path.basename(os.path.dirname(c))}: "
                  f"{x['tipo']}: {x['detalhe']}\n    {e['motivo']} | {e['issue']} ({e['data']})")
        for e in mortas:
            print(f"\n[allowlist morta] {e['skill']}: {e['detalhe']} não aparece mais "
                  f"- confira {e['issue']} e apague a entrada")
    return 1 if any(x["sev"] == QUEBRA for v in sujas.values() for x in v) else 0


if __name__ == "__main__":
    sys.exit(main())
