import base64

from odoo.tests.common import SavepointCase, tagged

CONFIG_YML = """
"18.0":
  - python_version: "3.12.11"
  - python_commands:
      - code: |
          env["res.company"].search([])
        before: false
      - before: |
          env["res.users"].search([])
      - after: |
          env["res.partner"].search([])
      - |
        env["ir.ui.view"].search([])
      - code: |
          env["ir.model"].search([])
        when: before
      - invalid: "no code here"
"""


@tagged("post_install", "-at_install")
class TestPythonCommands(SavepointCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # openupgrader.config.name is unique: reuse the record created by the
        # other tests when it is already there.
        cls.config = cls.env["openupgrader.config"].search(
            [("name", "=", "18.0")], limit=1
        )
        if not cls.config:
            cls.config = cls.env["openupgrader.config"].create(
                {
                    "name": "18.0",
                    "openupgrader_migration_id": cls.env["openupgrader.migration"]
                    .create({})
                    .id,
                    "python_version": "3.12.11",
                }
            )
        cls.migration = cls.config.openupgrader_migration_id
        cls.migration.config_file_path = False
        cls.migration.config_file = base64.b64encode(CONFIG_YML.encode())

    def _load(self):
        self.config.action_load_config()
        return self.config.python_command_ids.sorted("sequence")

    def test_parse_python_command(self):
        parse = self.env["openupgrader.config"]._parse_python_command
        self.assertEqual(
            parse("env['res.company'].search([])"),
            ("env['res.company'].search([])", False),
        )
        self.assertEqual(parse({"before": "code"}), ("code", True))
        self.assertEqual(parse({"after": "code"}), ("code", False))
        self.assertEqual(parse({"code": "code", "before": True}), ("code", True))
        self.assertEqual(parse({"code": "code"}), ("code", False))
        self.assertEqual(parse({"code": "code", "when": "before"}), ("code", True))
        self.assertEqual(parse({"code": "code", "when": "after"}), ("code", False))
        self.assertIsNone(parse({"invalid": "code"}))
        self.assertIsNone(parse(""))
        self.assertIsNone(parse(None))

    def test_load_python_commands(self):
        commands = self._load()
        self.assertEqual(len(commands), 5)
        self.assertEqual(
            commands.mapped("before_migration"),
            [False, True, False, False, True],
        )
        self.assertIn('env["res.company"].search([])', commands[0].name)
        self.assertIn('env["res.users"].search([])', commands[1].name)
        self.assertIn('env["res.partner"].search([])', commands[2].name)
        self.assertIn('env["ir.ui.view"].search([])', commands[3].name)
        self.assertIn('env["ir.model"].search([])', commands[4].name)
        self.assertFalse(any("invalid" in name for name in commands.mapped("name")))
        self.assertEqual(len(self.config._get_python_commands(True)), 2)
        self.assertEqual(len(self.config._get_python_commands(False)), 3)

    def test_reload_python_commands_is_idempotent(self):
        names = self._load().mapped("name")
        self.assertEqual(self._load().mapped("name"), names)
        self.assertEqual(len(self.config.python_command_ids), len(names))
