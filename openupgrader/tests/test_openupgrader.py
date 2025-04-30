import base64

from odoo.modules import get_module_resource
from odoo.tests.common import SavepointCase


class Openupgrader(SavepointCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.version_obj = cls.env["odoo.version"]
        cls.from_version = "12.0"
        cls.middle_version = "13.0"
        cls.to_version = "14.0"
        cls.future_version = "15.0"
        for version in [
            cls.from_version,
            cls.middle_version,
            cls.to_version,
            cls.future_version,
        ]:
            version_id = cls.version_obj.search(
                [
                    ("name", "=", version),
                ]
            )
            if not version_id:
                cls.version_obj.create(
                    {
                        "name": version,
                        "python_version": "3.8.16"
                        if version in ["14.0", "15.0"]
                        else "3.7.16",
                    }
                )
        cls.from_version_id = cls.version_obj.search(
            [
                ("name", "=", cls.from_version),
            ]
        )
        cls.middle_version_id = cls.version_obj.search(
            [
                ("name", "=", cls.middle_version),
            ]
        )
        cls.to_version_id = cls.version_obj.search(
            [
                ("name", "=", cls.to_version),
            ]
        )
        cls.future_version_id = cls.version_obj.search(
            [
                ("name", "=", cls.future_version),
            ]
        )
        for version in [cls.from_version, cls.middle_version, cls.to_version]:
            openupgrader_config = cls.env["openupgrader.config"].search(
                [
                    ("odoo_version_id.name", "=", version),
                ]
            )
            if not openupgrader_config:
                version_id = cls.version_obj.search(
                    [
                        ("name", "=", version),
                    ]
                )
                openupgrader_config = cls.env["openupgrader.config"].create(
                    {
                        "odoo_version_id": version_id.id,
                    }
                )
            config_file_path = get_module_resource(
                "openupgrader", "tests", "data", "openupgrader_config.yml"
            )
            with open(config_file_path, "rb") as file:
                config_file = base64.b64encode(file.read())
                file.close()
                openupgrader_config.config_file = config_file
                openupgrader_config.button_load_config()
            repos_file_path = get_module_resource(
                "openupgrader", "tests", "data", "openupgrader_repos.yml"
            )
            with open(repos_file_path, "rb") as file:
                repos_file = base64.b64encode(file.read())
                file.close()
                openupgrader_config.repos_file = repos_file
                openupgrader_config.button_load_repos()
        cls.openupgrader_migration = cls.env["openupgrader.migration"].create(
            {
                "from_version_id": cls.from_version_id.id,
                "to_version_id": cls.to_version_id.id,
                "openupgrade_repo": "git@github.com:efatto/OpenUpgrade.git",
                "odoo_repo": "git@github.com:OCA/OCB.git",
            }
        )
        cls.from_version_id.button_create_venv()
        cls.middle_version_id.button_create_venv()
        cls.to_version_id.button_create_venv()

    def test_openupgrader(self):
        self.openupgrader_migration.button_restore_db()
        self.assertEqual(self.openupgrader_migration.state, "db_restored")
        self.assertEqual(
            self.openupgrader_migration.current_version_id,
            self.from_version_id,
        )
        self.assertEqual(
            self.openupgrader_migration.next_version_id,
            self.middle_version_id,
        )
        self.openupgrader_migration.button_update_current_version()
        self.openupgrader_migration.button_ready_for_migration()
        self.assertEqual(self.openupgrader_migration.state, "ready_for_migration")
        self.openupgrader_migration.button_do_migration()
        self.assertEqual(self.openupgrader_migration.state, "done")
        self.assertEqual(
            self.openupgrader_migration.current_version_id,
            self.middle_version_id,
        )
        self.assertEqual(
            self.openupgrader_migration.next_version_id,
            self.to_version_id,
        )
        self.openupgrader_migration.button_update_current_version()
        self.openupgrader_migration.button_ready_for_migration()
        self.assertEqual(self.openupgrader_migration.state, "ready_for_migration")
        self.openupgrader_migration.button_do_migration()
        self.assertEqual(self.openupgrader_migration.state, "done")
        self.assertEqual(
            self.openupgrader_migration.current_version_id,
            self.to_version_id,
        )
        self.assertEqual(
            self.openupgrader_migration.next_version_id,
            self.future_version_id,
        )
