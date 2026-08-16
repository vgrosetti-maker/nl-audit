"""Testes da decisão do auditor de repos (tickets site-principal#4 e #5).

O seam é `avaliar()`: função pura que recebe o estado JÁ OBSERVADO e devolve o
veredito. A coleta via `gh` não é testada aqui de propósito — é I/O, e quem prova
que ela funciona é o teste negativo de ponta a ponta exigido pelo ticket.
"""
import sys, os, unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from audit_repos import Declaracao, Observado, avaliar, render, codigo_saida

GITIGNORE_OK = "node_modules/\n.env\n.env.*\n!.env.example\n"


def obs(alertas=True, branch="main", gitignore=GITIGNORE_OK,
        canonical=None, status_ar=None, ci_executado=True):
    """Estado observado de um repo. Cada campo vira None quando a coleta falhou.

    `canonical`: URL declarada no HTML, `""` quando o HTML foi lido e nao declara
    endereco nenhum, None quando nao deu para ler. `status_ar`: status HTTP do
    host como string, None quando o host nao respondeu (DNS, timeout, recusa).
    `ci_executado`: default True porque a maioria dos cenarios acima nao testa
    a invariante de CI - so as classes que a nomeiam devem variar isso.
    """
    return Observado(alertas=alertas, branch_default=branch, gitignore=gitignore,
                     canonical=canonical, status_ar=status_ar, ci_executado=ci_executado)


def dec(branch="main", serverless=True, site=False, tem_comando=True):
    return Declaracao(branch_producao=branch, serverless=serverless, site=site,
                      tem_comando=tem_comando)


def motivos(rel, invariante=None):
    return [v.motivo for v in rel.violacoes
            if invariante is None or v.invariante == invariante]


class AlertaDeDependencia(unittest.TestCase):

    def test_recorte_que_cita_repo_inexistente_grita(self):
        """O modo de falha que faria o auditor mentir verde: o recorte envelhece,
        o repo foi renomeado ou apagado, e ninguém observa nada sobre ele. Isso é
        violação do auditor, não silêncio."""
        r = avaliar(
            repos_da_conta={"site-principal": "main"},
            observado={"site-principal": obs()},
            recorte=["site-principal", "repo-que-nao-existe-mais"],
            declarado={"site-principal": dec(), "repo-que-nao-existe-mais": dec()},
            excecoes={},
        )
        self.assertEqual([v.repo for v in r.violacoes], ["repo-que-nao-existe-mais"])
        self.assertIn("nao existe na conta", r.violacoes[0].motivo)

    def test_excecao_declarada_dispensa_o_repo_mas_aparece_com_motivo(self):
        """Exceção nunca some em silêncio: ela sai do balde de violação e entra
        num balde visível, carregando o motivo escrito."""
        r = avaliar(
            repos_da_conta={"site-cliente-a": "master"},
            observado={"site-cliente-a": obs(alertas=False, branch="master")},
            recorte=["site-cliente-a"],
            declarado={"site-cliente-a": dec(branch="master", serverless=False)},
            excecoes={("site-cliente-a", "alertas"): "sem package.json, Dependabot e no-op hoje"},
        )
        self.assertEqual(r.violacoes, [])
        self.assertEqual(
            [(d.repo, d.motivo) for d in r.dispensados],
            [("site-cliente-a", "sem package.json, Dependabot e no-op hoje")],
        )

    def test_repo_do_recorte_sem_alerta_e_violacao_nomeada(self):
        r = avaliar(
            repos_da_conta={"site-principal": "main", "site-cliente-c": "main"},
            observado={"site-principal": obs(), "site-cliente-c": obs(alertas=False)},
            recorte=["site-principal", "site-cliente-c"],
            declarado={"site-principal": dec(), "site-cliente-c": dec()},
            excecoes={},
        )
        self.assertEqual([v.repo for v in r.violacoes], ["site-cliente-c"])


