# Copyright 2023 Sergio Corato <https://github.com/sergiocorato>
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).
from odoo import api, fields, models


class SupplierInfo(models.Model):
    _inherit = "product.supplierinfo"

    multiple_qty = fields.Float(default=1.0)


class ProductTemplate(models.Model):
    _inherit = "product.template"

    custom_purchase_delay = fields.Integer(
        string="Purchase Lead Time",
        help="Lead time in days to purchase this product. If set, it is used instead of"
             " the computed field."
    )
    purchase_delay = fields.Float(
        string="Purchase Lead Time (computed)",
        compute="_compute_purchase_delay",
        store=True,
        default=0.0,
        help="Lead time in days to purchase this product. "
        "Computed from delay of first seller.",
    )
    purchase_multiple_qty = fields.Float(
        "Purchase Multiple Qty",
        compute="_compute_purchase_delay",
        store=True,
        default=1.0,
        help="Purchase multiple quantity to purchase this product. "
        "Computed from multiple quantity of first seller.",
    )

    @api.depends("seller_ids", "seller_ids.delay", "custom_purchase_delay")
    def _compute_purchase_delay(self):
        for product_tmpl in self:
            if product_tmpl.custom_purchase_delay and product_tmpl.purchase_ok:
                product_tmpl.purchase_delay = product_tmpl.custom_purchase_delay
            elif product_tmpl.seller_ids and product_tmpl.purchase_ok:
                product_tmpl.purchase_delay = product_tmpl.seller_ids[0].delay
                product_tmpl.purchase_multiple_qty = product_tmpl.seller_ids[
                    0
                ].multiple_qty
            else:
                product_tmpl.purchase_delay = 0
