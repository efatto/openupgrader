
from odoo import fields, models


class RemoteRepo(models.Model):
    _name = "remote.repo"
    _description = "Remote Repo"

    name = fields.Char(string="Remote Repo Name")
    openupgrader_repo_id = fields.Many2one(
        comodel_name="openupgrader.repo"
    )
    remote_url = fields.Char(string="Remote Repo URL")
    remote_branch = fields.Char(string="Remote Repo Branch")


class OpenupgraderRepo(models.Model):
    _name = "openupgrader.repo"
    _description = "Openupgrader repositories for version"
    _rec_name = "odoo_version_id"

    odoo_version_id = fields.Many2one(
        comodel_name="odoo.version",
        string="Odoo version",
    )
    remote_repo_ids = fields.One2many(
        comodel_name="remote.repo",
        inverse_name="openupgrader_repo_id",
        string="Remote",
    )
