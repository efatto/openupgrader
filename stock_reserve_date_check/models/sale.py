# Copyright 2022 Sergio Corato <https://github.com/sergiocorato>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
from odoo import _, fields, models
from odoo.exceptions import UserError


class SaleOrder(models.Model):
    _inherit = "sale.order"

    enable_reserve_date_check = fields.Boolean(
        help="Forbid reservation on not possible date",
        copy=False,
    )

    def action_confirm(self):
        # Add variable in context to enable check
        for order in self:
            if order.enable_reserve_date_check:
                errors = []
                for line in order.order_line.filtered(
                    lambda x: x.product_id and x.product_id.type == "product"
                ):
                    commitment_date = (
                        line.commitment_date
                        and line.commitment_date.date()
                        or line.order_id.commitment_date
                        and line.order_id.commitment_date.date()
                        or line.order_id.date_order
                        and line.order_id.date_order.date()
                    )
                    avail_date, avail_date_info = line.get_available_date(
                        line.product_id,
                        line.product_uom_qty,
                        fields.Date.context_today(line),
                    )
                    if avail_date > commitment_date:
                        dates_info = avail_date_info.split("\n")
                        commitment_date_str = commitment_date.strftime("%d/%m/%Y")
                        dates_info_clean = [
                            x
                            for x in dates_info
                            if commitment_date_str not in x and x != ""
                        ]
                        dates_info_clean.reverse()
                        produce_delay = 0
                        if line.product_id.produce_delay:
                            produce_delay = int(line.product_id.produce_delay)
                        elif line.product_id.bom_ids:
                            bom_id = line.product_id.bom_ids[0]
                            if bom_id.operation_ids:
                                produce_delay = int(
                                    sum(
                                        bom_id.mapped("operation_ids.time_cycle_manual")
                                        or [0]
                                    )
                                    / 1440
                                )
                        errors.append(
                            _(
                                "Reservation of product [[%s] %s] is not possible "
                                "for date %s!\nAvailable date: %s %s\n"
                                "Exception availability info:\n%s"
                            )
                            % (
                                line.product_id.default_code,
                                line.product_id.name,
                                commitment_date.strftime("%d/%m/%Y"),
                                avail_date.strftime("%d/%m/%Y"),
                                _("(Produce delay: %.0f days)") % produce_delay
                                if produce_delay
                                else "",
                                "\n".join([x for x in dates_info_clean]),
                            )
                        )
                if errors:
                    raise UserError(" ".join(errors))
        super().action_confirm()
        return True
