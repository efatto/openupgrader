from odoo import api, fields, models


class StockMove(models.Model):
    _inherit = 'stock.move'

    def _get_price_with_discount(self, line, price):
        if hasattr(line, 'discount'):
            price = price * (1 - line.discount / 100.0)
        if hasattr(line, 'discount2'):
            price = price * (1 - line.discount2 / 100.0)
        if hasattr(line, 'discount3'):
            price = price * (1 - line.discount3 / 100.0)
        return price

    @api.multi
    def _get_current_price_unit(self):
        self.ensure_one()
        PurchaseOrderLine = self.env['purchase.order.line']
        price_unit = - self.price_unit
        date_price_unit = False
        origin_price_unit = False
        # search before datetime of move
        # 0. is there a move originated from a purchase order, with or without invoice
        # this cover also the case of a purchase invoice linked to a purchase order, as
        # it creates a move
        lines = self.search([
            ('product_id', '=', self.product_id.id),
            ('state', 'in', ['done', 'assigned']),
            ('date', '<=', self.date),
            ('purchase_line_id', '!=', False),
        ]).sorted(
            key=lambda l: l.date, reverse=True)
        if lines:
            last_line = lines[:1]
            invoice_lines = self.env['account.invoice.line'].search([
                ('purchase_line_id', '=', last_line.purchase_line_id.id),
                ('invoice_id.type', '=', 'in_invoice'),
                ('invoice_id.state', 'not in', ['draft', 'cancel']),
                ('product_id', '=', self.product_id.id),
            ]).sorted(
                key=lambda l: l.invoice_id.date_invoice, reverse=True)
            if invoice_lines:
                # get price from invoice if exists
                invoice_line = invoice_lines[0]
                price_unit = self._get_price_with_discount(
                    invoice_line, invoice_line.price_unit)
                date_price_unit = fields.Datetime.to_datetime(
                    invoice_line.invoice_id.date_invoice)
                origin_price_unit = invoice_line.invoice_id.number
            else:
                purchase_line = last_line.purchase_line_id
                price_unit = self._get_price_with_discount(
                    purchase_line, purchase_line.price_unit)
                date_price_unit = last_line.date
                origin_price_unit = last_line.purchase_line_id.order_id.name
            return price_unit, date_price_unit, origin_price_unit

        # 1. there is an invoice purchase line, linked or not to a purchase order
        invoice_lines = self.env['account.invoice.line'].search([
            ('invoice_id.type', '=', 'in_invoice'),
            ('invoice_id.state', 'not in', ['draft', 'cancel']),
            ('product_id', '=', self.product_id.id),
        ]).sorted(
            key=lambda l: l.invoice_id.date_invoice, reverse=True)
        if invoice_lines:
            # get price from invoice if exists
            invoice_line = invoice_lines[0]
            price_unit = self._get_price_with_discount(
                invoice_line, invoice_line.price_unit)
            date_price_unit = fields.Datetime.to_datetime(
                invoice_line.invoice_id.date_invoice)
            origin_price_unit = invoice_line.invoice_id.number
            return price_unit, date_price_unit, origin_price_unit

        # 2. else, is there a purchase order confirmed or done
        lines = PurchaseOrderLine.search([
            ('product_id', '=', self.product_id.id),
            ('state', 'in', ['purchase', 'done']),
            ('date_order', '<=', self.date),
        ]).sorted(
            key=lambda l: l.order_id.date_order, reverse=True)
        if lines:
            # Get most recent Purchase Order Line
            last_line = lines[:1]
            price_unit = self._get_price_with_discount(
                last_line, last_line.price_unit)
            date_price_unit = last_line.order_id.date_order
            origin_price_unit = last_line.order_id.name

        return price_unit, date_price_unit, origin_price_unit

    @api.multi
    def _is_correct_price(self):
        self.ensure_one()
        price_unit, date_price_unit, origin_price_unit = self._get_current_price_unit()
        return price_unit == - self.price_unit

    @api.multi
    def _prepare_wizard_line(self):
        self.ensure_one()
        move_price_variation = False
        price_unit, date_price_unit, origin_price_unit = self._get_current_price_unit()
        current_price = - self.price_unit
        if current_price:
            move_price_variation = 100 *\
                (price_unit - current_price) / current_price
        return {
            'product_id': self.product_id.id,
            'move_id': self.id,
            'current_price': current_price,
            'current_product_price': self.product_id.standard_price,
            'new_price': price_unit,
            'date_new_price': date_price_unit,
            'origin_new_price': origin_price_unit,
            'move_price_variation': move_price_variation,
        }
