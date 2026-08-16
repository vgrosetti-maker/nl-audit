"""Suíte do check_exposure.py — com os modos de falha injetados de propósito.

Detector sem teste negativo não prova nada. O bug que motivou este arquivo:
até 10/08/2026 o modo --all contava index.html e imagem como exposição, saía 1
e reprovaria todo deploy saudável. Os testes abaixo travam as duas pontas:
material interno acessível TEM que acusar, ativo público acessível NÃO pode.
"""
import hashlib
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from check_exposure import acessivel, classificar, suspeito, terceiro


def h(b):
    return hashlib.md5(b).hexdigest()


class TestSuspeito(unittest.TestCase):
    def test_material_interno_e_suspeito(self):
        for caminho in ("docs/AUDITORIA.md", "supabase/migrations.sql",
                        "tests/rls.test.mjs", "tools/review-shots/a.png",
                        "package.json", "netlify.toml", ".gitignore",
                        "CLAUDE.md", "backup.bak", "clientes.csv", ".env"):
            self.assertTrue(suspeito(caminho), caminho)

    def test_ativo_publico_nao_e_suspeito(self):
        for caminho in ("index.html", "blog.html", "assets/css/main.css",
                        "assets/js/app.js", "assets/logo/logo.svg",
                        "assets/r/hero.webp", "favicon.ico",
                        "assets/i18n/is.json", "assets/exemplo-spark.glb"):
            self.assertFalse(suspeito(caminho), caminho)

    def test_suspeito_e_por_pasta_de_topo_nao_por_substring(self):
        # `assets/docs-icon.svg` não é `docs/`; e `docs/` em qualquer profundidade
        # de topo conta.
        self.assertFalse(suspeito("assets/docs-icon.svg"))
        self.assertTrue(suspeito("docs/adr/0001.md"))


class TestAcessivel(unittest.TestCase):
    def setUp(self):
        self.home = b"<html>home</html>"
        self.erro = b"<html>404 bonito</html>"
        self.baselines = {h(self.home), h(self.erro)}

    def test_200_com_corpo_proprio_e_acessivel(self):
        self.assertTrue(acessivel(200, b"CREATE TABLE fichas...", self.baselines))

    def test_catch_all_que_devolve_a_home_nao_e_acessivel(self):
        # A armadilha nº 1: 200 em tudo. Corpo igual ao da home não é achado.
        self.assertFalse(acessivel(200, self.home, self.baselines))

    def test_pagina_de_erro_customizada_nao_e_acessivel(self):
        self.assertFalse(acessivel(200, self.erro, self.baselines))

    def test_404_401_e_corpo_vazio_nao_sao_acessiveis(self):
        self.assertFalse(acessivel(404, b"", self.baselines))
        self.assertFalse(acessivel(401, b"", self.baselines))
        self.assertFalse(acessivel(200, b"", self.baselines))


class TestClassificar(unittest.TestCase):
    def test_negativo_material_interno_acessivel_acusa(self):
        """Caso de origem, reinjetado: material interno servido junto do site."""
        expostos, terceiros, publicos = classificar([
            ("supabase/migrations.sql", 8100),
            ("docs/AUDITORIA.md", 4200),
            ("index.html", 12000),
        ])
        self.assertEqual([f for f, _ in expostos],
                         ["supabase/migrations.sql", "docs/AUDITORIA.md"])
        self.assertEqual([f for f, _ in publicos], ["index.html"])

    def test_positivo_site_inteiro_acessivel_nao_acusa_nada(self):
        """O falso positivo de 10/08: --all num site saudável tem que sair limpo."""
        site = [("index.html", 12000), ("blog.html", 9000),
                ("assets/css/main.css", 3000), ("assets/js/app.js", 5000),
                ("assets/r/hero.webp", 40000), ("sitemap.xml", 800),
                ("robots.txt", 60), ("404.html", 1000)]
        expostos, terceiros, publicos = classificar(site)
        self.assertEqual(expostos, [])
        self.assertEqual(len(publicos), len(site))

    def test_negativo_preview_de_terceiro_acessivel_acusa(self):
        """O vazamento real, reinjetado com nome ficticio: a previa feita para
        um cliente em potencial ficou 44 dias em HTTP 200 e as tres verificacoes
        automaticas passaram verdes, porque todas cacavam material NOSSO."""
        caso = "preview/oficina-exemplo-proposta-a1b2c3.html"
        expostos, terceiros, publicos = classificar([
            (caso, 9262), ("index.html", 12000)])
        self.assertEqual(expostos, [], "nao e interno nosso, nao pode cair nesse balde")
        self.assertEqual([f for f, _ in terceiros], [caso])
        self.assertEqual([f for f, _ in publicos], ["index.html"])

    def test_terceiro_e_por_pasta_de_topo_nao_por_substring(self):
        self.assertTrue(terceiro("preview/x.html"))
        self.assertFalse(terceiro("assets/preview-hero.webp"))
        self.assertFalse(terceiro("work/preview/menswear.html"))

    def test_nada_acessivel_nao_inventa_achado(self):
        self.assertEqual(classificar([]), ([], [], []))


if __name__ == "__main__":
    unittest.main()
