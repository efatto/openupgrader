import base64
import time

from odoo.modules import get_module_resource
from odoo.release import version_info
from odoo.tests.common import Form, SavepointCase, tagged


@tagged("post_install", "-at_install")
class OpenUpgrader(SavepointCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.config_obj = cls.env["openupgrader.config"]
        cls.migration_obj = cls.env["openupgrader.migration"]
        cls.from_version = ".".join(str(v) for v in version_info[:2])
        cls.middle_version = str(float(cls.from_version) + 1)
        cls.middle_version1 = str(float(cls.from_version) + 2)
        cls.middle_version2 = str(float(cls.from_version) + 3)
        cls.to_version = str(float(cls.from_version) + 4)
        versions = {
            "from_version": cls.from_version,
            "middle_version": cls.middle_version,
            "middle_version1": cls.middle_version1,
            "middle_version2": cls.middle_version2,
            "to_version": cls.to_version,
        }
        cls.from_config_id = cls.config_obj
        cls.middle_config_id = cls.config_obj
        cls.middle1_config_id = cls.config_obj
        cls.middle2_config_id = cls.config_obj
        cls.to_config_id = cls.config_obj
        cls.openupgrader_migration = cls.migration_obj.search([])
        if not cls.openupgrader_migration:
            migration_form = Form(cls.migration_obj)
            migration_form.openupgrade_repo = (
                "https://github.com/efatto/OpenUpgrade.git"
            )
            migration_form.pg_password_var = "$PGPASSWORD"  # PGPASSWORD=odoo
            migration_form.pg_host = "$PGHOST"  # PGHOST=postgres
            migration_form.pg_user = "$PGUSER"  # PGUSER=odoo
            cls.openupgrader_migration = migration_form.save()
        else:
            cls.openupgrader_migration.button_draft()
        config_file_path = get_module_resource(
            "openupgrader", "tests", "data", "openupgrader_config.yml"
        )
        with open(config_file_path, "rb") as config_file_reader:
            cls.openupgrader_migration.config_file = base64.b64encode(
                config_file_reader.read()
            )
        for version in versions:
            openupgrader_config = cls.config_obj.search(
                [
                    ("name", "=", versions[version]),
                ]
            )
            if not openupgrader_config:
                openupgrader_config_form = Form(cls.config_obj)
                openupgrader_config_form.name = versions[version]
                openupgrader_config_form.openupgrader_migration_id = (
                    cls.openupgrader_migration
                )
                openupgrader_config = openupgrader_config_form.save()
                setattr(cls, f"{version}_id", openupgrader_config)
                openupgrader_config.button_recreate_venv()
        cls.from_config_id = cls.config_obj.search(
            [
                ("name", "=", cls.from_version),
            ]
        )
        cls.middle_config_id = cls.config_obj.search(
            [
                ("name", "=", cls.middle_version),
            ]
        )
        cls.middle_config1_id = cls.config_obj.search(
            [
                ("name", "=", cls.middle_version1),
            ]
        )
        cls.middle_config2_id = cls.config_obj.search(
            [
                ("name", "=", cls.middle_version2),
            ]
        )
        cls.to_config_id = cls.config_obj.search(
            [
                ("name", "=", cls.to_version),
            ]
        )
        cls.openupgrader_migration.from_version_id = cls.from_config_id
        cls.openupgrader_migration.to_version_id = cls.to_config_id

    def test_00_openupgrader_manual(self):
        openupgrader_migration = self.openupgrader_migration
        openupgrader_migration.button_clean_logs()
        self.assertEqual(
            self.openupgrader_migration.to_version_id,
            self.to_config_id,
        )
        self.assertEqual(
            self.openupgrader_migration.from_version_id,
            self.from_config_id,
        )
        openupgrader_migration.button_stop_odoo()
        openupgrader_migration.button_restore()
        self.assertEqual(openupgrader_migration.state, "restored")
        self.assertEqual(
            openupgrader_migration.current_version_id,
            self.from_config_id,
        )
        self.assertEqual(
            openupgrader_migration.next_version_id,
            self.middle_config_id,
        )
        for _i, config_id in enumerate(
            [self.middle_config_id, self.middle_config_id, self.middle_config2_id]
        ):
            openupgrader_migration.button_update_current_version()
            openupgrader_migration.button_update_current_version()
            openupgrader_migration.button_prepare_for_migration()
            self.assertEqual(openupgrader_migration.state, "ready_for_migration")
            openupgrader_migration.button_do_migration()
            # wait until migration is stopped with threading
            while openupgrader_migration._get_odoo_migrated_state() == "running":
                time.sleep(10)
            openupgrader_migration.button_do_migration()
            self.assertEqual(openupgrader_migration.state, "migrated")
            self.assertEqual(
                openupgrader_migration.current_version_id,
                config_id,
            )
            # self.assertEqual(
            #     openupgrader_migration.next_version_id,
            #     self.to_config_id,
            # )
            openupgrader_migration.button_prepare_for_migration()
            self.assertEqual(openupgrader_migration.state, "ready_for_migration")
            openupgrader_migration.button_do_migration()
            # wait until migration is stopped with threading
            while openupgrader_migration._get_odoo_migrated_state() == "running":
                time.sleep(10)
        self.assertEqual(
            openupgrader_migration.next_version_id,
            self.to_config_id,
        )
        openupgrader_migration.button_do_migration()
        self.assertEqual(openupgrader_migration.is_migration_done, True)
        self.assertEqual(
            openupgrader_migration.current_version_id,
            self.to_config_id,
        )

    def test_01_openupgrader_auto(self):
        openupgrader_migration = self.openupgrader_migration
        openupgrader_migration.button_clean_logs()
        self.assertEqual(
            self.openupgrader_migration.to_version_id,
            self.to_config_id,
        )
        self.assertEqual(
            self.openupgrader_migration.from_version_id,
            self.from_config_id,
        )
        openupgrader_migration.button_stop_odoo()
        openupgrader_migration.button_do_all()
        self.assertEqual(openupgrader_migration.is_migration_done, True)
