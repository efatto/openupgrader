# Copyright 2022 Sergio Corato <https://github.com/sergiocorato>
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).

import math
from datetime import datetime

from scipy.stats import norm

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools.date_utils import relativedelta
from odoo.tools.float_utils import float_round


class Orderpoint(models.Model):
    _inherit = "stock.warehouse.orderpoint"

    orderpoint_tmpl_id = fields.Many2one("stock.warehouse.orderpoint.template")
    is_draft = fields.Boolean(
        string="Is Draft",
        help="To be enabled manually",
    )


class OrderpointTemplate(models.Model):
    _inherit = "stock.warehouse.orderpoint.template"

    compute_on_sale = fields.Boolean(string="Compute On Sale")
    compute_on_out = fields.Boolean(
        string="Compute On Out",
        help="Compute On All Out or Consumed (excluded inventory)",
    )
    move_days = fields.Integer(
        help="Used when not filled date from and to in maximum criteria"
    )
    service_level = fields.Float()
    order_mngt_cost = fields.Float()
    variation_percent = fields.Float(
        help="Increment/decrement value of qty by this percent"
    )
    product_ctg_ids = fields.Many2many("product.category", string="Product Category")
    auto_max_qty_criteria = fields.Selection(selection_add=[("sum", "Sum")])
    log_info = fields.Text()
    orderpoint_count = fields.Integer(
        "# Orderpoint", compute="_compute_orderpoint_count"
    )
    draft_orderpoint_count = fields.Integer(
        "# Draft Orderpoint", compute="_compute_orderpoint_count"
    )
    is_new_orderpoint_draft = fields.Boolean(
        string="New Orderpoints are in Draft",
        default=True,
        help="New orderpoints are not confirmed by default",
    )

    def create_orderpoints(self, products):
        """Override to create new orderpoints in draft optionally"""
        if self.is_new_orderpoint_draft:
            # delete inactive instances
            orderpoints = self.env["stock.warehouse.orderpoint"].with_context(
                active_test=False
            ).search([
                ("orderpoint_tmpl_id", "=", self.id),
                ("active", "=", False),
            ])
            orderpoints.unlink()
            # create new istances to put in a new o2m
            self.with_context(is_draft=True)._create_instances(products)
        else:
            self._disable_old_instances(products)
            self._create_instances(products)

    def button_confirm_orderpoints(self):
        # disable current orderpoints and re-enable the newly created
        self.env["stock.warehouse.orderpoint"].search(
            [
                ("orderpoint_tmpl_id", "=", self.id),
                ("is_draft", "=", False),
            ]
        ).write({"active": False})
        self.env["stock.warehouse.orderpoint"].search(
            [
                ("orderpoint_tmpl_id", "=", self.id),
                ("is_draft", "=", True),
                ("active", "=", False),
            ]
        ).write({"is_draft": False, "active": True})

    def _compute_orderpoint_count(self):
        for record in self:
            record.orderpoint_count = self.env[
                "stock.warehouse.orderpoint"
            ].search_count(
                [("orderpoint_tmpl_id", "=", record.id), ("is_draft", "=", False)]
            )
            record.draft_orderpoint_count = self.env[
                "stock.warehouse.orderpoint"
            ].search_count(
                [
                    ("orderpoint_tmpl_id", "=", record.id),
                    ("is_draft", "=", True),
                    ("active", "=", False),
                ]
            )

    @api.onchange("compute_on_sale", "compute_on_out", "auto_max_qty_criteria")
    def _onchange_compute_on(self):
        # only "sum" option is possible when compute_on_* is set
        # note: auto_max_qty_criteria is invisible in this case
        if (
            self.compute_on_sale or self.compute_on_out
        ) and self.auto_max_qty_criteria != "sum":
            self.auto_max_qty_criteria = "sum"

    @api.onchange("compute_on_sale", "compute_on_out")
    def onchange_compute_on_sale(self):
        if self.compute_on_sale or self.compute_on_out:
            self.auto_min_qty = self.auto_max_qty = True
            self.auto_max_qty_criteria = "sum"
        else:
            self.auto_min_qty = self.auto_max_qty = False
            self.auto_max_qty_criteria = False

    @api.constrains(
        "move_days", "auto_max_date_start", "auto_max_date_end", "auto_max_qty"
    )
    def check_move_days(self):
        for template in self.filtered(lambda x: x.compute_on_sale or x.compute_on_out):
            if (
                not template.move_days
                and not (template.auto_max_date_start or template.auto_max_date_end)
                and template.auto_max_qty
            ):
                raise UserError(
                    _(
                        "Move days cannot be equal to 0 if Auto max qty is "
                        "set and no min and max date are set!"
                    )
                )

    @api.constrains("service_level")
    def check_service_level(self):
        for template in self.filtered(lambda x: x.compute_on_sale or x.compute_on_out):
            if template.service_level > 1 or template.service_level <= 0.0:
                raise UserError(_("Service level cannot be greater than 1 nor <= 0!"))

    @api.model
    def _get_criteria_methods(self):
        res = super()._get_criteria_methods()
        res.update(
            {
                "sum": sum,
            }
        )
        return res

    def _template_fields_to_discard(self):
        """In order to create every orderpoint we should pop this template
        customization fields"""
        res = super()._template_fields_to_discard()
        res += [
            "move_days",
            "service_level",
            "order_mngt_cost",
            "compute_on_sale",
            "compute_on_out",
            "log_info",
            "variation_percent",
            "product_ctg_ids",
            "is_new_orderpoint_draft",
        ]
        return res

    def create_auto_orderpoints(self):
        for template in self:
            if not template.auto_generate:
                continue
            if (
                not template.auto_last_generation
                or template.write_date > template.auto_last_generation
            ):
                template.auto_last_generation = fields.Datetime.now()
                product_ids = template.auto_product_ids
                if template.product_ctg_ids:
                    product_ids = self.env["product.product"].search(
                        [
                            ("categ_id", "in", template.product_ctg_ids.ids),
                            ("orderpoint_generate_active", "=", True),
                            ("state", "not in", ["end", "obsolete"]),
                        ]
                    )
                template.create_orderpoints(product_ids)

    def _create_instances(self, product_ids):  # noqa C901
        """Create instances of model using template inherited model and
        compute autovalues if needed"""
        orderpoint_model = self.env["stock.warehouse.orderpoint"]
        for record in self:
            record.log_info = ""
            # Flag equality so we compute the values just once
            if record.compute_on_sale or record.compute_on_out:
                if record.auto_max_date_start and record.auto_max_date_end:
                    auto_max_date_end = record.auto_max_date_end
                    auto_max_date_start = record.auto_max_date_start
                else:
                    date_end = datetime.now()
                    date_start = date_end + relativedelta(days=-record.move_days)
                    auto_max_date_end = date_end
                    auto_max_date_start = date_start
                auto_min_date_end = auto_max_date_end
                auto_min_date_start = auto_max_date_start
            else:
                auto_max_date_end = record.auto_max_date_end
                auto_max_date_start = record.auto_max_date_start
                auto_min_date_end = record.auto_min_date_end
                auto_min_date_start = record.auto_min_date_start
            auto_same_values = (
                (auto_max_date_start == auto_min_date_start)
                and (auto_max_date_end == auto_max_date_end)
                and (record.auto_max_qty_criteria == record.auto_min_qty_criteria)
            )
            stock_max_qty = {}
            if record.auto_min_qty:
                stock_min_qty = self._get_product_qty_by_criteria(
                    product_ids,
                    location_id=record.location_id,
                    from_date=auto_min_date_start,
                    to_date=auto_min_date_end,
                    criteria=record.auto_min_qty_criteria,
                )
                if auto_same_values:
                    stock_max_qty = stock_min_qty
            if record.auto_max_qty and not stock_max_qty:
                if record.compute_on_sale or record.compute_on_out:
                    stock_max_qty = self._get_product_qty_by_criteria_sale(
                        product_ids,
                        location_id=record.location_id,
                        from_date=auto_max_date_start,
                        to_date=auto_max_date_end,
                        criteria=record.auto_max_qty_criteria,
                        compute_on_sale=record.compute_on_sale,
                    )
                else:
                    stock_max_qty = self._get_product_qty_by_criteria(
                        product_ids,
                        location_id=record.location_id,
                        from_date=auto_max_date_start,
                        to_date=auto_max_date_end,
                        criteria=record.auto_max_qty_criteria,
                    )
            for data in record.copy_data():
                for discard_field in self._template_fields_to_discard():
                    data.pop(discard_field)
                for product_id in product_ids:
                    phantom_bom = self.env["mrp.bom"].search(
                        [
                            ("type", "=", "phantom"),
                            "|",
                            ("product_tmpl_id", "=", product_id.product_tmpl_id.id),
                            ("product_id", "=", product_id.id),
                        ]
                    )
                    if phantom_bom:
                        record.log_info = "\n".join(
                            [
                                record.log_info,
                                (
                                    _("Product with phantom bom excluded: %s")
                                    % product_id.default_code
                                ),
                            ]
                        )
                        continue
                    if not product_id.standard_price:
                        record.log_info = "\n".join(
                            [
                                record.log_info,
                                (
                                    _("Missing price in product %s!")
                                    % product_id.default_code
                                ),
                            ]
                        )
                        continue
                    vals = data.copy()
                    vals["name"] = "%s - %s" % (vals["name"], product_id.default_code)
                    vals["product_id"] = product_id.id
                    # function replicated from calc file
                    move_days = record.move_days
                    if (
                        not move_days
                        and record.auto_max_date_start
                        and record.auto_max_date_end
                    ):
                        move_days = (
                            record.auto_max_date_end - record.auto_max_date_start
                        ).days
                    max_qty = stock_max_qty[product_id.id]
                    qty_by_day = max_qty / (move_days or 1)
                    consumed_qty_by_lead_time = (
                        qty_by_day * (1 + (record.variation_percent / 100.0))
                    ) * (product_id.purchase_delay or 1)
                    service_factor = norm.ppf(record.service_level)
                    lead_time_factor = (product_id.purchase_delay or 1) ** (1 / 2)
                    security_stock = int(
                        math.ceil(qty_by_day * service_factor * lead_time_factor)
                    )
                    min_qty = float_round(
                        math.ceil(consumed_qty_by_lead_time + security_stock),
                        precision_digits=-1,
                        rounding_method="UP",
                    )
                    # lot_to_reorder = ARROTONDA.ECCESSO.XCL(
                    #   RADQ(
                    #     2 * record.order_mngt_cost * qty_by_day * move_days
                    #     / (0, 15 * product_id.standard_price)
                    #   ); 1)
                    lot_to_reorder = float_round(
                        math.ceil(
                            (
                                2
                                * record.order_mngt_cost
                                * qty_by_day
                                * move_days
                                / (0.15 * product_id.standard_price)
                            )
                            ** (1 / 2)
                        ),
                        precision_digits=-1,
                        rounding_method="UP",
                    )
                    reorder_coeff = fields.first(
                        product_id.seller_ids
                    ).name.country_id.reorder_coeff
                    # Set a maximum of orders by period
                    if lot_to_reorder < (max_qty / reorder_coeff):
                        lot_to_reorder = (max_qty / reorder_coeff)
                    max_qty = min_qty + lot_to_reorder
                    # end function
                    if record.auto_min_qty:
                        vals["product_min_qty"] = min_qty
                    if record.auto_max_qty:
                        vals["product_max_qty"] = max_qty
                    vals["qty_multiple"] = product_id.purchase_multiple_qty
                    vals["orderpoint_tmpl_id"] = record.id
                    if self.env.context.get("is_draft"):
                        vals["is_draft"] = True
                        vals["active"] = False
                    orderpoint_model.create(vals)
                    record.log_info = "\n".join(
                        [
                            record.log_info,
                            (
                                _(
                                    "[%s] Product orderpoint created (from max qty in "
                                    "selected date range / move days period: %s) "
                                    "(Move days: %s, "
                                    "Qty by day: %s, "
                                    "Consumed qty by lead time: %s, "
                                    "Service factor: %s, "
                                    "Lead time factor: %s, "
                                    "Security stock: %s, "
                                    "Minimum qty: %s, "
                                    "Lot to reorder: %s, "
                                    "Maximum qty: %s)"
                                )
                                % (
                                    product_id.default_code,
                                    stock_max_qty[product_id.id],
                                    move_days,
                                    qty_by_day,
                                    consumed_qty_by_lead_time,
                                    service_factor,
                                    lead_time_factor,
                                    security_stock,
                                    min_qty,
                                    lot_to_reorder,
                                    max_qty,
                                )
                            ),
                        ]
                    )

    def _disable_old_instances(self, products):
        """Clean old instance by setting those inactives"""
        super()._disable_old_instances(products)
        orderpoints = self.env["stock.warehouse.orderpoint"].search(
            [("orderpoint_tmpl_id", "=", self.id)]
        )
        orderpoints.write({"active": False})

    @api.model
    def _get_product_qty_by_criteria_sale(
        self, products, location_id, from_date, to_date, criteria, compute_on_sale=False
    ):
        """Returns a dict with product ids as keys and the resulting
        calculation of historic moves according to criteria"""
        stock_qty_history = products._compute_historic_sale_quantities_dict(
            location_id=location_id,
            from_date=from_date,
            to_date=to_date,
            compute_on_sale=compute_on_sale,
        )
        criteria_methods = self._get_criteria_methods()
        if criteria == "sum":
            return {
                x: criteria_methods[criteria](y["move_history"])
                for x, y in stock_qty_history.items()
            }
        return {
            x: criteria_methods[criteria](y["stock_history"])
            for x, y in stock_qty_history.items()
        }
