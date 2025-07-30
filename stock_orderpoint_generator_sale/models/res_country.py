from odoo import fields, models


class ResCountry(models.Model):
    _inherit = "res.country"

    reorder_coeff = fields.Float(
        default=4, help="This value will be used in orderpoint generation."
    )
