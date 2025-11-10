from odoo import api, fields, models
from odoo.tools.date_utils import relativedelta


class SupplierInfo(models.Model):
    _inherit = "product.supplierinfo"

    overtime_purchase_delay = fields.Integer(
        compute="_compute_overtime_purchase_delay",
        store=True,
        help="Over the past x days (configured in parameter "
        "'purchase_stock.on_time_delivery_days', by default 365 days): the number "
        "of days over time of a minimum 3 days for this vendor to deliver this "
        "product.",
    )

    @api.depends("name.purchase_line_ids", "name.purchase_line_ids.move_ids")
    def _compute_overtime_purchase_delay(self):
        for seller in self:
            overtime_purchase_delay = 0
            if seller.name.purchase_line_ids:
                date_order_days_delta = int(
                    self.env["ir.config_parameter"]
                    .sudo()
                    .get_param("purchase_stock.on_time_delivery_days", default="365")
                )
                order_lines = seller.name.purchase_line_ids.filtered(
                    lambda l: l.date_order
                    > fields.Datetime.today()
                    - relativedelta(days=date_order_days_delta)
                    and l.order_id.state in ["done", "purchase"]
                    and (
                        l.product_id == seller.product_id
                        or l.product_id.product_tmpl_id == seller.product_tmpl_id
                    )
                )
                overtime_moves = order_lines.move_ids.filtered(
                    lambda m: m.state == "done"
                    and (
                        m.product_id == seller.product_id
                        or m.product_id.product_tmpl_id == seller.product_tmpl_id
                    )
                    and m.date
                    > (m.purchase_line_id.date_planned + relativedelta(days=3))
                )
                if overtime_moves:
                    overtime_purchase_delay = sum(
                        (m.date.date() - m.purchase_line_id.date_planned.date()).days
                        for m in overtime_moves
                    ) / len(overtime_moves)
            seller.overtime_purchase_delay = overtime_purchase_delay
