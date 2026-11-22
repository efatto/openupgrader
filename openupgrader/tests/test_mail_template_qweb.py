import os
import tempfile
from unittest.mock import Mock, patch

from odoo.exceptions import UserError
from odoo.tests.common import SavepointCase, tagged

from .. import mail_template_qweb as fix_module

JINJA_BODY = (
    '<p>Buongiorno <t t-out="object.name"></t>,</p>'
    "<p>per <t t-out=\"ctx.get('nome_animali') or "
    "object.child_ids|selectattr('type','equalto','pet')"
    "|map(attribute='name')|join(', ')\"></t>.</p>"
)
CONVERTED_BODY = (
    '<p>Buongiorno <t t-out="object.name"></t>,</p>'
    "<p>per <t t-out=\"ctx.get('nome_animali') or "
    "', '.join(object.child_ids.filtered(lambda _r: _r.type == 'pet')"
    ".mapped('name'))\"></t>.</p>"
)


@tagged("post_install", "-at_install")
class TestMailTemplateQweb(SavepointCase):
    """Conversion of the Jinja filters left in the mail templates."""

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

    def _template(self, name, body):
        return self.env["mail.template"].create(
            {
                "name": name,
                "model_id": self.env["ir.model"]._get("res.partner").id,
                "body_html": body,
            }
        )

    def test_convert_selectattr_chain(self):
        expression = (
            "ctx.get('nome_animali') or object.child_ids"
            "|selectattr('type','equalto','pet')|map(attribute='name')|join(', ')"
        )
        self.assertEqual(
            fix_module.convert_expr(expression),
            "ctx.get('nome_animali') or ', '.join("
            "object.child_ids.filtered(lambda _r: _r.type == 'pet').mapped('name'))",
        )

    def test_convert_keeps_the_markup_and_encodes_entities(self):
        body = (
            '<p style="margin:0px;">a <t t-out="object.child_ids|length &gt; 0">'
            "</t> b</p>"
        )
        new_body, changes = fix_module.convert_html(body)
        self.assertEqual(
            new_body,
            '<p style="margin:0px;">a <t t-out="len(object.child_ids) &gt; 0">'
            "</t> b</p>",
        )
        self.assertEqual(len(changes), 1)

    def test_convert_safe_becomes_t_raw(self):
        new_body, changes = fix_module.convert_html(
            "<p>${object.website_description|safe}</p>"
        )
        self.assertEqual(new_body, '<p><t t-raw="object.website_description"></t></p>')
        self.assertEqual(len(changes), 1)

    def test_convert_safe_in_t_out_becomes_t_raw(self):
        new_body, _changes = fix_module.convert_html(
            '<t t-out="object.website_description|safe"></t>'
        )
        self.assertEqual(new_body, '<t t-raw="object.website_description"></t>')

    def test_convert_list_filter(self):
        new_body, _changes = fix_module.convert_html(
            '<t t-if="object.child_ids|list|length &gt; 0"></t>'
        )
        self.assertEqual(
            new_body, '<t t-if="len([_i for _i in object.child_ids]) &gt; 0"></t>'
        )

    def test_unknown_filter_is_reported_with_its_expression(self):
        with self.assertRaises(fix_module.Unsupported) as error:
            fix_module.convert_html('<t t-out="object.name|myfilter"></t>')
        self.assertIn("myfilter", str(error.exception))
        self.assertIn("object.name|myfilter", str(error.exception))

    def test_convert_unknown_filter_is_unsupported(self):
        with self.assertRaises(fix_module.Unsupported):
            fix_module.convert_html('<t t-out="object.name|striptags"></t>')

    def test_convert_inline_placeholder_in_text(self):
        body = "<p>Gentile ${object.name},</p>"
        self.assertEqual(
            fix_module.convert_html(body),
            (
                '<p>Gentile <t t-out="object.name"></t>,</p>',
                [("${object.name}", '<t t-out="object.name"></t>')],
            ),
        )

    def test_convert_inline_placeholder_with_filter(self):
        new_body, changes = fix_module.convert_html("<p>${object.child_ids|length}</p>")
        self.assertEqual(new_body, '<p><t t-out="len(object.child_ids)"></t></p>')
        self.assertEqual(len(changes), 1)

    def test_convert_inline_placeholder_in_attribute_is_unsupported(self):
        with self.assertRaises(fix_module.Unsupported):
            fix_module.convert_html('<a href="/p/${object.id}">x</a>')

    def test_convert_leaves_plain_expressions_untouched(self):
        body = '<t t-out="object.name"></t>'
        self.assertEqual(fix_module.convert_html(body), (body, []))

    def test_fix_converts_and_keeps_the_original_body(self):
        template = self._template("QWeb fix test", JINJA_BODY)
        with tempfile.TemporaryDirectory() as dump_dir:
            fix_module.fix(self.env, dump_dir)
            self.assertEqual(template.body_html, CONVERTED_BODY)
            backup = [
                name
                for name in os.listdir(dump_dir)
                if name.startswith("mail_template_%s_body_html_" % template.id)
            ]
            self.assertEqual(len(backup), 1)
            with open(os.path.join(dump_dir, backup[0])) as original:
                self.assertEqual(original.read(), JINJA_BODY)
            self.assertTrue(os.path.exists(os.path.join(dump_dir, "index.json")))

    def test_fix_is_idempotent(self):
        template = self._template("QWeb fix idempotent", JINJA_BODY)
        fix_module.fix(self.env, None)
        self.assertEqual(template.body_html, CONVERTED_BODY)
        # the converted body has no Jinja filter left: nothing is reported again
        stats, lines = fix_module.fix(self.env, None)
        self.assertEqual(stats["converted"], 0)
        self.assertFalse([line for line in lines if " | %s | " % template.id in line])
        self.assertEqual(template.body_html, CONVERTED_BODY)

    def test_leftover_inline_template_is_converted(self):
        template = self._template(
            "Leftover inline template", "<p>Buongiorno ${object.name},</p>"
        )
        fix_module.fix(self.env, None)
        self.assertEqual(
            template.body_html, '<p>Buongiorno <t t-out="object.name"></t>,</p>'
        )

    def test_leftover_inline_template_in_attribute_is_reported(self):
        body = '<p><a href="/p/${object.id}">x</a></p>'
        template = self._template("Leftover in attribute", body)
        _stats, lines = fix_module.fix(self.env, None)
        self.assertEqual(template.body_html, body)
        self.assertTrue(
            any(
                " | %s | " % template.id in line
                and "inline placeholder inside an attribute" in line
                for line in lines
            )
        )

    def test_button_runs_the_script_on_the_target_version(self):
        migration = self.env["openupgrader.migration"].create(
            {"current_config_id": self.config.id}
        )
        command_result = Mock(
            returncode=0,
            stdout="MAIL-QWEB-FIX | summary | converted=2 skipped=0 "
            "leftover=0 errors=0\n",
            stderr="",
        )
        migration_class = type(migration)
        with tempfile.TemporaryDirectory() as folder:
            migration.folder = folder
            with patch.object(migration_class, "_lock_migrated_db"), patch.object(
                migration_class, "_wait_migrated_db_idle"
            ), patch.object(
                migration_class, "_get_migrated_db_name", return_value="x_migrate"
            ), patch.object(
                migration_class,
                "_get_python_command_shell_command",
                return_value=("odoo shell -d x_migrate", folder, {}),
            ) as command_mock, patch(
                "odoo.addons.openupgrader.models.openupgrader_migration.run",
                return_value=command_result,
            ) as run_mock:
                action = migration.button_fix_mail_templates()

        command_mock.assert_called_once_with(self.config, "x_migrate")
        script_input = run_mock.call_args[1]["input"]
        self.assertIn("def main(env", script_input)
        self.assertIn("\nmain(env)\n", script_input)
        self.assertIn("env.cr.commit()", script_input)
        self.assertEqual(
            run_mock.call_args[1]["env"]["OPENUPGRADER_MAIL_TEMPLATE_DUMP"],
            os.path.join(folder, "mail_template_qweb", "18.0", "x_migrate"),
        )
        self.assertIn("converted=2", migration.mail_template_fix_log)
        self.assertEqual(action["tag"], "display_notification")
        self.assertIn("converted=2", action["params"]["message"])

    def test_button_reports_a_failed_run(self):
        migration = self.env["openupgrader.migration"].create(
            {"current_config_id": self.config.id}
        )
        migration_class = type(migration)
        with tempfile.TemporaryDirectory() as folder:
            migration.folder = folder
            with patch.object(migration_class, "_lock_migrated_db"), patch.object(
                migration_class, "_wait_migrated_db_idle"
            ), patch.object(
                migration_class, "_get_migrated_db_name", return_value="x_migrate"
            ), patch.object(
                migration_class,
                "_get_python_command_shell_command",
                return_value=("odoo shell -d x_migrate", folder, {}),
            ), patch(
                "odoo.addons.openupgrader.models.openupgrader_migration.run",
                return_value=Mock(returncode=1, stdout="", stderr="Traceback: boom"),
            ):
                with self.assertRaises(UserError) as error:
                    migration.button_fix_mail_templates()
        self.assertIn("Traceback: boom", str(error.exception))
