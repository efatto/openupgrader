from odoo import fields, models


class MrpProduction(models.Model):
    _inherit = "mrp.production"

    production_notes = fields.Text(
        related="sale_id.production_notes",
        readonly=False,
        store=True,
    )
    is_prototype = fields.Boolean(related="sale_id.is_prototype", store=True)
