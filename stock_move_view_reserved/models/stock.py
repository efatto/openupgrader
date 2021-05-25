# Copyright 2021 Sergio Corato <https://github.com/sergiocorato>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models, api, _


class StockMoveLine(models.Model):
    _inherit = "stock.move.line"

    date_expected = fields.Datetime(
        string='Expected Date',
        store=True,
        related='move_id.date_expected'
    )
