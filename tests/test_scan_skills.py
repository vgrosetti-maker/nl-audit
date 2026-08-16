# -*- coding: utf-8 -*-
"""Suíte do scan_skills.py — com os dois falsos positivos medidos congelados.

Detector que nunca acusou nada não prova nada; detector que acusa tudo prova
menos ainda. Em 15/08/2026 o rascunho deste scan apontou 12 skills "quebradas"
de 248 e as duas que eu abri à mão eram falso positivo:

  - `exact/path/to/test.py` em superpowers:writing-plans → caminho DIDÁTICO;
  - `references/backlink-quality.md` em claude-seo:seo-backlinks → o arquivo
    EXISTE, em `skills/seo/references/`, compartilhado entre skills irmãs.

Os dois viraram teste. Cada invariante tem as duas pontas: o defeito injetado
de propósito TEM que acusar, e o caso legítimo NÃO pode.
"""
import contextlib
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scan_skills import (achar_raiz_plugin, aplicar_allowlist, auditar_skill, carregar_allowlist,
                         classificar_ref, sem_ancora,
                         eh_saida, versoes_ativas, _versao_obsoleta,
                         eh_ilustrativa, extrair_refs, parece_caminho,
                         problemas_front_matter, varrer)

FM = "---\nname: teste\ndescription: skill de teste\n---\n\n"


@contextlib.contextmanager
def usuario_local(nome):
    """Fixa quem e o dono desta maquina para o teste nao depender do ambiente."""
    import scan_skills
    antes = scan_skills.RE_ABS_ESTRANHO
    scan_skills.RE_ABS_ESTRANHO = scan_skills.montar_regex_estranho(nome)
    try:
        yield
    finally:
        scan_skills.RE_ABS_ESTRANHO = antes


def montar(raiz, skill, corpo, recursos=()):
    """Cria <raiz>/skills/<skill>/SKILL.md e os recursos pedidos (relativos à raiz)."""
    d = os.path.join(raiz, "skills", skill)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "SKILL.md"), "w", encoding="utf-8") as f:
        f.write(corpo)
    for r in recursos:
        alvo = os.path.join(raiz, r)
        os.makedirs(os.path.dirname(alvo), exist_ok=True)
        with open(alvo, "w", encoding="utf-8") as fr:
            fr.write("x")
    return os.path.join(d, "SKILL.md")


class TestIlustrativa(unittest.TestCase):
    def test_caminho_didatico_nao_e_dependencia(self):
        # ponta negativa: nenhum destes pode virar achado
        for ref in ("exact/path/to/test.py", "path/to/file.py", "/path/to/x.json",
                    "scripts/YOUR_SCRIPT.py", "references/<SKILL_DIR>/a.md",
                    "caminho/para/arquivo.md", "references/${VAR}.md"):
            self.assertTrue(eh_ilustrativa(ref), ref)

    def test_referencia_real_nao_e_confundida_com_exemplo(self):
        # ponta positiva: refs de verdade têm que passar pelo filtro
        for ref in ("references/backlink-quality.md", "scripts/parse_html.py",
                    "assets/tokens.css", "templates/post.md"):
            self.assertFalse(eh_ilustrativa(ref), ref)


class TestPareceCaminho(unittest.TestCase):
    def test_prosa_em_crase_nao_e_caminho(self):
        for ref in ("--json", "npm run build", "gsap.to()", "name:", "-Force",
                    "https://x.com/a.md", "# titulo"):
            self.assertFalse(parece_caminho(ref), ref)

    def test_recurso_e_caminho(self):
        for ref in ("references/a.md", "scripts/b.py", "./assets/c.json",
                    "data/x.yaml", "hooks/h.sh"):
            self.assertTrue(parece_caminho(ref), ref)


