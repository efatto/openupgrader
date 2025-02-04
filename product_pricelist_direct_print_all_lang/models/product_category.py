from odoo import api, fields, models,  _


class ProductCategory(models.Model):
    _inherit = "product.category"
    _order = "sequence, complete_name"

    sequence = fields.Integer(
        string="Sequence",
        index=True,
        default=1,
    )
