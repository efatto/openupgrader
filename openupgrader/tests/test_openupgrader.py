import base64

from odoo.tests.common import Form, SavepointCase

from odoo.modules import get_module_resource


class Openupgrader(SavepointCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.base_version = "12.0"
        for version in ["12.0", "13.0", "14.0"]:
            if not cls.env["odoo.version"].search([
                ("name", "=", version),
            ]):
                version = cls.env["odoo.version"].create({
                    "name": version,
                    "python_version": "3.8.16" if version == "14.0" else "3.7.16",
                })
                version.button_create_venv()
        cls.openupgrader_config = cls.env["openupgrader.config"].search([
            ("odoo_version_id.name", "=", cls.base_version),
        ])
        if not cls.openupgrader_config:
            cls.openupgrader_config = cls.env["openupgrader.config"].create({
                "odoo_version_id": cls.env["odoo.version"].search([
                    ("name", "=", cls.base_version),
                ]).id,
            })
        config_file_path = get_module_resource(
            "openupgrader", "tests", "data", "openupgrader_config.yml"
        )
        with open(config_file_path, "rb") as file:
            config_file = base64.b64encode(file.read())
            file.close()
            cls.openupgrader_config.config_file = config_file
            cls.openupgrader_config.button_load_config()
        repos_file_path = get_module_resource(
            "openupgrader", "tests", "data", "openupgrader_repos.yml"
        )
        with open(repos_file_path, "rb") as file:
            repos_file = base64.b64encode(file.read())
            file.close()
            cls.openupgrader_config.repos_file = repos_file
            cls.openupgrader_config.button_load_repos()

    def test_openupgrader(self):

        pass
