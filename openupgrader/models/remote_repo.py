from odoo import fields, models


class RemoteRepo(models.Model):
    _name = "remote.repo"
    _description = "Remote Repo"

    name = fields.Char(string="Remote Repo Name")
    remote_url = fields.Char(string="Remote Repo URL")
    remote_branch = fields.Char(string="Remote Repo Branch")
    github_user = fields.Char(string="Git User")
    github_token = fields.Char(string="Git Token")
    is_odoo = fields.Boolean()

    def name_get(self):
        vals = []
        for record in self:
            vals.append(
                tuple([record.id, "%s - %s" % (record.name, record.remote_branch)])
            )
        return vals
