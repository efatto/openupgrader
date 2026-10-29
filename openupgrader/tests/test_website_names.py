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