class BranchDeProducao(unittest.TestCase):
    """Invariante nova #1: o default branch é a branch que vai ao ar.

    O modo de falha real (site-principal, até 10/08): o default era `main` e a
    produção era `consolidacao` — clone e religação de Netlify caíam na branch errada.
    """

    def test_default_diferente_da_declarada_e_violacao_com_as_duas_branches(self):
        r = avaliar(
            repos_da_conta={"site-principal": "main"},
            observado={"site-principal": obs(branch="main")},
            recorte=["site-principal"],
            declarado={"site-principal": dec(branch="consolidacao")},
            excecoes={},
        )
        self.assertEqual([v.repo for v in r.violacoes], ["site-principal"])
        motivo = motivos(r, "branch")[0]
        self.assertIn("main", motivo)          # o observado
        self.assertIn("consolidacao", motivo)  # o declarado

    def test_master_declarado_nao_e_violacao(self):
        """A branch de producao e dado declarado, nao `main` hardcoded: dois repos
        do recorte usam `master` e estao certos assim."""
        r = avaliar(
            repos_da_conta={"site-cliente-a": "master", "painel-estatico": "master"},
            observado={"site-cliente-a": obs(branch="master"),
                       "painel-estatico": obs(branch="master")},
            recorte=["site-cliente-a", "painel-estatico"],
            declarado={"site-cliente-a": dec(branch="master"),
                       "painel-estatico": dec(branch="master")},
            excecoes={},
        )
        self.assertEqual(r.violacoes, [])

    def test_branch_nao_observada_vira_violacao(self):
        r = avaliar(
            repos_da_conta={"a": None},
            observado={"a": obs(branch=None)},
            recorte=["a"], declarado={"a": dec()}, excecoes={},
        )
        self.assertIn("nao observad", motivos(r, "branch")[0])

    def test_repo_do_recorte_sem_declaracao_e_violacao_nao_silencio(self):
        """Auditar branch sem saber qual é a de produção seria adivinhação. Quem
        entra no recorte sem declaração vira violação, não passa em branco."""
        r = avaliar(
            repos_da_conta={"repo-novo": "main"},
            observado={"repo-novo": obs()},
            recorte=["repo-novo"], declarado={}, excecoes={},
        )
        self.assertEqual([v.repo for v in r.violacoes], ["repo-novo"])
        self.assertIn("sem declaracao", r.violacoes[0].motivo)


class GitignoreDeSegredo(unittest.TestCase):
    """Invariante nova #2: repo com função serverless ignora arquivo de segredo.

    Função serverless é onde a credencial mora (Airtable, Twilio, Supabase); o
    modo de falha é um `.env.local` entrar num commit distraído.
    """

    def test_serverless_sem_cobertura_e_violacao(self):
        r = avaliar(
            repos_da_conta={"painel-estatico": "master"},
            observado={"painel-estatico": obs(branch="master", gitignore=".netlify\n")},
            recorte=["painel-estatico"],
            declarado={"painel-estatico": dec(branch="master", serverless=True)},
            excecoes={},
        )
        self.assertEqual([v.repo for v in r.violacoes], ["painel-estatico"])
        self.assertIn(".env", motivos(r, "gitignore")[0])

    def test_cobertura_parcial_ainda_e_violacao(self):
        """`.env` sozinho nao cobre `.env.local` nem `.env.production`, que sao
        exatamente os arquivos que o Netlify CLI e o Vite criam na maquina."""
        r = avaliar(
            repos_da_conta={"automacao-interna": "main"},
            observado={"automacao-interna": obs(gitignore=".env\nnode_modules/\n")},
            recorte=["automacao-interna"], declarado={"automacao-interna": dec()}, excecoes={},
        )
        self.assertIn(".env.*", motivos(r, "gitignore")[0])

    def test_comentario_nao_cobre_nada(self):
        r = avaliar(
            repos_da_conta={"a": "main"},
            observado={"a": obs(gitignore="# .env\n# .env.*\n")},
            recorte=["a"], declarado={"a": dec()}, excecoes={},
        )
        self.assertEqual([v.invariante for v in r.violacoes], ["gitignore"])

    def test_repo_sem_serverless_nao_e_cobrado(self):
        """A invariante nao se aplica onde nao ha credencial rodando; e o relatorio
        diz `n/a` em vez de fingir que passou."""
        r = avaliar(
            repos_da_conta={"site-cliente-c": "main"},
            observado={"site-cliente-c": obs(gitignore="node_modules/\n")},
            recorte=["site-cliente-c"],
            declarado={"site-cliente-c": dec(serverless=False)},
            excecoes={},
        )
        self.assertEqual(r.violacoes, [])
        self.assertIn("n/a", render(r))

    def test_gitignore_nao_observado_vira_violacao(self):
        r = avaliar(
            repos_da_conta={"a": "main"},
            observado={"a": obs(gitignore=None)},
            recorte=["a"], declarado={"a": dec()}, excecoes={},
        )
        self.assertIn("nao observad", motivos(r, "gitignore")[0])

    def test_repo_sem_gitignore_e_violacao_observada_nao_coleta_falha(self):
        r = avaliar(
            repos_da_conta={"a": "main"},
            observado={"a": obs(gitignore="")},
            recorte=["a"], declarado={"a": dec()}, excecoes={},
        )
        self.assertEqual([v.invariante for v in r.violacoes], ["gitignore"])
        self.assertNotIn("nao observad", motivos(r, "gitignore")[0])


