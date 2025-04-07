from odoo import api, fields, models


class CrmLead(models.Model):
    _inherit = "crm.lead"

    partner_email_exists = fields.Boolean(
        string="Partner Email Exists",
        help="A partner with this email exists and is not linked to this lead",
        compute="_compute_partner_email_exists",
        store=True,
        index=True,
    )

    @api.depends("email_from")
    def _compute_partner_email_exists(self):
        for lead in self:
            if lead.email_from and not lead.partner_id:
                lead.partner_email_exists = bool(
                    self.env["res.partner"].search([("email", "=", lead.email_from)])
                )
            else:
                lead.partner_email_exists = False
