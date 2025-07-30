# Copyright 2022 Sergio Corato <https://github.com/sergiocorato>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
from odoo import _, fields, models
from odoo.exceptions import UserError
from odoo.tools.date_utils import relativedelta

stock_options = {
    "from_stock": _("FROM STOCK"),
    "to_produce": _("TO PRODUCE"),
    "to_purchase": _("TO PURCHASE"),
}


class SaleOrderLine(models.Model):
    _inherit = "sale.order.line"

    def get_available_date(  # noqa: C901
        self, product_id, qty, date_start, available_date=False, level=0
    ):
        child = "└"
        vertical = "─"
        available_info = []
        available_dates_info = ""
        (
            domain_quant_loc,
            domain_move_in_loc,
            domain_move_out_loc,
        ) = product_id._get_domain_locations()
        incoming_stock_moves = self.env["stock.move"].search(
            [
                ("product_id", "=", product_id.id),
                ("product_uom_qty", ">", 0),
                ("state", "not in", ["done", "cancel"]),
                ("date", ">=", date_start),
            ]
            + domain_move_in_loc
        )
        reserved_stock_moves = self.env["stock.move"].search(
            [
                ("product_id", "=", product_id.id),
                ("product_uom_qty", ">", 0),
                ("state", "not in", ["done", "cancel"]),
                ("date", ">=", date_start),
            ]
            + domain_move_out_loc
        )
        # use stock move date as it is shown as scheduled date
        for reserve_date in set(
            [x.date() for x in [w.date or w.date for w in reserved_stock_moves]]
            + [y.date() for y in [z.date or z.date for z in incoming_stock_moves]]
            + [date_start]
        ):
            available_info.append(
                {
                    "info": "Stock move",
                    "date": reserve_date,
                    "qty": product_id.with_context(
                        to_date=reserve_date
                    ).virtual_available_at_date_move,
                }
            )
        # FIXME when all dates has sufficient availability is ignored!
        # todo remove all availability with a previous availability with qty <
        #  requested qty, to prevent "steal" of goods from reserved moves
        #  get the available date later of the far available date with qty <
        if available_info:
            farther_unreservable_dates = [
                x["date"] for x in available_info if x["qty"] < qty
            ]
            if not farther_unreservable_dates:
                # all dates have a sufficient qty
                farther_unreservable_dates = [min([x["date"] for x in available_info])]
                stock_available_date = farther_unreservable_dates[0]
            elif len(farther_unreservable_dates) == len(available_info):
                # None of the available dates meets the requested quantity
                stock_available_date = False
            else:
                stock_available_date = min(
                    [
                        x["date"]
                        for x in available_info
                        if x["date"] >= max(farther_unreservable_dates)
                        and x["qty"] >= qty
                    ]
                    or [False]
                )
        else:
            raise UserError(_("No available info found!"))
        if product_id.bom_ids:
            # fixme need to filter boms?
            option = stock_options["to_produce"]
            bom_id = product_id.bom_ids[0]
            avail_dates = []
            if stock_available_date:
                # available in stock
                available_date = stock_available_date
                option = stock_options["from_stock"]
                available_text = _(
                    "%s[BOM] [%s] [QTY: %s] [%s] plannable date %s.\n"
                ) % (
                    vertical * level,
                    product_id.default_code,
                    qty,
                    option,
                    available_date.strftime("%d/%m/%Y"),
                )
            else:
                for bom_line in bom_id.bom_line_ids.sorted(
                    key=lambda x: x.product_id.bom_ids, reverse=True
                ):
                    avail_date, avail_text = self.get_available_date(
                        bom_line.product_id,
                        qty * bom_line.product_qty,
                        date_start,
                        available_date,
                        level=level + 1,
                    )
                    if avail_date:
                        avail_dates.append(avail_date)
                    if avail_text and avail_text not in available_dates_info:
                        available_dates_info += avail_text
                if avail_dates:
                    available_date = max(avail_dates)
                    available_text = _(
                        "%s[BOM] [%s] [QTY: %s] [%s] plannable date %s.\n"
                    ) % (
                        vertical * level,
                        product_id.default_code,
                        qty,
                        option,
                        available_date.strftime("%d/%m/%Y"),
                    )
                else:
                    available_text = _(
                        "%s[BOM] [%s] [QTY: %s] [%s] plannable date %s.\n"
                    ) % (
                        vertical * level,
                        product_id.default_code,
                        qty,
                        option,
                        "Not found",
                    )
            if available_date and not stock_available_date:
                produce_delay = 0
                if product_id.produce_delay:
                    produce_delay = int(product_id.produce_delay)
                elif bom_id.operation_ids:
                    produce_delay = (
                        sum(bom_id.mapped("operation_ids.time_cycle_manual") or [0])
                        / 1440
                    )
                # get current next available slot for this product in its workcenter
                if bom_id.operation_ids:
                    start_date = available_date
                    for operation in bom_id.operation_ids:
                        (
                            op_start_dt,
                            op_end_dt,
                        ) = operation.workcenter_id._get_first_available_slot(
                            self.commitment_date, operation.time_cycle_manual
                        )
                        op_start_date = op_start_dt.date()
                        if op_start_date > start_date:
                            start_date = op_start_date
                    available_date = start_date
                    available_text = _(
                        "%s[BOM] [%s] [QTY: %s] [%s] plannable date %s.\n"
                    ) % (
                        vertical * level,
                        product_id.default_code,
                        qty,
                        option,
                        available_date.strftime("%d/%m/%Y"),
                    )
                elif produce_delay:
                    available_date += relativedelta(days=int(produce_delay))
                    available_text = _(
                        "%s[BOM] [%s] [QTY: %s] [%s] plannable date %s.\n"
                    ) % (
                        vertical * level,
                        product_id.default_code,
                        qty,
                        option,
                        available_date.strftime("%d/%m/%Y"),
                    )
            if available_text and available_text not in available_dates_info:
                available_dates_info += available_text
        else:
            purchase_available_date = fields.Date.today() + relativedelta(
                days=int(product_id.purchase_delay)
            )
            if stock_available_date:
                # removed by customer request:
                # and stock_available_date <= purchase_available_date:
                # so get any future date of purchase order as available from stock
                available_date = stock_available_date
                option = stock_options["from_stock"]
                available_text = _(
                    "%s[COMP] [%s] [QTY: %s] [%s] plannable date %s.\n"
                ) % (
                    vertical * (level - 1) + child,
                    product_id.default_code,
                    qty,
                    option,
                    available_date.strftime("%d/%m/%Y"),
                )
            else:
                # Check if ordering the product the incoming date will be sooneer
                option = stock_options["to_purchase"]
                if not available_date or (
                    stock_available_date
                    and purchase_available_date < stock_available_date
                ):
                    available_date = purchase_available_date
                else:
                    # available in stock
                    available_date = stock_available_date
                    option = stock_options["from_stock"]
                available_text = _(
                    "%s[COMP] [%s] [QTY: %s] [%s] plannable date %s.\n"
                ) % (
                    vertical * (level - 1) + child,
                    product_id.default_code,
                    qty,
                    option,
                    available_date.strftime("%d/%m/%Y"),
                )

            if available_text not in available_dates_info:
                available_dates_info += available_text
        return available_date, available_dates_info
