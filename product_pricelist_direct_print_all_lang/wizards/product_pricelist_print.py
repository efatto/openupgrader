from odoo import fields, models
from odoo.osv import expression


class ProductPricelistPrint(models.TransientModel):
    _inherit = "product.pricelist.print"

    show_all_langs = fields.Boolean(string="Show All Langs")
    show_child_categ = fields.Boolean(string="Show Child Categories")

    def get_products_domain(self):
        domain = super().get_products_domain()
        if self.categ_ids and self.show_child_categ:
            domain = expression.OR([
                domain,
                [
                    ("sale_ok", "=", True),
                    ("categ_id", "child_of", self.categ_ids.ids),
                ]
            ])
        return domain
