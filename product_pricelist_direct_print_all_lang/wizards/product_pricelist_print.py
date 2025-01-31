from odoo import fields, models


class ProductPricelistPrint(models.TransientModel):
    _inherit = "product.pricelist.print"

    show_all_langs = fields.Boolean(string="Show All Langs")
