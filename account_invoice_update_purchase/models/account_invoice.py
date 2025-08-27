# Copyright 2021 Sergio Corato <https://github.com/sergiocorato>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import api, fields, models


class AccountMove(models.Model):
    _inherit = "account.move"

    purchase_force_valid = fields.Boolean()


class AccountMoveLine(models.Model):
    _inherit = "account.move.line"

    display_update_purchase_button = fields.Boolean(
        compute="_compute_display_update_purchase_button", store=True
    )

    @api.depends(
        "purchase_line_id",
        "purchase_line_id.price_unit",
        "purchase_line_id.discount",
        "purchase_line_id.discount2",
        "purchase_line_id.discount3",
        "move_id.purchase_force_valid",
        "purchase_line_id.product_uom",
        "product_uom_id",
        "price_unit",
        "discount",
        "discount2",
        "discount3",
    )
    def _compute_display_update_purchase_button(self):
        for line in self:
            line.display_update_purchase_button = (
                not line.move_id.purchase_force_valid
                and line.purchase_line_id
                and (
                    line.purchase_line_id.price_unit
                    != line.product_uom_id._compute_price(
                        line.price_unit, line.purchase_line_id.product_uom
                    )
                    or line.purchase_line_id.discount != line.discount
                    or line.purchase_line_id.discount2 != line.discount2
                    or line.purchase_line_id.discount3 != line.discount3
                )
            )

    def update_purchase(self):
        # todo convert price from currency?
        self.purchase_line_id.write(
            {
                "price_unit": self.product_uom_id._compute_price(
                    self.price_unit, self.purchase_line_id.product_uom
                ),
                "discount": self.discount,
                "discount2": self.discount2,
                "discount3": self.discount3,
            }
        )
        supplierinfos = self.env["product.supplierinfo"].search(
            [
                ("name", "=", self.purchase_line_id.order_id.partner_id.id),
                "|",
                ("product_id", "=", self.product_id.id),
                ("product_tmpl_id", "=", self.product_id.product_tmpl_id.id),
            ]
        )
        for supplierinfo in supplierinfos:
            if (
                supplierinfo.date_end
                and supplierinfo.date_end
                >= (self.move_id.invoice_date or fields.Date.today())
                or not supplierinfo.date_end
            ) and (
                supplierinfo.date_start
                and supplierinfo.date_start
                <= (self.move_id.invoice_date or fields.Date.today())
                or not supplierinfo.date_start
            ):
                supplierinfo.write(
                    {
                        "price": self.product_uom_id._compute_price(
                            self.price_unit, supplierinfo.product_uom
                        ),
                        "discount": self.discount,
                        "discount2": self.discount2,
                        "discount3": self.discount3,
                    }
                )
