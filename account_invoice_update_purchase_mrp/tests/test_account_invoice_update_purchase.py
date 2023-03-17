# Copyright 2022 Sergio Corato <https://github.com/sergiocorato>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl)
from odoo.addons.mrp_production_demo.tests.common_data import TestProductionData
from odoo.tools import mute_logger, float_round
from odoo.tests import Form


class TestAccountInvoiceUpdatePurchaseMrp(TestProductionData):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.vendor_1 = cls.env.ref('base.res_partner_4')
        supplierinfo_1 = cls.env['product.supplierinfo'].create({
            'name': cls.vendor_1.id,
            'price': 50.0,
            'currency_id': cls.env.ref('base.EUR').id,
        })
        cls.product_to_purchase_3 = cls.env['product.product'].create([{
            'name': 'Additional component product 3',
            'type': 'product',
            'default_code': 'ADDCOMP3',
            'purchase_ok': True,
            'route_ids': [
                (4, cls.env.ref('purchase_stock.route_warehouse0_buy').id),
                (4, cls.env.ref('stock.route_warehouse0_mto').id)],
            'seller_ids': [(6, 0, [supplierinfo_1.id])],
        }])
        cls.vendor = cls.env.ref('base.res_partner_3')
        supplierinfo = cls.env['product.supplierinfo'].create({
            'name': cls.vendor.id,
            'price': 100.0,
            'currency_id': cls.env.ref('base.EUR').id,
        })
        cls.product_to_purchase = cls.env['product.product'].create([{
            'name': 'Component product to purchase manually',
            'default_code': 'COMPPURCHMANU',
            'standard_price': 60.0,
            'type': 'product',
            'purchase_ok': True,
            'route_ids': [
                (4, cls.env.ref('purchase_stock.route_warehouse0_buy').id),
                (4, cls.env.ref('stock.route_warehouse0_mto').id)],
            'seller_ids': [(6, 0, [supplierinfo.id])],
        }])
        cls.main_bom.write({
            'bom_line_ids': [
                (0, 0, {
                    'product_id': cls.product_to_purchase.id,
                    'product_qty': 7,
                    'product_uom_id': cls.product_to_purchase.uom_id.id,
                })
            ]
        })
        cls.product_qty = 5
        cls.main_bom.routing_id = cls.routing1
        cls.man_order = cls.env['mrp.production'].create({
            'name': 'MO-Test',
            'product_id': cls.top_product.id,
            'product_uom_id': cls.top_product.uom_id.id,
            'product_qty': cls.product_qty,
            'bom_id': cls.main_bom.id,
        })

    def _start_wizard(self, man_order):
        wizard_data = man_order.check_raw_moves_price_unit()
        update_price_form = Form(
            self.env['mrp.sync.price'].with_context(
                wizard_data['context']
            )
        )
        update_price_wizard = update_price_form.save()
        update_price_wizard.update_price_unit()

    def test_01_mo_purchase_invoice_after_done(self):
        # check that price_unit for move_raw_ids is equal to purchase cost from invoice
        # vendor, if cost valuation is standard, and average if average
        # check procurement has created RDP
        with mute_logger('odoo.addons.stock.models.procurement'):
            self.procurement_model.run_scheduler()
        po_ids = self.env['purchase.order'].search([
            ('origin', '=', self.man_order.name),
            ('state', '=', 'draft'),
        ])
        self.assertTrue(po_ids)
        self.assertEqual(len(po_ids), 1)
        po = po_ids[0]
        po_lines = po.order_line.filtered(
            lambda x: x.product_id == self.product_to_purchase)
        self.assertEqual(sum(po_line.product_qty for po_line in po_lines),
                         7 * self.product_qty)
        self.assertEqual(len(po_lines), 1)
        po_line = po_lines[0]
        self.assertAlmostEqual(po_line.price_unit, 100.0)
        # change po_line price and discount
        po_line.price_unit = 67.88
        po_line.discount = 15.0
        # confirm purchase order
        po.button_confirm()
        # complete purchase
        picking = po.picking_ids[0]
        picking.action_confirm()
        for move_line in picking.move_lines:
            move_line.write({'quantity_done': move_line.product_uom_qty})
        picking.button_validate()
        self.assertEqual(picking.state, 'done')
        # create workorder to add relative costs
        self.man_order.action_assign()
        self.man_order.button_plan()
        # produce partially
        produce_form = Form(
            self.env['mrp.product.produce'].with_context(
                active_id=self.man_order.id,
                active_ids=[self.man_order.id],
            )
        )
        produced_qty = 2.0
        produce_form.product_qty = produced_qty
        wizard = produce_form.save()
        wizard.do_produce()

        # check price_unit of stock_move of components is 0
        mo_raw_moves = self.man_order.move_raw_ids.filtered(
            lambda x: x.product_id == self.product_to_purchase)
        self.assertEqual(len(mo_raw_moves), 1)
        mo_move = mo_raw_moves[0]
        # note: price is set negative when stock move is done
        self.assertAlmostEqual(mo_move.price_unit, 60.0)

        # aggiungere delle righe extra-bom, in stato confermato come da ui
        self.man_order.action_toggle_is_locked()
        self.man_order.write({
            'move_raw_ids': [
                (0, 0, {
                    'name': self.product_to_purchase_3.name,
                    'product_id': self.product_to_purchase_3.id,
                    'product_uom': self.product_to_purchase_3.uom_id.id,
                    'product_uom_qty': 10,
                    'location_id': self.man_order.location_src_id.id,
                    'location_dest_id': self.man_order.location_dest_id.id,
                    'state': 'confirmed',
                    'raw_material_production_id': self.man_order.id,
                    'picking_type_id': self.man_order.picking_type_id.id,
                }),
            ]
        })
        self.man_order.action_toggle_is_locked()
        move_raw = self.man_order.move_raw_ids.filtered(
            lambda x: x.product_id == self.product_to_purchase_3
        )
        # complete production
        move_raw.write({'quantity_done': 3})
        self.assertEqual(move_raw.quantity_done, 3)
        produce_form.product_qty = 3.0
        produced_qty += produce_form.product_qty
        wizard_1 = produce_form.save()
        wizard_1.do_produce()
        self.man_order.button_mark_done()
        self.assertEqual(self.man_order.state, 'done')

        # check price_unit of mo raw move is equal to product standard price
        mo_raw_moves = self.man_order.move_raw_ids.filtered(
            lambda x: x.product_id == self.product_to_purchase)
        self.assertEqual(len(mo_raw_moves), 1)
        mo_move = mo_raw_moves[0]
        # note: price is set negative when stock move is done
        self.assertAlmostEqual(mo_move.price_unit, - 60.0)

        # start wizard to update stock move price
        self._start_wizard(self.man_order)

        # check price_unit of mo raw move is equal to po line
        mo_raw_moves = self.man_order.move_raw_ids.filtered(
            lambda x: x.product_id == self.product_to_purchase)
        self.assertEqual(len(mo_raw_moves), 1)
        mo_move = mo_raw_moves[0]
        po_price = float_round(
            po_line.price_unit * (1 - po_line.discount / 100.0),
            self.env['decimal.precision'].precision_get('Product Price')
        )
        self.assertAlmostEqual(mo_move.price_unit, po_price)
        # invoice the purchase order with a different price
        purchase_invoice = self.env['account.invoice'].create({
            'partner_id': po.partner_id.id,
            'purchase_id': po.id,
            'account_id': po.partner_id.property_account_payable_id.id,
            'type': 'in_invoice',
        })
        purchase_invoice.purchase_order_change()
        invoice_line = purchase_invoice.invoice_line_ids.filtered(
            lambda x: x.product_id == self.product_to_purchase
        )
        self.assertAlmostEqual(invoice_line.price_unit, po_line.price_unit)
        self.assertAlmostEqual(invoice_line.discount, po_line.discount)
        invoice_line.write({
            'price_unit': 90.0,
            'discount': 20.0,
        })
        purchase_invoice.action_invoice_open()
        self.assertEqual(purchase_invoice.state, 'open')
        self.assertAlmostEqual(invoice_line.price_unit, 90)
        self.assertAlmostEqual(invoice_line.discount, 20)
        # re-start wizard to update to new price
        self._start_wizard(self.man_order)
        # check move is updated with new price
        mo_raw_moves = self.man_order.move_raw_ids.filtered(
            lambda x: x.product_id == self.product_to_purchase)
        self.assertEqual(len(mo_raw_moves), 1)
        mo_move = mo_raw_moves[0]
        invoice_price = float_round(
            invoice_line.price_unit * (1 - invoice_line.discount / 100.0),
            self.env['decimal.precision'].precision_get('Product Price')
        )
        self.assertAlmostEqual(- mo_move.price_unit, invoice_price)

    def test_02_mo_purchase_invoice_simple(self):
        # complete production
        self.man_order.action_assign()
        self.man_order.button_plan()
        produce_form = Form(
            self.env['mrp.product.produce'].with_context(
                active_id=self.man_order.id,
                active_ids=[self.man_order.id],
            )
        )
        produced_qty = 5.0
        produce_form.product_qty = produced_qty
        wizard = produce_form.save()
        wizard.do_produce()
        # create directly a purchase invoice for the product
        other_account_type = self.env['account.account.type'].search([
            ('type', '=', 'other')
        ], limit=1)
        new_purchase_invoice = self.env['account.invoice'].create([{
            'type': 'in_invoice',
            'partner_id': self.vendor.id,
            'account_id': self.vendor.property_account_payable_id.id,
            'journal_id': self.env['account.journal'].search([
                ('type', '=', 'purchase')], limit=1).id,
            'invoice_line_ids': [
                (0, 0, {
                    'name': self.product_to_purchase.name,
                    'product_id': self.product_to_purchase.id,
                    'account_id': self.env['account.account'].search([
                        ('user_type_id', '=', other_account_type.id)
                    ], limit=1).id,
                    'quantity': 1,
                    'price_unit': 100,
                    'discount': 8,
                })
            ]
        }])
        new_purchase_invoice.action_invoice_open()
        self.assertEqual(new_purchase_invoice.state, 'open')
        # re-start wizard to update to new price
        self._start_wizard(self.man_order)
        # check move is updated with new price
        mo_raw_moves = self.man_order.move_raw_ids.filtered(
            lambda x: x.product_id == self.product_to_purchase)
        self.assertEqual(len(mo_raw_moves), 1)
        mo_move = mo_raw_moves[0]
        invoice_price = float_round(
            100 * (1 - 8 / 100.0),
            self.env['decimal.precision'].precision_get('Product Price')
        )
        self.assertAlmostEqual(- mo_move.price_unit, invoice_price)