class NaoObservadoNaoEConformidade(unittest.TestCase):
    """O modo de falha que mata auditor: a coleta falha e o verde continua verde."""

    def test_estado_nao_observado_vira_violacao(self):
        r = avaliar(
            repos_da_conta={"site-principal": "main"},
            observado={"site-principal": obs(alertas=None)},  # gh nao respondeu 204 nem 404
            recorte=["site-principal"],
            declarado={"site-principal": dec()},
            excecoes={},
        )
        self.assertEqual([v.repo for v in r.violacoes], ["site-principal"])
        self.assertIn("nao observad", motivos(r, "alertas")[0])

    def test_motivo_separa_desligado_de_nao_observado(self):
        r = avaliar({"a": "main", "b": "main"},
                    {"a": obs(alertas=False), "b": obs(alertas=None)},
                    ["a", "b"], {"a": dec(), "b": dec()}, {})
        self.assertNotEqual(motivos(r, "alertas")[0], motivos(r, "alertas")[1])

    def test_excecao_dispensa_alerta_desligado_mas_nao_cobre_coleta_falha(self):
        r = avaliar({"a": "main"}, {"a": obs(alertas=None)}, ["a"], {"a": dec()},
                    {("a", "alertas"): "repo parado, sem package.json"})
        self.assertEqual(r.dispensados, [])
        self.assertEqual([v.repo for v in r.violacoes], ["a"])

    def test_repo_do_recorte_que_o_gh_nao_devolveu_nao_passa_em_branco(self):
        """Sem entrada em `observado`, tudo e desconhecido — e desconhecido grita."""
        r = avaliar({"a": "main"}, {}, ["a"], {"a": dec()}, {})
        self.assertEqual(sorted(v.invariante for v in r.violacoes),
                         ["alertas", "branch", "ci", "gitignore"])


class ExcecaoEPorInvariante(unittest.TestCase):
    """Exceção dispensa UM estado aceito, não abre o repo inteiro."""

    def test_excecao_de_alertas_nao_dispensa_gitignore(self):
        r = avaliar(
            repos_da_conta={"a": "main"},
            observado={"a": obs(alertas=False, gitignore=".netlify\n")},
            recorte=["a"], declarado={"a": dec()},
            excecoes={("a", "alertas"): "motivo escrito"},
        )
        self.assertEqual([v.invariante for v in r.violacoes], ["gitignore"])
        self.assertEqual([d.repo for d in r.dispensados], ["a"])

    def test_repo_pode_violar_duas_invariantes_e_as_duas_aparecem(self):
        """Uma violação não engole a outra: o relatório mostra as duas, com nome."""
        r = avaliar(
            repos_da_conta={"a": "main"},
            observado={"a": obs(branch="main", gitignore=".netlify\n")},
            recorte=["a"], declarado={"a": dec(branch="producao")}, excecoes={},
        )
        self.assertEqual(sorted(v.invariante for v in r.violacoes),
                         ["branch", "gitignore"])


