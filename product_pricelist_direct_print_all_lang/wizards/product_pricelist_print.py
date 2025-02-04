from collections import defaultdict

from odoo import fields, models
from odoo.osv import expression


class ProductPricelistPrint(models.TransientModel):
    _inherit = "product.pricelist.print"

    show_all_langs = fields.Boolean(string="Show All Langs")
    show_child_categ = fields.Boolean(string="Show Child Categories")

    def get_products_domain(self):
        domain = super().get_products_domain()
        if self.categ_ids and self.show_child_categ:
            domain = expression.OR(
                [
                    domain,
                    [
                        ("sale_ok", "=", True),
                        ("categ_id", "child_of", self.categ_ids.ids),
                    ],
                ]
            )
        return domain

    def get_group_key(self, product):
        max_level = self.max_categ_level or 99
        return "%s ][ %s" % (
            product.categ_id.sequence,
            " / ".join(product.categ_id.complete_name.split(" / ")[:max_level]),
        )

    def get_groups_to_print(self):
        self.ensure_one()
        products = self.get_products_to_print()
        if not products:
            return []
        group_dict = defaultdict(lambda: products.browse())
        for product in products:
            key = self.get_group_key(product)
            group_dict[key] |= product
        group_list = []
        for key in sorted(group_dict.keys()):
            group_list.append(
                {
                    "group_name": key.split(" ][ ")[1],
                    "products": self.get_sorted_products(group_dict[key]),
                }
            )
        return group_list
