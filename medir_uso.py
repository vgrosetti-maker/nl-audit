# -*- coding: utf-8 -*-
"""
Mede uso REAL do panteao: cruza o inventario de skills/agentes instalados
com as invocacoes encontradas nos transcripts .jsonl do Claude Code.
Nao le conteudo de mensagem: extrai apenas nome de tool, nome de skill/agente e timestamp.
"""
import json, os, sys, re, glob
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta

HOME = os.environ.get("NL_AUDIT_HOME") or os.path.expanduser("~")
CLAUDE = os.path.join(HOME, ".claude")
PROJECTS = os.path.join(CLAUDE, "projects")
OUT_DIR = os.environ.get("NL_AUDIT_OUT") or os.path.join(HOME, ".scratch", "panteao-uso")
JANELA_DIAS = 30
HOJE = datetime.now(timezone.utc)  # antes: fixo em 2026-08-09, o que mentia o rotulo da janela
CORTE = HOJE - timedelta(days=JANELA_DIAS)

# ---------------- inventario ----------------

def frontmatter_name(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            head = f.read(2000)
    except OSError:
        return None
    m = re.search(r"^name:\s*(.+?)\s*$", head, re.M)
    return m.group(1).strip().strip('"').strip("'") if m else None


def inventario():
    skills, agents = {}, {}   # ref -> origem
    ip = os.path.join(CLAUDE, "plugins", "installed_plugins.json")
    data = json.load(open(ip, encoding="utf-8"))
    for key, entries in data.get("plugins", {}).items():
        plugin = key.split("@")[0]
        for e in entries:
            root = e.get("installPath")
            if not root or not os.path.isdir(root):
                continue
            sdir = os.path.join(root, "skills")
            for p in glob.glob(os.path.join(sdir, "**", "SKILL.md"), recursive=True):
                nome = os.path.basename(os.path.dirname(p))
                skills[f"{plugin}:{nome}"] = f"plugin {plugin}"
            adir = os.path.join(root, "agents")
            for p in glob.glob(os.path.join(adir, "**", "*.md"), recursive=True):
                nome = frontmatter_name(p) or os.path.splitext(os.path.basename(p))[0]
                agents[f"{plugin}:{nome}"] = f"plugin {plugin}"
    for p in glob.glob(os.path.join(CLAUDE, "skills", "**", "SKILL.md"), recursive=True):
        nome = os.path.basename(os.path.dirname(p))
        skills[nome] = "usuario"
    for p in glob.glob(os.path.join(CLAUDE, "agents", "*.md")):
        nome = frontmatter_name(p) or os.path.splitext(os.path.basename(p))[0]
        agents[nome] = "usuario"
    return skills, agents

# ---------------- transcripts ----------------

SKILL_TOOLS = {"Skill"}
AGENT_TOOLS = {"Task", "Agent", "AgentTool"}
CMD_RE = re.compile(r"<command-name>\s*(/[^<\s]+)")


def ts_of(obj):
    t = obj.get("timestamp")
    if not t:
        return None
    try:
        return datetime.fromisoformat(t.replace("Z", "+00:00"))
    except ValueError:
        return None


def blocks(msg):
    if isinstance(msg, dict):
        c = msg.get("content")
        if isinstance(c, list):
            for b in c:
                if isinstance(b, dict):
                    yield b


def varrer():
    """retorna dict: categoria -> Counter(nome -> hits) para janela e all-time,
    mais por-arquivo pra validacao."""
    jan = {"skill": Counter(), "agent": Counter(), "cmd": Counter()}
    alltime = {"skill": Counter(), "agent": Counter(), "cmd": Counter()}
    por_arquivo = defaultdict(lambda: defaultdict(set))
    arquivos = glob.glob(os.path.join(PROJECTS, "**", "*.jsonl"), recursive=True)
    linhas = ruins = 0
    for fp in arquivos:
        base = os.path.basename(fp)
        with open(fp, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                linhas += 1
                try:
                    obj = json.loads(line)
                except Exception:
                    ruins += 1
                    continue
                if not isinstance(obj, dict):
                    continue
                t = ts_of(obj)
                dentro = bool(t and t >= CORTE)
                msg = obj.get("message")
                achados = []
                for b in blocks(msg):
                    if b.get("type") != "tool_use":
                        continue
                    nome = b.get("name")
                    inp = b.get("input") or {}
                    if not isinstance(inp, dict):
                        continue
                    if nome in SKILL_TOOLS:
                        v = inp.get("skill") or inp.get("command") or inp.get("name")
                        if v:
                            achados.append(("skill", str(v)))
                    elif nome in AGENT_TOOLS:
                        v = inp.get("subagent_type")
                        if v:
                            achados.append(("agent", str(v)))
                    elif nome == "SlashCommand":
                        v = inp.get("command")
                        if v:
                            achados.append(("cmd", str(v).split()[0]))
                if isinstance(msg, dict) and msg.get("role") == "user":
                    c = msg.get("content")
                    txt = c if isinstance(c, str) else ""
                    if not txt:
                        for b in blocks(msg):
                            if b.get("type") == "text":
                                txt += b.get("text") or ""
                    for m in CMD_RE.finditer(txt):
                        achados.append(("cmd", m.group(1)))
                for cat, val in achados:
                    alltime[cat][val] += 1
                    por_arquivo[base][cat].add(val)
                    if dentro:
                        jan[cat][val] += 1
    return jan, alltime, por_arquivo, len(arquivos), linhas, ruins


def main():
    skills, agents = inventario()
    jan, alltime, por_arquivo, n_arq, linhas, ruins = varrer()

    # ---- validacao: a sessao atual TEM que aparecer (teste positivo obrigatorio)
    atual = "85261882-9689-4fa4-b635-62157ef444d5.jsonl"
    v = por_arquivo.get(atual, {})
    vs, va = v.get("skill", set()), v.get("agent", set())
    ok_skill = any("zeus-orquestra" in x or "grilling" in x for x in vs)
    ok_agent = len(va) > 0
    if not (ok_skill and ok_agent):
        print("PARSER REPROVADO no teste positivo.", file=sys.stderr)
        print("  skills vistas na sessao atual:", sorted(vs), file=sys.stderr)
        print("  agentes vistos na sessao atual:", sorted(va), file=sys.stderr)
        sys.exit(2)

    # slash command que casa com uma skill instalada CONTA como invocacao da skill
    # (descoberto por teste negativo: /watch:watch existia e a skill aparecia como nunca usada)
    curtos_skill = {}
    for ref in skills:
        curtos_skill.setdefault(ref.split(":")[-1], ref)
    via_cmd = Counter()
    for c, n in jan["cmd"].items():
        alvo = c.lstrip("/")
        if alvo in skills:
            via_cmd[alvo] += n
        elif alvo.split(":")[-1] in curtos_skill:
            via_cmd[curtos_skill[alvo.split(":")[-1]]] += n
    jan["skill"].update(via_cmd)

    def parte(inv, cat, rotulo):
        usados_ref = jan[cat]
        # casa por ref exata OU por sufixo depois de ':'
        def hits(ref):
            n = usados_ref.get(ref, 0)
            curto = ref.split(":")[-1]
            for k, c in usados_ref.items():
                if k != ref and k.split(":")[-1] == curto:
                    n += c
            return n
        linhas_ = [(ref, inv[ref], hits(ref)) for ref in sorted(inv)]
        usados = [x for x in linhas_ if x[2] > 0]
        nunca = [x for x in linhas_ if x[2] == 0]
        return rotulo, linhas_, usados, nunca

    os.makedirs(OUT_DIR, exist_ok=True)
    rel = os.path.join(OUT_DIR, "relatorio.md")
    with open(rel, "w", encoding="utf-8") as f:
        f.write("# Uso real do panteao - janela de %d dias (%s a %s)\n\n"
                % (JANELA_DIAS, CORTE.date(), HOJE.date()))
        f.write("Fonte: %d transcripts .jsonl em `.claude/projects` (%d linhas, %d ilegiveis).\n"
                % (n_arq, linhas, ruins))
        f.write("Extraido: tool_use `Skill` (campo skill), `Task/Agent` (subagent_type), "
                "`SlashCommand` e `<command-name>` em turno de usuario.\n\n")
        f.write("Validacao do parser: sessao atual acusou skills %s e agentes %s.\n\n"
                % (sorted(vs), sorted(va)))
        for inv, cat, rotulo in ((skills, "skill", "SKILLS"), (agents, "agent", "AGENTES")):
            rotulo, todas, usados, nunca = parte(inv, cat, rotulo)
            f.write("## %s: %d instalados, %d usados, %d nunca invocados\n\n"
                    % (rotulo, len(todas), len(usados), len(nunca)))
            f.write("### Usados na janela\n\n| ref | origem | invocacoes |\n|---|---|---|\n")
            for ref, org, n in sorted(usados, key=lambda x: -x[2]):
                f.write("| %s | %s | %d |\n" % (ref, org, n))
            f.write("\n### Nunca invocados na janela\n\n")
            for ref, org, _ in nunca:
                f.write("- %s (%s)\n" % (ref, org))
            f.write("\n")
        f.write("## Invocacoes sem correspondencia no inventario\n\n")
        for cat in ("skill", "agent"):
            inv = skills if cat == "skill" else agents
            curtos = {r.split(":")[-1] for r in inv}
            orfaos = {k: c for k, c in jan[cat].items()
                      if k not in inv and k.split(":")[-1] not in curtos}
            f.write("- %s: %s\n" % (cat, dict(sorted(orfaos.items(), key=lambda x: -x[1])) or "nenhum"))
        f.write("\n## Slash commands na janela\n\n")
        for k, c in jan["cmd"].most_common(30):
            f.write("- %s: %d\n" % (k, c))

    # console
    for inv, cat, rotulo in ((skills, "skill", "SKILLS"), (agents, "agent", "AGENTES")):
        _, todas, usados, nunca = parte(inv, cat, rotulo)
        print("%s: %d instalados | %d usados | %d nunca (%.0f%% ocioso)"
              % (rotulo, len(todas), len(usados), len(nunca),
                 100.0 * len(nunca) / max(1, len(todas))))
    print("arquivos=%d linhas=%d ilegiveis=%d" % (n_arq, linhas, ruins))
    print("relatorio:", rel)


if __name__ == "__main__":
    main()
