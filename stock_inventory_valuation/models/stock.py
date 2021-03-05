# -*- coding: utf-8 -*-
##############################################################################
# For copyright and license notices, see __openerp__.py file in root directory
##############################################################################
from openerp import fields, models, exceptions, api, _


class StockInventoryLine(models.Model):
    _inherit = "stock.inventory.line"

    valuation_price_unit = fields.Float(
        'Recalculated unit price')
    valuation_price_subtotal = fields.Float(
        'Recalculated subtotal price')


class StockInventory(models.Model):
    _inherit = "stock.inventory"

    valuation_type = fields.Selection(
        [('fifo', 'FIFO'),
         ('lifo', 'LIFO'),
         ('average', 'AVERAGE'),
         ('standard', 'STANDARD'),
         ('list_price', 'LIST_PRICE')
        ], 'Price valuation')

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
                    res = self.price_calculation(line)
                    price_amount = amount = 0
                    for match in res:
                        price_amount += match[1] * match[2]
                        amount += match[1]
                    if amount != 0.0:
                        line.write({
                            'valuation_price_unit': price_amount / amount,
                            'valuation_price_subtotal': price_amount / amount
                            * line.product_qty,
                        })
            else:
                for line in inv.line_ids:
                    res = self.price_calculation(line)
                    price_amount = amount = 0
                    for match in res:
                        price_amount += match[1] * match[2]
                        amount += match[1]
                    if amount != 0.0:
                        line.write({
                            'valuation_price_unit': price_amount / amount,
                            'valuation_price_subtotal': price_amount,
                        })

    @api.multi
    def price_calculation(self, line):
        order = 'date desc, id desc'
        move_obj = self.env['stock.move']
        if self.valuation_type in ['fifo', 'average']:
            # search for incoming moves
            move_ids = move_obj.search([
                ('company_id', '=', self.env.user.company_id.id),
                ('date', '<=', self.date_inventory or self.date),
                ('state', '=', 'done'),
                ('product_id', '=', line.product_id.id),
                ('location_id.usage', '!=', 'internal'),
                ('location_dest_id.usage', '=', 'internal'),
            ], order=order)
        else:
            # search for incoming and outgoing moves
            move_ids = move_obj.search([
                ('company_id', '=', self.env.user.company_id.id),
                ('date', '<=', self.date_inventory or self.date),
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
        # get only move with lot of inventory line
        flag = False
        for move in move_ids:
            for quant in move.quant_ids.filtered(
                lambda x: x.lot_id == line.prod_lot_id
            ):
                # Convert to UoM of product each time
                uom_from = move.product_uom.id
                qty_from = quant.qty
                product_qty = self.env['product.uom']._compute_qty(
                    uom_from, qty_from, move.product_id.uom_id.id)
                # Get price from purchase line
                price_unit = 0
                if move.invoice_line_ids and move.invoice_line_ids[0].\
                        invoice_id.state in ('open', 'paid'):
                    # In real life, all move lines related to an 1 invoice line
                    # should be in the same state and have the same date
                    inv_line = move.invoice_line_ids[0]
                    price_unit = inv_line.price_unit * (
                        1 - (inv_line.discount or 0.0) / 100.0)
                elif move.purchase_line_id:
                    # get price from purchase line
                    price_unit = move.purchase_line_id.price_unit_net
                else:
                    # Get price from product, move is an inventory or not link
                    # to a purchase (income move created and even invoiced, but
                    # price is not usable here)
                    price_unit = move.product_id.standard_price

                if self.valuation_type == 'fifo':
                    if qty_to_go - product_qty >= 0:
                        tuples.append((move.product_id.id, product_qty,
                                       price_unit, qty_from))
                        qty_to_go -= product_qty
                    else:
                        tuples.append((
                            move.product_id.id, qty_to_go, price_unit,
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
                            tuples.append((move.product_id.id,
                                           (qty_to_go - older_qty), price_unit,
                                           qty_from))
                            qty_to_go = older_qty
                        elif qty_to_go > older_qty <= 0:
                            tuples.append((move.product_id.id, qty_to_go,
                                           price_unit,
                                           qty_from * qty_to_go / product_qty))
                            flag = True
                            break
                elif self.valuation_type == 'average':
                    tuples.append((move.product_id.id, product_qty,
                                   price_unit, qty_from))
            if flag:
                break
        return tuples