class EnderecoNoAr(unittest.TestCase):
    """Invariante 4 (site-principal#6): "existe no git" nao é "está publicado".

    Só é cobrada de repo declarado `site=True`. O endereço canônico sai do HTML
    (observado, não presumido) e é conferido contra a resposta real do host.
    """

    def test_site_com_canonical_respondendo_200_nao_e_violacao(self):
        r = avaliar(
            repos_da_conta={"site-principal": "consolidacao"},
            observado={"site-principal": obs(branch="consolidacao",
                                             canonical="https://example.org/",
                                             status_ar="200")},
            recorte=["site-principal"],
            declarado={"site-principal": dec(branch="consolidacao", site=True)},
            excecoes={},
        )
        self.assertEqual(r.violacoes, [])

    def test_canonical_apontando_para_host_morto_e_violacao_com_status_e_url(self):
        """O defeito que passou despercebido até a auditoria manual: o repo existe,
        o HTML declara um endereço, e o endereço não é site nenhum."""
        r = avaliar(
            repos_da_conta={"site-cliente-c": "main"},
            observado={"site-cliente-c": obs(canonical="https://cliente-c.netlify.app/",
                                          status_ar="404")},
            recorte=["site-cliente-c"],
            declarado={"site-cliente-c": dec(site=True)},
            excecoes={},
        )
        self.assertEqual([v.invariante for v in r.violacoes], ["ar"])
        self.assertIn("404", r.violacoes[0].motivo)
        self.assertIn("https://cliente-c.netlify.app/", r.violacoes[0].motivo)

    def test_repo_que_nao_e_site_nao_e_cobrado(self):
        """`painel-interno` roda só local: cobrar endereço no ar dele seria falso positivo."""
        r = avaliar({"painel-interno": "main"}, {"painel-interno": obs()}, ["painel-interno"],
                    {"painel-interno": dec(site=False)}, {})
        self.assertEqual([v.invariante for v in r.violacoes], [])

    def test_site_sem_canonical_no_html_aparece_nomeado(self):
        """Repo de site sem projeto de hospedagem: não há endereço declarado para
        conferir, e isso é violação explícita, não silêncio."""
        r = avaliar({"site-cliente-c": "main"}, {"site-cliente-c": obs(canonical="")},
                    ["site-cliente-c"], {"site-cliente-c": dec(site=True)}, {})
        self.assertEqual([v.invariante for v in r.violacoes], ["ar"])
        self.assertIn("sem endereco canonico", r.violacoes[0].motivo)

    def test_401_intencional_declarado_como_excecao_nao_vira_alarme(self):
        r = avaliar(
            repos_da_conta={"site-cliente-a": "master"},
            observado={"site-cliente-a": obs(branch="master", gitignore=None,
                                            canonical="https://cliente-a.netlify.app/",
                                            status_ar="401")},
            recorte=["site-cliente-a"],
            declarado={"site-cliente-a": dec(branch="master", serverless=False, site=True)},
            excecoes={("site-cliente-a", "ar"): "401 e protecao de acesso posta pelo dono"},
        )
        self.assertEqual(r.violacoes, [])
        self.assertEqual([(d.invariante, d.motivo) for d in r.dispensados],
                         [("ar", "401 e protecao de acesso posta pelo dono")])

    def test_host_que_nao_respondeu_e_violacao_e_excecao_nao_cobre(self):
        """"Não sei" nunca conta como "está ok": DNS que não resolve, timeout ou
        conexão recusada é violação com motivo próprio, mesmo com exceção escrita."""
        r = avaliar({"a": "main"},
                    {"a": obs(canonical="https://cliente-c.example/", status_ar=None)},
                    ["a"], {"a": dec(site=True)},
                    {("a", "ar"): "protecao de acesso intencional"})
        self.assertEqual(r.dispensados, [])
        self.assertEqual([v.invariante for v in r.violacoes], ["ar"])
        self.assertIn("nao respondeu", r.violacoes[0].motivo)

    def test_canonical_nao_observado_vira_violacao(self):
        """O gh não devolveu o HTML: desconhecido grita, não passa em branco."""
        r = avaliar({"a": "main"}, {"a": obs(canonical=None)}, ["a"],
                    {"a": dec(site=True)}, {})
        self.assertEqual([v.invariante for v in r.violacoes], ["ar"])
        self.assertIn("nao observad", r.violacoes[0].motivo)

    def test_site_parado_sem_canonical_pode_ser_dispensado_com_motivo(self):
        """Decisão 8 do grill: parado sem previsão de deploy não é acidente."""
        r = avaliar({"site-cliente-c": "main"}, {"site-cliente-c": obs(canonical="")},
                    ["site-cliente-c"], {"site-cliente-c": dec(site=True)},
                    {("site-cliente-c", "ar"): "parado sem previsao de deploy (decisao 8)"})
        self.assertEqual(r.violacoes, [])
        self.assertEqual([d.repo for d in r.dispensados], ["site-cliente-c"])

    def test_motivo_separa_host_morto_de_host_que_nao_respondeu(self):
        r = avaliar({"a": "main", "b": "main"},
                    {"a": obs(canonical="https://x.netlify.app/", status_ar="404"),
                     "b": obs(canonical="https://y.is/", status_ar=None)},
                    ["a", "b"], {"a": dec(site=True), "b": dec(site=True)}, {})
        self.assertNotEqual(motivos(r, "ar")[0], motivos(r, "ar")[1])

    def test_ar_nao_engole_as_outras_invariantes_do_mesmo_repo(self):
        r = avaliar({"a": "main"},
                    {"a": obs(alertas=False, canonical="https://x.is/", status_ar="404")},
                    ["a"], {"a": dec(site=True, serverless=False)}, {})
        self.assertEqual(sorted(v.invariante for v in r.violacoes), ["alertas", "ar"])


