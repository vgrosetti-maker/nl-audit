"""Teste negativo do classificador de desfechos (fusao nl-audit + nl-audit-interno,
veredito do conselho 10/09/2026, ARQUITETURA-ALVO-20260910.md secao 10 passo 2).

O caso quebrado de proposito: um auditor que sai com codigo 2 (segredo ausente)
NAO PODE cair no balde 'achado-real'. Se cair, o gate volta a reprovar o alvo por
um problema que e do proprio auditor - exatamente o defeito que motivou a fusao
(nl-audit-interno falhou 5 rodadas seguidas por secret ausente, sem nunca dizer
que nao era o alvo que estava quebrado).
"""
import sys, os, unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from classificar_desfecho import classificar


class TestClassificarDesfecho(unittest.TestCase):

    def test_limpo(self):
        self.assertEqual(classificar(0), "limpo")

    def test_achado_real(self):
        self.assertEqual(classificar(1), "achado-real")

    def test_falha_de_credencial_nunca_e_achado_real(self):
        # O caso quebrado de proposito: codigo 2 e a assinatura exata dos 10/10
        # runs vermelhos de nl-audit-interno (secret GH_AUDIT_PAT ausente).
        # Se isto virasse 'achado-real' o gate mentiria que o ALVO esta quebrado.
        desfecho = classificar(2)
        self.assertEqual(desfecho, "falha-de-credencial")
        self.assertNotEqual(desfecho, "achado-real")

    def test_codigo_desconhecido_e_falha_do_auditor_nao_achado_real(self):
        # Timeout, segfault, dependencia faltando: qualquer coisa que
        # audit_repos.py nem chegou a classificar sozinho.
        for codigo in (3, 127, 139, -1):
            with self.subTest(codigo=codigo):
                desfecho = classificar(codigo)
                self.assertEqual(desfecho, "falha-do-auditor")
                self.assertNotEqual(desfecho, "achado-real")


if __name__ == "__main__":
    unittest.main()
