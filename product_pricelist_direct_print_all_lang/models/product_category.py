from odoo import api, fields, models,  _


class ProductCategory(models.Model):
    _inherit = "product.category"

    sequence = fields.Integer(
        string="Sequence",
        index=True,
        default=1,
    )
