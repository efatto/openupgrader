import base64
import time

from odoo.modules import get_module_resource
from odoo.release import version_info
from odoo.tests.common import SingleTransactionCase, Form


class Openupgrader(SingleTransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.version_obj = cls.env["odoo.version"]
        cls.migration_obj = cls.env["openupgrader.migration"]
        cls.from_version = ".".join(str(v) for v in version_info[:2])
        cls.middle_version = str(float(cls.from_version) + 1)
        cls.to_version = str(float(cls.middle_version) + 1)
        cls.future_version = str(float(cls.to_version) + 1)
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
        for version_name in [cls.from_version, cls.middle_version, cls.to_version]:
            openupgrader_config = cls.env["openupgrader.config"].search(
                [
                    ("odoo_version_id.name", "=", version_name),
                ]
            )
            if not openupgrader_config:
                version_id = cls.version_obj.search(
                    [
                        ("name", "=", version_name),
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
        cls.openupgrader_migration = cls.migration_obj.search([])
        if not cls.openupgrader_migration:
            migration_form = Form(cls.migration_obj)
            migration_form.from_version_id = cls.from_version_id
            migration_form.to_version_id = cls.to_version_id
            migration_form.openupgrade_repo = "https://github.com/efatto/OpenUpgrade.git"
            migration_form.pg_password_var = "$PGPASSWORD"  # PGPASSWORD=odoo
            migration_form.pg_host = "$PGHOST"  # PGHOST=postgres
            migration_form.pg_user = "$PGUSER"  # PGUSER=odoo
            cls.openupgrader_migration = migration_form.save()
        for version in [cls.from_version_id, cls.middle_version_id, cls.to_version_id]:
            version.button_create_venv()

    def test_openupgrader(self):
        openupgrader_migration = self.openupgrader_migration
        openupgrader_migration.button_stop_odoo()
        openupgrader_migration.button_restore()
        self.assertEqual(openupgrader_migration.state, "restored")
        self.assertEqual(
            openupgrader_migration.current_version_id,
            self.from_version_id,
        )
        self.assertEqual(
            openupgrader_migration.next_version_id,
            self.middle_version_id,
        )
        openupgrader_migration.button_update_current_version()
        openupgrader_migration.button_prepare_for_migration()
        self.assertEqual(openupgrader_migration.state, "ready_for_migration")
        openupgrader_migration.button_do_migration()
        # wait until migration is stopped with threading
        while openupgrader_migration._get_odoo_migrated_state() == "running":
            time.sleep(10)
        self.assertEqual(openupgrader_migration.state, "done")
        self.assertEqual(
            openupgrader_migration.current_version_id,
            self.middle_version_id,
        )
        self.assertEqual(
            openupgrader_migration.next_version_id,
            self.to_version_id,
        )
        openupgrader_migration.button_prepare_for_migration()
        self.assertEqual(openupgrader_migration.state, "ready_for_migration")
        openupgrader_migration.button_do_migration()
        # wait until migration is stopped with threading
        while openupgrader_migration._get_odoo_migrated_state() == "running":
            time.sleep(10)
        self.assertEqual(openupgrader_migration.state, "done")
        self.assertEqual(
            openupgrader_migration.current_version_id,
            self.to_version_id,
        )
        self.assertEqual(
            openupgrader_migration.next_version_id,
            self.future_version_id,
        )
