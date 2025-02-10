from odoo import fields, models


class ProductCategory(models.Model):
    _inherit = "product.category"

    is_special = fields.Boolean(
        string="Is Special",
    )