class ExecucaoDeCI(unittest.TestCase):
    """Invariante nova #5 (site-principal#8): repo com comando de build/teste tem
    workflow que EXECUTOU - existir o arquivo `.yml` nao prova nada, so a API de
    Actions prova.
    """

    def test_arquivo_de_workflow_sem_execucao_e_violacao_nao_arquivo(self):
        """O modo de falha que o ticket pede pra distinguir: workflow commitado,
        zero corrida registrada na conta."""
        r = avaliar(
            repos_da_conta={"painel-interno": "main"},
            observado={"painel-interno": obs(ci_executado=False)},
            recorte=["painel-interno"], declarado={"painel-interno": dec()}, excecoes={},
        )
        self.assertEqual([v.invariante for v in r.violacoes], ["ci"])
        self.assertIn("nenhuma execucao", motivos(r, "ci")[0])

    def test_execucao_registrada_nao_e_violacao(self):
        r = avaliar(
            repos_da_conta={"painel-interno": "main"},
            observado={"painel-interno": obs(ci_executado=True)},
            recorte=["painel-interno"], declarado={"painel-interno": dec()}, excecoes={},
        )
        self.assertEqual(r.violacoes, [])

    def test_execucao_nao_observada_e_violacao_nao_coleta_falha_dispensada(self):
        r = avaliar(
            repos_da_conta={"a": "main"},
            observado={"a": obs(ci_executado=None)},
            recorte=["a"], declarado={"a": dec()},
            excecoes={("a", "ci"): "tentativa de dispensar coleta que falhou"},
        )
        self.assertEqual(r.dispensados, [])
        self.assertIn("nao respondeu", motivos(r, "ci")[0])

    def test_repo_sem_comando_sem_excecao_aparece_nomeado(self):
        """Excecao e dado, nao presuncao: repo sem comando sem entrada em EXCECOES
        vira violacao visivel, nao passa em branco por "nao se aplica"."""
        r = avaliar(
            repos_da_conta={"painel-estatico": "master"},
            observado={"painel-estatico": obs(branch="master")},
            recorte=["painel-estatico"],
            declarado={"painel-estatico": dec(branch="master", tem_comando=False)},
            excecoes={},
        )
        self.assertEqual([v.invariante for v in r.violacoes], ["ci"])

    def test_repo_sem_comando_com_excecao_declarada_e_dispensado(self):
        r = avaliar(
            repos_da_conta={"automacao-interna": "main"},
            observado={"automacao-interna": obs()},
            recorte=["automacao-interna"],
            declarado={"automacao-interna": dec(tem_comando=False)},
            excecoes={("automacao-interna", "ci"): "package.json sem secao scripts"},
        )
        self.assertEqual(r.violacoes, [])
        self.assertEqual([(d.repo, d.invariante) for d in r.dispensados],
                         [("automacao-interna", "ci")])

    def test_relatorio_mostra_n_a_para_repo_sem_comando(self):
        r = avaliar(
            repos_da_conta={"automacao-interna": "main"},
            observado={"automacao-interna": obs()},
            recorte=["automacao-interna"],
            declarado={"automacao-interna": dec(tem_comando=False)},
            excecoes={("automacao-interna", "ci"): "package.json sem secao scripts"},
        )
        self.assertIn("n/a (sem comando)", render(r))


