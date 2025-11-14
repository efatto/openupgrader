from odoo import api, fields, models, tools


class SaleOrderCalendarStateReport(models.Model):
    _name = "sale.order.calendar.state.report"
    _auto = False
    _description = "Sale Order Calendar State Report"

    @api.model
    def _get_order_state(self):
        order_state = self.env["sale.order"]._fields["calendar_state"].selection
        return order_state

    name = fields.Char("Order Name", readonly=True)
    amount_untaxed = fields.Float(readonly=True)
    calendar_state = fields.Selection(selection=_get_order_state, readonly=True)
    production_id = fields.Many2one("mrp.production", readonly=True)
    production_qty = fields.Float(readonly=True)
    production_date_planned_start = fields.Date(readonly=True)

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self._cr.execute(
            """CREATE OR REPLACE VIEW %s AS (
            SELECT
                so.id AS id,
                so.name AS name,
                so.amount_untaxed AS amount_untaxed,
                so.calendar_state AS calendar_state,
                so.production_id AS production_id,
                mo.product_qty AS production_qty,
                to_char(mo.date_planned_start, 'YYYY-MM-DD') AS production_date_planned_start
            FROM sale_order so
                LEFT JOIN mrp_production mo ON mo.id = so.production_id
            WHERE so.production_id is not null
        )
        """
            % self._table
        )
