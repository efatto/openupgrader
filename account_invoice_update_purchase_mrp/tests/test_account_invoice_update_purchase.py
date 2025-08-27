from odoo import fields
from odoo.tests import Form
from odoo.tools import float_round, mute_logger

from odoo.addons.mrp_production_demo.tests.common_data import TestProductionData


class TestAccountInvoiceUpdatePurchaseMrp(TestProductionData):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.vendor_1 = cls.env.ref("base.res_partner_4")
        cls.warehouse = cls.env.ref("stock.warehouse0")
        route_mto = cls.warehouse.mto_pull_id.route_id
        cls.warehouse.mto_pull_id.route_id.active = True
        supplierinfo_1 = cls.env["product.supplierinfo"].create(
            {
                "name": cls.vendor_1.id,
                "price": 50.0,
                "currency_id": cls.env.ref("base.EUR").id,
            }
        )
        cls.product_to_purchase_3 = cls.env["product.product"].create(
            [
                {
                    "name": "Additional component product 3",
                    "type": "product",
                    "default_code": "ADDCOMP3",
                    "purchase_ok": True,
                    "route_ids": [
                        (4, cls.env.ref("purchase_stock.route_warehouse0_buy").id),
                        (4, route_mto.id),
                    ],
                    "seller_ids": [(4, supplierinfo_1.id)],
                }
            ]
        )
        cls.vendor = cls.env.ref("base.res_partner_3")
        supplierinfo_form = Form(cls.env["product.supplierinfo"])
        supplierinfo_form.name = cls.vendor
        supplierinfo_form.price = 100.0
        supplierinfo_form.currency_id = cls.env.ref("base.EUR")
        supplierinfo = supplierinfo_form.save()
        product_to_purchase_form = Form(cls.env["product.product"])
        product_to_purchase_form.name = "Component product to purchase manually"
        product_to_purchase_form.default_code = "COMPPURCHMANU"
        product_to_purchase_form.standard_price = 60.0
        product_to_purchase_form.type = "product"
        product_to_purchase_form.purchase_ok = True
        cls.product_to_purchase = product_to_purchase_form.save()
        cls.product_to_purchase.write(
            {
                "route_ids": [
                    (4, cls.env.ref("purchase_stock.route_warehouse0_buy").id),
                    (4, cls.env.ref("stock.route_warehouse0_mto").id),
                ],
                "seller_ids": [(4, supplierinfo.id)],
            }
        )
        main_bom_form = Form(cls.main_bom)
        with main_bom_form.bom_line_ids.new() as main_bom_line:
            main_bom_line.product_id = cls.product_to_purchase
            main_bom_line.product_qty = 7
            main_bom_line.product_uom_id = cls.product_to_purchase.uom_id
        main_bom_form.save()
        cls.product_qty = 5
        cls.main_bom.operation_ids = cls.operation1
        # put only product_id and product_qty in the wizard data to avoid the default
        # setting of product_qty to 1
        man_order_form = Form(cls.env["mrp.production"])
        man_order_form.product_id = cls.top_product
        man_order_form.product_qty = cls.product_qty
        cls.man_order = man_order_form.save()
        cls.man_order.action_confirm()

    def _start_wizard(self, man_order):
        wizard_data = man_order.check_raw_moves_price_unit()
        update_price_form = Form(
            self.env["mrp.sync.price"].with_context(wizard_data["context"])
        )
        update_price_wizard = update_price_form.save()
        update_price_wizard.update_price_unit()

    def test_01_mo_purchase_invoice_after_done(self):
        # 1. check that the price_unit for move_raw_ids is equal to the purchase cost
        # from the invoice vendor if cost valuation is standard, else is average if it's
        # average
        # 2. check that the procurement has created an RDP
        self.assertTrue(self.man_order)
        self.assertEqual(self.man_order.product_qty, self.product_qty)
        with mute_logger("odoo.addons.stock.models.procurement"):
            self.procurement_model.run_scheduler()
        po_ids = self.env["purchase.order"].search(
            [
                ("origin", "=", self.man_order.name),
                ("state", "=", "draft"),
            ]
        )
        self.assertTrue(po_ids)
        self.assertEqual(len(po_ids), 1)
        po = po_ids[0]
        po_lines = po.order_line.filtered(
            lambda x: x.product_id == self.product_to_purchase
        )
        self.assertEqual(
            sum(po_line.product_qty for po_line in po_lines), 7 * self.product_qty
        )
        self.assertEqual(len(po_lines), 1)
        po_line = po_lines[0]
        self.assertAlmostEqual(
            po_line.price_unit,
            po_line.currency_id._convert(
                po_line.price_unit, po.currency_id, po.company_id, po.date_order
            ),
        )
        # change po_line price and discount
        po_line.price_unit = 67.88
        po_line.discount = 15.0
        # confirm purchase order
        po.button_confirm()
        # complete purchase
        picking = po.picking_ids[0]
        picking.action_confirm()
        for move_line in picking.move_lines:
            move_line.write({"quantity_done": move_line.product_uom_qty})
        picking.button_validate()
        self.assertEqual(picking.state, "done")
        # create workorder to add relative costs
        self.man_order.action_assign()
        self.man_order.button_plan()
        # produce partially
        produce_form = Form(self.man_order)
        produced_qty = 2.0
        produce_form.qty_producing = produced_qty
        produce_form.save()
        self.man_order.action_confirm()

        # check stock_move's price_unit of components is 0
        mo_raw_moves = self.man_order.move_raw_ids.filtered(
            lambda x: x.product_id == self.product_to_purchase
        )
        self.assertEqual(len(mo_raw_moves), 1)
        # mo_move = mo_raw_moves[0]
        # note: price is set negative when stock move is done
        # self.assertAlmostEqual(mo_move.price_unit, 60.0) # fixme? price is 0

        # aggiungere delle righe extra-bom, in stato confermato come da ui
        self.man_order.action_toggle_is_locked()
        self.man_order.write(
            {
                "move_raw_ids": [
                    (
                        0,
                        0,
                        {
                            "name": self.product_to_purchase_3.name,
                            "product_id": self.product_to_purchase_3.id,
                            "product_uom": self.product_to_purchase_3.uom_id.id,
                            "product_uom_qty": 10,
                            "location_id": self.man_order.location_src_id.id,
                            "location_dest_id": self.man_order.location_dest_id.id,
                            "state": "confirmed",
                            "raw_material_production_id": self.man_order.id,
                            "picking_type_id": self.man_order.picking_type_id.id,
                        },
                    ),
                ]
            }
        )
        self.man_order.action_toggle_is_locked()
        move_raw = self.man_order.move_raw_ids.filtered(
            lambda x: x.product_id == self.product_to_purchase_3
        )
        # complete production
        move_raw.write({"quantity_done": 3})
        self.assertEqual(move_raw.quantity_done, 3)
        action = self.man_order.button_mark_done()
        consumption = Form(
            self.env["mrp.consumption.warning"].with_context(**action["context"])
        )
        warning = consumption.save()
        new_action = warning.action_confirm()
        backorder = Form(
            self.env["mrp.production.backorder"].with_context(**new_action["context"])
        )
        backorder.save().action_backorder()
        self.assertEqual(self.man_order.state, "done")
        # todo the residual production is done in a backorder, how is it shown in
        #  the statistics?
        # produce_form.product_qty = 3.0
        # produced_qty += produce_form.product_qty
        # wizard_1 = produce_form.save()
        # wizard_1.do_produce()
        # check price_unit of mo raw move is equal to product standard price
        mo_raw_moves = self.man_order.move_raw_ids.filtered(
            lambda x: x.product_id == self.product_to_purchase
        )
        self.assertEqual(len(mo_raw_moves), 1)
        # mo_move = mo_raw_moves[0]
        # note: price is set negative when stock move is done
        # self.assertAlmostEqual(mo_move.price_unit, -60.0) # fixme price is 0

        # start wizard to update stock move price
        self._start_wizard(self.man_order)

        # check price_unit of mo raw move is equal to po line
        mo_raw_moves = self.man_order.move_raw_ids.filtered(
            lambda x: x.product_id == self.product_to_purchase
        )
        self.assertEqual(len(mo_raw_moves), 1)
        mo_move = mo_raw_moves[0]
        po_price = float_round(
            po_line.price_unit * (1 - po_line.discount / 100.0),
            self.env["decimal.precision"].precision_get("Product Price"),
        )
        self.assertAlmostEqual(-mo_move.price_unit, po_price)
        # invoice the purchase order with a different price
        purchase_invoice_form = Form(
            self.env["account.move"].with_context(default_move_type="in_invoice")
        )
        purchase_invoice_form.partner_id = po.partner_id
        purchase_invoice_form.purchase_id = po
        purchase_invoice_form.invoice_date = fields.Date.today()
        purchase_invoice = purchase_invoice_form.save()
        purchase_invoice._onchange_purchase_auto_complete()
        invoice_line = purchase_invoice.invoice_line_ids.filtered(
            lambda x: x.product_id == self.product_to_purchase
        )
        self.assertAlmostEqual(invoice_line.price_unit, po_line.price_unit)
        self.assertAlmostEqual(invoice_line.discount, po_line.discount)
        invoice_form = Form(purchase_invoice)
        for i, _l in enumerate(purchase_invoice.invoice_line_ids):
            with invoice_form.invoice_line_ids.edit(i) as invoice_line_form:
                if invoice_line_form.product_id == self.product_to_purchase:
                    invoice_line_form.price_unit = 90.0
                    invoice_line_form.discount = 20.0
        invoice_form.save()
        for inv_line in purchase_invoice.invoice_line_ids:
            self.assertEqual(len(inv_line.tax_ids), 1)
        purchase_invoice.action_post()
        self.assertEqual(purchase_invoice.state, "posted")
        self.assertAlmostEqual(invoice_line.price_unit, 90)
        self.assertAlmostEqual(invoice_line.discount, 20)
        # re-start wizard to update to new price
        self._start_wizard(self.man_order)
        # check move is updated with new price
        mo_raw_moves = self.man_order.move_raw_ids.filtered(
            lambda x: x.product_id == self.product_to_purchase
        )
        self.assertEqual(len(mo_raw_moves), 1)
        mo_move = mo_raw_moves[0]
        invoice_price = float_round(
            invoice_line.price_unit * (1 - invoice_line.discount / 100.0),
            self.env["decimal.precision"].precision_get("Product Price"),
        )
        self.assertAlmostEqual(-mo_move.price_unit, invoice_price)

    def test_02_mo_purchase_invoice_simple(self):
        # complete the production
        self.man_order.action_assign()
        self.man_order.button_plan()
        produce_form = Form(self.man_order)
        produce_form.qty_producing = 5.0
        self.man_order = produce_form.save()
        self.man_order.action_confirm()
        # create directly a purchase invoice for the product
        other_account_type = self.env["account.account.type"].search(
            [("type", "=", "other")], limit=1
        )
        new_purchase_invoice_form = Form(
            self.env["account.move"].with_context(default_move_type="in_invoice")
        )
        new_purchase_invoice_form.partner_id = self.vendor
        new_purchase_invoice_form.invoice_date = fields.Date.today()
        new_purchase_invoice_form.journal_id = self.env["account.journal"].search(
            [("type", "=", "purchase")], limit=1
        )
        with new_purchase_invoice_form.invoice_line_ids.new() as invoice_line_form:
            invoice_line_form.name = self.product_to_purchase.name
            invoice_line_form.product_id = self.product_to_purchase
            invoice_line_form.account_id = self.env["account.account"].search(
                [("user_type_id", "=", other_account_type.id)],
                limit=1,
            )
            invoice_line_form.quantity = 1
            invoice_line_form.price_unit = 100
            invoice_line_form.discount = 8
        new_purchase_invoice = new_purchase_invoice_form.save()
        for inv_line in new_purchase_invoice.invoice_line_ids:
            self.assertEqual(len(inv_line.tax_ids), 1)
        new_purchase_invoice.action_post()
        self.assertEqual(new_purchase_invoice.state, "posted")
        # re-start the wizard to update to the new price
        self._start_wizard(self.man_order)
        # check move is updated with the new price
        mo_raw_moves = self.man_order.move_raw_ids.filtered(
            lambda x: x.product_id == self.product_to_purchase
        )
        self.assertEqual(len(mo_raw_moves), 1)
        mo_move = mo_raw_moves[0]
        invoice_price = float_round(
            100 * (1 - 8 / 100.0),
            self.env["decimal.precision"].precision_get("Product Price"),
        )
        self.assertAlmostEqual(-mo_move.price_unit, invoice_price)
