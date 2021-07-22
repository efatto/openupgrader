# Copyright 2018-2021 Sergio Corato <https://github.com/sergiocorato>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
from odoo import fields, models, api, _


class StockInventoryLine(models.Model):
    _inherit = "stock.inventory.line"

    valuation_price_unit = fields.Float(
        'Recalculated unit cost')
    valuation_price_subtotal = fields.Float(
        'Recalculated subtotal cost')


class StockInventory(models.Model):
    _inherit = "stock.inventory"

    valuation_type = fields.Selection([
        ('fifo', 'FIFO'),
        ('lifo', 'LIFO'),
        ('average', 'AVERAGE'),
        ('standard', 'STANDARD'),
        ('list_price', 'LIST_PRICE')
    ], 'Cost valuation')

    @api.multi
    def product_recalculate_value(self):
        for inv in self:
            if inv.valuation_type == 'standard':
                for line in inv.line_ids:
                    line.write({
                        'valuation_price_unit': line.product_id.standard_price,
                        'valuation_price_subtotal':
                            line.product_id.standard_price * line.product_qty,
                    })
            elif inv.valuation_type == 'list_price':
                for line in inv.line_ids:
                    line.write({
                        'valuation_price_unit': line.product_id.lst_price,
                        'valuation_price_subtotal':
                            line.product_id.lst_price * line.product_qty,
                    })
            elif inv.valuation_type == 'average':
                for line in inv.line_ids:
                    res = inv.price_calculation(line)
                    price_amount = 0
                    amount = 0
                    for match in res:
                        price_amount += match[1] * match[2]
                        amount += match[1]
                    line.write({
                        'valuation_price_unit': price_amount / amount if amount != 0.0
                        else 0.0,
                        'valuation_price_subtotal': price_amount / amount
                        * line.product_qty if amount != 0.0 else 0.0,
                    })
            else:
                for line in inv.line_ids:
                    res = inv.price_calculation(line)
                    price_amount = 0
                    amount = 0
                    for match in res:
                        price_amount += match[1] * match[2]
                        amount += match[1]
                    line.write({
                        'valuation_price_unit': price_amount / amount if amount != 0.0
                        else 0.0,
                        'valuation_price_subtotal': price_amount if amount != 0.0
                        else 0.0,
                    })

    @api.multi
    def price_calculation(self, line):
        order = 'date desc, id desc'
        move_obj = self.env['stock.move']
        if self.valuation_type in ['fifo', 'average']:
            # search for incoming moves
            move_ids = move_obj.search([
                ('company_id', '=', self.company_id.id),
                ('date', '<=', self.accounting_date or self.date),
                ('state', '=', 'done'),
                ('product_id', '=', line.product_id.id),
                ('location_id.usage', '!=', 'internal'),
                ('location_dest_id.usage', '=', 'internal'),
            ], order=order)
        else:
            # search for incoming and outgoing moves
            move_ids = move_obj.search([
                ('company_id', '=', self.company_id.id),
                ('date', '<=', self.accounting_date or self.date),
                ('state', '=', 'done'),
                ('product_id', '=', line.product_id.id),
                '|',
                ('location_id.usage', '=', 'internal'),
                ('location_dest_id.usage', '=', 'internal'),
            ], order=order)
        res = self._get_tuples(line, move_ids)
        return res

    def _get_tuples(self, line, move_ids):
        tuples = []
        qty_to_go = line.product_qty
        older_qty = line.product_qty
        # get all move without lot of inventory line because it is not relevant
        flag = False
        for move in move_ids:
            for ml in move.move_line_ids:
                # Convert to UoM of product each time
                uom_from = move.product_uom
                qty_from = ml.qty_done
                product_qty = uom_from._compute_quantity(
                    qty_from, move.product_id.uom_id)
                # Get price from purchase line
                price_unit = 0
                if move.purchase_line_id:
                    if move.purchase_line_id.invoice_lines:
                        # In real life, all move lines related to an 1 invoice line
                        # should be in the same state and have the same date
                        inv_line = move.purchase_line_id.invoice_lines[0]
                        invoice = inv_line.invoice_id
                        if inv_line.invoice_id.state in ('open', 'paid'):
                            price_unit = (
                                invoice.currency_id._convert(
                                    inv_line.price_subtotal,
                                    invoice.company_id.currency_id,
                                    invoice.company_id,
                                    invoice.date or fields.Date.today())
                                / (inv_line.quantity if inv_line.quantity != 0 else 1))
                    else:
                        # get price from purchase line
                        purchase = move.purchase_line_id.order_id
                        price_unit = (
                            purchase.currency_id._convert(
                                move.purchase_line_id.price_subtotal,
                                purchase.company_id.currency_id,
                                purchase.company_id,
                                purchase.date_order or fields.Date.today()
                            ) / (
                                    move.purchase_line_id.product_qty if
                                    move.purchase_line_id.product_qty != 0 else 1
                                )
                        )
                else:
                    # Get price from product, move is an inventory or not link
                    # to a purchase (income move created and even invoiced, but
                    # price is not usable here)
                    price_unit = move.product_id.standard_price

                if self.valuation_type == 'fifo':
                    if qty_to_go - product_qty >= 0:
                        tuples.append((
                            move.product_id.id,
                            product_qty,
                            price_unit,
                            qty_from))
                        qty_to_go -= product_qty
                    else:
                        tuples.append((
                            move.product_id.id,
                            qty_to_go,
                            price_unit,
                            qty_from * qty_to_go / product_qty))
                        flag = True
                        break
                elif self.valuation_type == 'lifo':
                    # sale
                    if move.location_id.usage == 'internal' and \
                            move.location_dest_id.usage != 'internal':
                        older_qty += product_qty
                    # purchase
                    if move.location_id.usage != 'internal' and \
                            move.location_dest_id.usage == 'internal':
                        older_qty -= product_qty
                        if qty_to_go > older_qty > 0:
                            tuples.append((
                                move.product_id.id,
                                qty_to_go - older_qty,
                                price_unit,
                                qty_from))
                            qty_to_go = older_qty
                        elif qty_to_go > older_qty <= 0:
                            tuples.append((
                                move.product_id.id,
                                qty_to_go,
                                price_unit,
                                qty_from * qty_to_go / product_qty))
                            flag = True
                            break
                elif self.valuation_type == 'average':
                    tuples.append((
                        move.product_id.id,
                        product_qty,
                        price_unit,
                        qty_from))
            if flag:
                break
        return tuples
