from odoo import api, fields, models,  _
from odoo.exceptions import UserError


class OdooVersion(models.Model):
    _name = "odoo.version"
    _description = "Odoo Version"

    name = fields.Selection(
        string="Odoo Version name",
        selection=[
            ("7.0", "7.0"),
            ("8.0", "8.0"),
            ("9.0", "9.0"),
            ("10.0", "10.0"),
            ("11.0", "11.0"),
            ("12.0", "12.0"),
            ("13.0", "13.0"),
            ("14.0", "14.0"),
            ("15.0", "15.0"),
            ("16.0", "16.0"),
            ("17.0", "17.0"),
            ("18.0", "18.0"),
        ],
        required=True,
    )
    python_version = fields.Char(
        string="Python Version",
        required=True,
        default="3.7.16"
    )
    odoo_is_openupgrade = fields.Boolean(
        string="Odoo is Openupgrade",
        compute="_compute_odoo_is_openupgrade",
        store=True,
    )

    @api.depends("name")
    def _compute_odoo_is_openupgrade(self):
        for record in self:
            if float(record.name) < 14:
                record.odoo_is_openupgrade = True
            else:
                record.odoo_is_openupgrade = False

    def button_create_venv(self):
        self.ensure_one()
        openupgrader_migration = self.env["openupgrader.migration"].search([])
        if len(openupgrader_migration) != 1:
            raise UserError(_("Missing Openupgrader Migration record!"))
        if len(openupgrader_migration) > 1:
            raise UserError(_("Only one Openupgrader Migration record can be created!"))
        openupgrader_migration.create_venv_git_version(self.name)