class ContratoDeSaida(unittest.TestCase):
    """Mesmo contrato do check_exposure.py: legível por humano, exit != 0 em violação."""

    def cenario(self):
        return avaliar(
            repos_da_conta={"site-principal": "consolidacao", "site-cliente-c": "main",
                            "ai-job-search": "owner/perfil-setup"},
            observado={"site-principal": obs(branch="consolidacao",
                                             canonical="https://example.org/",
                                             status_ar="200"),
                       "site-cliente-c": obs(alertas=False),
                       "ai-job-search": obs(alertas=False)},
            recorte=["site-principal", "site-cliente-c"],
            declarado={"site-principal": dec(branch="consolidacao", site=True),
                       "site-cliente-c": dec()},
            excecoes={},
        )

    def test_saida_lista_todos_os_repos_da_conta_e_marca_o_recorte(self):
        texto = render(self.cenario())
        # repo fora do recorte aparece, mas não condena ninguém
        self.assertIn("ai-job-search", texto)
        self.assertIn("fora do recorte", texto)
        self.assertIn("site-cliente-c", texto)
        self.assertIn("RESULTADO:", texto)

    def test_saida_mostra_as_quatro_invariantes_por_repo(self):
        texto = render(self.cenario())
        self.assertIn("consolidacao", texto)  # branch observada, repo a repo
        for cabecalho in ("alertas", "branch", "gitignore", "canonical"):
            self.assertIn(cabecalho, texto)

    def test_saida_mostra_o_estado_do_ar_do_repo_de_site(self):
        texto = render(self.cenario())
        self.assertIn("200", texto)

    def test_repo_fora_do_recorte_nao_vira_violacao(self):
        self.assertEqual([v.repo for v in self.cenario().violacoes], ["site-cliente-c"])

    def test_exit_1_com_violacao_e_0_sem(self):
        self.assertEqual(codigo_saida(self.cenario()), 1)
        limpo = avaliar({"a": "main"}, {"a": obs()}, ["a"], {"a": dec()}, {})
        self.assertEqual(codigo_saida(limpo), 0)


if __name__ == "__main__":
    unittest.main()
