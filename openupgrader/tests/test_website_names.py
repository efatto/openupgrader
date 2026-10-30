from odoo.tests.common import SavepointCase, tagged


@tagged("post_install", "-at_install")
class TestWebsiteNames(SavepointCase):
    def test_extract_website_hostname(self):
        Migration = self.env["openupgrader.migration"]
        self.assertEqual(
            Migration._extract_website_hostname("https://odoo.mysite.it:8069/web"),
            "odoo.mysite.it",
        )
        self.assertEqual(
            Migration._extract_website_hostname("//odoo.mysite.it/x"),
            "odoo.mysite.it",
        )
        self.assertEqual(
            Migration._extract_website_hostname("odoo.mysite.it"),
            "odoo.mysite.it",
        )
        self.assertFalse(Migration._extract_website_hostname(""))

    def test_build_new_website_name(self):
        Migration = self.env["openupgrader.migration"]
        self.assertEqual(
            Migration._build_new_website_name("odoo.mysite.it"),
            "odoomigr.mysite.it",
        )
        self.assertEqual(
            Migration._build_new_website_name("mysite.it"),
            "mysitemigr.it",
        )
        self.assertEqual(
            Migration._build_new_website_name("localhost"),
            "localhostmigr",
        )
        self.assertFalse(Migration._build_new_website_name(""))

    def test_get_website_link_replacements(self):
        Migration = self.env["openupgrader.migration"]
        self.assertEqual(
            Migration._get_website_link_replacements(
                "odoo.mysite.it", "odoomigr.mysite.it"
            ),
            [
                (
                    "https://odoo.mysite.it",
                    "https://odoomigr.mysite.it",
                ),
                ("http://odoo.mysite.it", "http://odoomigr.mysite.it"),
                ("//odoo.mysite.it", "//odoomigr.mysite.it"),
            ],
        )

    def test_get_old_website_name_variants(self):
        Migration = self.env["openupgrader.migration"]
        self.assertEqual(
            Migration._get_old_website_name_variants("odootest.mysite.it"),
            ["odootest.mysite.it", "odoo.mysite.it"],
        )
        self.assertEqual(
            Migration._get_old_website_name_variants("test.mysite.it"),
            ["test.mysite.it", "mysite.it"],
        )
        self.assertEqual(
            Migration._get_old_website_name_variants("mysite.test.it"),
            ["mysite.test.it", "mysite.it"],
        )
        self.assertEqual(
            Migration._get_old_website_name_variants("odoo.mysite.it"),
            ["odoo.mysite.it"],
        )
        self.assertEqual(
            Migration._get_old_website_name_variants("OdooTest.Mysite.IT"),
            ["OdooTest.Mysite.IT", "Odoo.Mysite.IT"],
        )
        self.assertEqual(
            Migration._get_old_website_name_variants("  odootest.mysite.it  "),
            ["odootest.mysite.it", "odoo.mysite.it"],
        )
        self.assertEqual(Migration._get_old_website_name_variants(""), [])
        self.assertEqual(Migration._get_old_website_name_variants(False), [])

    def test_get_website_link_replacements_with_test_host(self):
        Migration = self.env["openupgrader.migration"]
        replacements = Migration._get_website_link_replacements(
            "odootest.mysite.it", "odoomigr.mysite.it"
        )
        self.assertIn(
            (
                "https://odootest.mysite.it",
                "https://odoomigr.mysite.it",
            ),
            replacements,
        )
        self.assertIn(
            ("https://odoo.mysite.it", "https://odoomigr.mysite.it"),
            replacements,
        )
        self.assertIn(
            ("//odoo.mysite.it", "//odoomigr.mysite.it"),
            replacements,
        )
