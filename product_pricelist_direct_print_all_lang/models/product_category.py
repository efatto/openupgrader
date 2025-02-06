from odoo import fields, models


class ProductCategory(models.Model):
    _inherit = "product.category"
    _order = "sequence, complete_name"

    sequence = fields.Integer(
        string="Sequence",
        index=True,
        default=1,
    )
    show_stock_available = fields.Boolean(
        string="Show stock availability in pricelist report",
    )