class TestClassificarRef(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.raiz = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_ref_ao_lado_da_skill_e_ok(self):
        p = montar(self.raiz, "s1", FM + "Veja `references/guia.md`.",
                   recursos=("skills/s1/references/guia.md",))
        self.assertEqual(classificar_ref("references/guia.md", os.path.dirname(p)), "ok")

    def test_ausente_de_verdade_acusa(self):
        # defeito injetado: o arquivo não existe em canto nenhum
        p = montar(self.raiz, "s2", FM + "Veja `references/fantasma.md`.")
        self.assertEqual(classificar_ref("references/fantasma.md", os.path.dirname(p)),
                         "ausente")

    def test_recurso_compartilhado_entre_skills_irmas_nao_e_ausente(self):
        # o falso positivo do claude-seo, byte por byte
        p = montar(self.raiz, "seo-backlinks", FM + "Load `references/backlink-quality.md`.",
                   recursos=("skills/seo/references/backlink-quality.md",))
        self.assertEqual(
            classificar_ref("references/backlink-quality.md", os.path.dirname(p)),
            "fora_da_skill")

    def test_achar_raiz_plugin_sobe_ate_skills(self):
        p = montar(self.raiz, "s3", FM + "vazio")
        self.assertEqual(os.path.normcase(achar_raiz_plugin(os.path.dirname(p))),
                         os.path.normcase(self.raiz))


class TestFrontMatter(unittest.TestCase):
    def test_sem_front_matter_acusa(self):
        self.assertEqual(problemas_front_matter("# skill\ntexto"),
                         ["sem front-matter (não carrega)"])

    def test_faltando_campo_acusa_o_campo_certo(self):
        probs = problemas_front_matter("---\ndescription: x\n---\ncorpo")
        self.assertEqual(probs, ["front-matter sem 'name:'"])

    def test_front_matter_completo_fica_calado(self):
        self.assertEqual(problemas_front_matter(FM + "corpo"), [])


class TestAuditarSkill(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.raiz = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_skill_didatica_sai_limpa(self):
        """O caso writing-plans: só exemplo em crase, zero achado."""
        corpo = (FM + "Escreva o plano assim:\n\n"
                 "- Create: `exact/path/to/file.py`\n"
                 "- Modify: `exact/path/to/existing.py:123-145`\n"
                 "- Test: `tests/exact/path/to/test.py`\n")
        p = montar(self.raiz, "writing-plans", corpo)
        self.assertEqual(auditar_skill(p), [])

    def test_skill_quebrada_de_verdade_acusa_uma_vez(self):
        corpo = FM + "Rode `scripts/sumiu.py` antes de tudo."
        p = montar(self.raiz, "quebrada", corpo)
        achados = auditar_skill(p)
        self.assertEqual([a["tipo"] for a in achados], ["ref-ausente"])
        self.assertEqual(achados[0]["sev"], "quebra")

    def test_compartilhado_e_aviso_nao_quebra(self):
        corpo = FM + "Load `references/backlink-quality.md`."
        p = montar(self.raiz, "seo-backlinks", corpo,
                   recursos=("skills/seo/references/backlink-quality.md",))
        achados = auditar_skill(p)
        self.assertEqual([a["tipo"] for a in achados], ["ref-fora-da-skill"])
        self.assertEqual(achados[0]["sev"], "aviso")

    def test_caminho_de_outra_maquina_acusa(self):
        corpo = FM + "Config em /home/outro/.claude/x.json e C:\\Users\\outro\\y.md"
        p = montar(self.raiz, "estranha", corpo)
        with usuario_local("eu"):
            tipos = [a["tipo"] for a in auditar_skill(p)]
        self.assertEqual(tipos.count("caminho-de-outra-maquina"), 2)

    def test_caminho_do_usuario_local_nao_acusa(self):
        # Teste negativo do par acima: mesmo formato de caminho, home DESTA
        # maquina - o scan so acusa dependencia que nunca vai existir aqui.
        corpo = FM + "Fonte em C:\\Users\\eu\\tools e /Users/eu/x.md"
        p = montar(self.raiz, "propria", corpo)
        with usuario_local("eu"):
            self.assertEqual(auditar_skill(p), [])

    def test_ref_repetida_conta_uma_vez_so(self):
        corpo = FM + "`scripts/sumiu.py` e de novo `scripts/sumiu.py`."
        p = montar(self.raiz, "repetida", corpo)
        self.assertEqual(len(auditar_skill(p)), 1)

    def test_varrer_acha_todas_e_so_suja_tem_achado(self):
        montar(self.raiz, "limpa", FM + "sem refs")
        montar(self.raiz, "suja", FM + "`scripts/nada.py`")
        res = varrer([self.raiz])
        self.assertEqual(len(res), 2)
        self.assertEqual(sum(1 for v in res.values() if v), 1)


class TestExtrairRefs(unittest.TestCase):
    def test_pega_crase_e_markdown_link(self):
        refs = extrair_refs("veja `references/a.md` e [b](scripts/b.py) e `--flag`")
        self.assertEqual(refs, ["references/a.md", "scripts/b.py"])


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestAncora(unittest.TestCase):
    """FP real nº1 congelado: web-quality-skills:accessibility citava
    `references/A11Y-PATTERNS.md#skip-link` e o scan cobrava o arquivo
    com a ancora colada no nome."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.raiz = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_ancora_nao_vira_parte_do_nome(self):
        self.assertEqual(sem_ancora("references/A11Y-PATTERNS.md#skip-link"),
                         "references/A11Y-PATTERNS.md")

    def test_ref_so_ancora_fica_intacta_e_e_rejeitada(self):
        self.assertEqual(sem_ancora("#skip-link"), "#skip-link")

    def test_arquivo_que_existe_com_ancora_nao_acusa(self):
        corpo = FM + "Veja `references/guia.md#secao`."
        p = montar(self.raiz, "a11y", corpo,
                   recursos=("skills/a11y/references/guia.md",))
        self.assertEqual(auditar_skill(p), [])

    def test_ancora_em_arquivo_ausente_ainda_acusa(self):
        # ponta negativa: a ancora nao pode virar anistia
        corpo = FM + "Veja `references/fantasma.md#secao`."
        p = montar(self.raiz, "a11y", corpo)
        self.assertEqual([a["tipo"] for a in auditar_skill(p)], ["ref-ausente"])


class TestArtefatoDoAlvo(unittest.TestCase):
    """FP real nº2 congelado: `robots.txt` e `/llms.txt` sao arquivos do SITE
    auditado, nao recurso que a skill carrega."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.raiz = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_artefato_do_site_nao_e_dependencia(self):
        for ref in ("robots.txt", "/llms.txt", "llms.txt", "sitemap.xml"):
            self.assertFalse(parece_caminho(ref), ref)

    def test_skill_de_seo_fica_limpa(self):
        corpo = FM + "Confira `robots.txt`, `/llms.txt` e `sitemap.xml` no site."
        p = montar(self.raiz, "seo", corpo)
        self.assertEqual(auditar_skill(p), [])

    def test_recurso_de_verdade_na_pasta_ainda_acusa(self):
        # ponta negativa: allowlist nao pode cegar a pasta de recurso
        corpo = FM + "Carrega `references/robots.txt`."
        p = montar(self.raiz, "seo", corpo)
        self.assertEqual([a["tipo"] for a in auditar_skill(p)], ["ref-ausente"])

    def test_nome_solto_com_barra_continua_valendo(self):
        self.assertTrue(parece_caminho("scripts/b.py"))
        self.assertFalse(parece_caminho("README.md"))


class TestSaida(unittest.TestCase):
    """FP real nº 3 congelado: caminho que a skill GRAVA nao e dependencia."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.raiz = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_artefato_gerado_nao_acusa(self):
        corpo = FM + "Grave o laudo em `_qa/mobile/2026-08-03-2259-e3d51c4.md`."
        p = montar(self.raiz, "mob", corpo)
        self.assertEqual(auditar_skill(p), [])

    def test_repo_alvo_nao_acusa(self):
        corpo = FM + "Leia `graphify-out/graph.json` e `netlify/functions/x.js`."
        p = montar(self.raiz, "gr", corpo)
        self.assertEqual(auditar_skill(p), [])

    def test_recurso_de_verdade_continua_acusando(self):
        # ponta negativa: a allowlist nao pode cegar pasta de recurso
        p = montar(self.raiz, "seo", FM + "Carrega `references/fantasma.md`.")
        self.assertEqual([a["tipo"] for a in auditar_skill(p)], ["ref-ausente"])

    def test_prefixo_e_nao_substring(self):
        # `skills/x/plans.md` nao vira saida so por conter "plans"
        self.assertFalse(eh_saida("references/plans/guia.md"))
        self.assertTrue(eh_saida("plans/README.md"))
        self.assertTrue(eh_saida(".claude/settings.json"))  # lstrip("./") comeria o ponto

    def test_arquivo_que_existe_continua_ok(self):
        corpo = FM + "Veja `blog/DNA.md`."
        p = montar(self.raiz, "b1", corpo, recursos=("skills/b1/blog/DNA.md",))
        self.assertEqual(classificar_ref("blog/DNA.md", os.path.dirname(p)), "ok")


class TestVersaoAtiva(unittest.TestCase):
    """10 copias de plugin-exemplo em cache multiplicavam o mesmo achado."""

    def setUp(self):
        self.cache = os.path.normcase(
            os.path.abspath(os.path.join(os.sep, "c", "plugins", "cache")))
        self.ativa = os.path.join(self.cache, "mkt", "plug", "4.17.0")
        self.ativos = {os.path.normcase(self.ativa)}

    def _skill(self, base):
        return os.path.join(base, "skills", "s", "SKILL.md")

    def test_versao_ativa_e_varrida(self):
        self.assertFalse(_versao_obsoleta(self._skill(self.ativa), self.ativos, self.cache))

    def test_versao_velha_e_pulada(self):
        velha = os.path.join(self.cache, "mkt", "plug", "4.6.0")
        self.assertTrue(_versao_obsoleta(self._skill(velha), self.ativos, self.cache))

    def test_skill_de_usuario_fora_do_cache_nunca_e_pulada(self):
        # ponta negativa: o filtro nao pode sumir com ~/.claude/skills
        fora = os.path.abspath(
            os.path.join(os.sep, "c", ".claude", "skills", "x", "SKILL.md"))
        self.assertFalse(_versao_obsoleta(fora, self.ativos, self.cache))

    def test_json_ilegivel_nao_derruba_o_scan(self):
        self.assertIsInstance(versoes_ativas("/nao/existe/installed.json"), set)


class TestRefRelativoARaizDoPlugin(unittest.TestCase):
    """FP real no 4 congelado: `extensions/ahrefs/install.sh` do claude-seo
    existe a partir da raiz do plugin, nao da pasta da skill."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.raiz = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def _plugin(self):
        # <raiz>/plug/.claude-plugin  +  <raiz>/plug/skills/s/SKILL.md
        d = os.path.join(self.raiz, "plug")
        os.makedirs(os.path.join(d, ".claude-plugin"), exist_ok=True)
        sk = os.path.join(d, "skills", "s")
        os.makedirs(sk, exist_ok=True)
        with open(os.path.join(sk, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write(FM + "Rode `extensions/ahrefs/install.sh`.\n")
        return d, sk

    def test_ref_a_partir_da_raiz_do_plugin_e_ok(self):
        d, sk = self._plugin()
        alvo = os.path.join(d, "extensions", "ahrefs", "install.sh")
        os.makedirs(os.path.dirname(alvo), exist_ok=True)
        with open(alvo, "w", encoding="utf-8") as f:
            f.write("x")
        self.assertEqual(
            classificar_ref("extensions/ahrefs/install.sh", sk), "ok")
        self.assertEqual(auditar_skill(os.path.join(sk, "SKILL.md")), [])

    def test_ref_que_nao_existe_em_lugar_nenhum_continua_ausente(self):
        # ponta negativa: sem o arquivo na raiz, o achado tem que voltar
        d, sk = self._plugin()
        achados = auditar_skill(os.path.join(sk, "SKILL.md"))
        self.assertEqual([a["tipo"] for a in achados], ["ref-ausente"])


class TestContextoDaFrase(unittest.TestCase):
    """Famílias 6 e 7 de falso positivo, medidas em 15/08/2026 no acervo real:

      - `evals/evals.json` (skill-creator): "Save test cases to ..." — arquivo
        que a skill GRAVA, impossível existir antes de ela rodar;
      - `feature/bento-grid--material.md` (design-taste-frontend): apresentado
        como "(e.g. ...)" do molde `blocks/<category>/<name>--<system>.md`.

    Cada uma com as duas pontas: sem o verbo/sem o "e.g.", o achado volta.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.raiz = self.tmp.name
        self.addCleanup(self.tmp.cleanup)

    def _tipos(self, corpo):
        return [a["tipo"] for a in auditar_skill(montar(self.raiz, "s", FM + corpo))]

    def test_destino_de_escrita_nao_e_dependencia(self):
        self.assertEqual(self._tipos("Save test cases to `evals/evals.json`.\n"), [])

    def test_mesmo_arquivo_lido_ainda_acusa(self):
        # ponta negativa da família da escrita
        self.assertEqual(self._tipos("Read the cases from `evals/evals.json`.\n"),
                         ["ref-ausente"])

    def test_manda_o_ultimo_verbo_antes_da_ref(self):
        # `references/a.md` é lido (acusa); `out/b.json` é gravado (cala)
        self.assertEqual(
            self._tipos("Read `references/a.md` and save the result to `out/b.json`.\n"),
            ["ref-ausente"])

    def test_uma_leitura_derruba_a_dispensa(self):
        # gravado numa linha, lido em outra: continua sendo dependência
        self.assertEqual(
            self._tipos("Write `evals/evals.json`.\n\nThen load `evals/evals.json`.\n"),
            ["ref-ausente"])

    def test_exemplo_apos_eg_nao_e_dependencia(self):
        self.assertEqual(self._tipos(
            "Blocks live under `blocks/<category>/<name>--<system>.md` "
            "(e.g. `feature/bento-grid--material.md`).\n"), [])

    def test_mesmo_caminho_sem_eg_acusa(self):
        # ponta negativa da família do exemplo
        self.assertEqual(self._tipos(
            "Blocks live under `feature/bento-grid--material.md`.\n"),
            ["ref-ausente"])

    def test_eg_nao_dispensa_ref_de_outra_linha(self):
        self.assertEqual(self._tipos(
            "Veja, por exemplo, `references/a.md`.\n\nCarregue `references/b.md`.\n"),
            ["ref-ausente"])


class TestAllowlist(unittest.TestCase):
    """Allowlist de defeito de terceiro - com as duas pontas.

    Ponta positiva: achado declarado sai do placar de quebras.
    Ponta negativa (a que importa): a mesma declaracao NAO pode silenciar skill
    nossa nem skill de usuario, e declaracao incompleta derruba o scan.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = self.tmp.name
        # HOME e USERPROFILE juntos: expanduser("~") le USERPROFILE no Windows e
        # HOME no Linux. Trocar so um deixa o teste verde na maquina do autor e
        # vermelho no CI - foi o que aconteceu no primeiro push do repo publico.
        self._old = {k: os.environ.get(k) for k in ("HOME", "USERPROFILE")}
        for k in self._old:
            os.environ[k] = self.home
        self.addCleanup(self._restaurar_home)

    def _restaurar_home(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _skill_em(self, mercado, plugin, skill):
        d = os.path.join(self.home, ".claude", "plugins", "cache",
                         mercado, plugin, "1.0.0", "skills", skill)
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, "SKILL.md")

    ENTRADA = {"plugin": "x", "skill": "barba-js", "tipo": "ref-ausente",
               "detalhe": "examples/", "motivo": "pacote incompleto",
               "issue": "https://github.com/o/r/issues/8", "data": "2026-08-15"}
    ACHADO = {"tipo": "ref-ausente", "sev": "quebra", "detalhe": "examples/"}

    def test_terceiro_declarado_sai_do_placar(self):
        p = self._skill_em("claude-design-skillstack", "barba-js", "barba-js")
        limpo, silenciados, mortas = aplicar_allowlist({p: [dict(self.ACHADO)]},
                                                       [dict(self.ENTRADA)])
        self.assertEqual(limpo[p], [])
        self.assertEqual(len(silenciados), 1)
        self.assertEqual(mortas, [])

    def test_skill_nossa_nao_pode_ser_silenciada(self):
        # ponta negativa: mesmo achado, mesma entrada, plugin marketplace-exemplo
        p = self._skill_em("marketplace-exemplo", "plugin-exemplo", "barba-js")
        limpo, silenciados, _ = aplicar_allowlist({p: [dict(self.ACHADO)]},
                                                  [dict(self.ENTRADA)])
        self.assertEqual(len(limpo[p]), 1)
        self.assertEqual(silenciados, [])

    def test_skill_de_usuario_fora_do_cache_nao_pode_ser_silenciada(self):
        d = os.path.join(self.home, ".claude", "skills", "barba-js")
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, "SKILL.md")
        limpo, silenciados, _ = aplicar_allowlist({p: [dict(self.ACHADO)]},
                                                  [dict(self.ENTRADA)])
        self.assertEqual(len(limpo[p]), 1)
        self.assertEqual(silenciados, [])

    def test_entrada_de_outra_skill_nao_vale(self):
        p = self._skill_em("claude-design-skillstack", "barba-js", "lottie-animations")
        limpo, silenciados, _ = aplicar_allowlist({p: [dict(self.ACHADO)]},
                                                  [dict(self.ENTRADA)])
        self.assertEqual(len(limpo[p]), 1)
        self.assertEqual(silenciados, [])

    def test_entrada_morta_e_denunciada(self):
        p = self._skill_em("claude-design-skillstack", "barba-js", "barba-js")
        _, _, mortas = aplicar_allowlist({p: []}, [dict(self.ENTRADA)])
        self.assertEqual(len(mortas), 1)

    def _grava_allow(self, entrada):
        alvo = os.path.join(self.tmp.name, "allowlist.json")
        with open(alvo, "w", encoding="utf-8") as f:
            json.dump({"entradas": [entrada]}, f)
        return alvo

    def test_sem_issue_derruba_o_scan(self):
        e = dict(self.ENTRADA, issue="")
        with self.assertRaises(ValueError):
            carregar_allowlist(self._grava_allow(e))

    def test_issue_que_nao_e_url_derruba_o_scan(self):
        e = dict(self.ENTRADA, issue="pedi no discord")
        with self.assertRaises(ValueError):
            carregar_allowlist(self._grava_allow(e))

    def test_sem_motivo_derruba_o_scan(self):
        e = dict(self.ENTRADA, motivo="   ")
        with self.assertRaises(ValueError):
            carregar_allowlist(self._grava_allow(e))

    def test_data_fora_do_formato_derruba_o_scan(self):
        e = dict(self.ENTRADA, data="ontem")
        with self.assertRaises(ValueError):
            carregar_allowlist(self._grava_allow(e))

    def test_allowlist_ausente_e_lista_vazia(self):
        self.assertEqual(carregar_allowlist(
            os.path.join(self.tmp.name, "nao-existe.json")), [])

    def test_allowlist_do_repo_e_valida(self):
        self._restaurar_home()
        entradas = carregar_allowlist()
        self.assertTrue(entradas, "allowlist.json do repo deveria ter as 2 entradas")
