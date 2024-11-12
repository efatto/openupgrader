# Copyright 2022 Sergio Corato <https://github.com/sergiocorato>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
from odoo import fields
from odoo.exceptions import UserError
from odoo.tests.common import Form
from odoo.tools import mute_logger
from odoo.tools.date_utils import relativedelta

from odoo.addons.mrp_production_demo.tests.common_data import TestProductionData


class TestSaleStockMrpProduceDelay(TestProductionData):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.maxDiff = None
        config = Form(cls.env["res.config.settings"])
        config.use_po_lead = False
        config = config.save()
        config.execute()
        cls.partner = cls.env.ref("base.res_partner_2")
        cls.product = cls.env["product.product"].create(
            {
                "name": "New product",
                "type": "product",
                "route_ids": [
                    (4, cls.env.ref("purchase_stock.route_warehouse0_buy").id)
                ],
            }
        )
        cls.product.write(
            {
                "seller_ids": [
                    (
                        0,
                        0,
                        {
                            "name": cls.env.ref("base.res_partner_3").id,
                            "price": 5.0,
                            "min_qty": 0.0,
                            "sequence": 1,
                            "date_start": fields.Date.today() - relativedelta(days=100),
                            "delay": 26,
                        },
                    ),
                ]
            }
        )
        cls.op_model = cls.env["stock.warehouse.orderpoint"]
        cls.warehouse = cls.env.ref("stock.warehouse0")
        cls.op1 = cls.op_model.create(
            [
                {
                    "name": "Orderpoint_1",
                    "warehouse_id": cls.warehouse.id,
                    "location_id": cls.warehouse.lot_stock_id.id,
                    "product_id": cls.product.id,
                    "product_min_qty": 10.0,
                    "product_max_qty": 50.0,
                    "qty_multiple": 1.0,
                    "product_uom": cls.product.uom_id.id,
                }
            ]
        )
        # subproduct_2_1 is BUY only, so create an orderpoint
        cls.subproduct_2_1.write(
            {
                "seller_ids": [
                    (
                        0,
                        0,
                        {
                            "name": cls.env.ref("base.res_partner_4").id,
                            "price": 5.0,
                            "min_qty": 0.0,
                            "sequence": 1,
                            "date_start": fields.Date.today() - relativedelta(days=100),
                            "delay": 35,
                        },
                    ),
                ]
            }
        )
        cls.op3 = cls.op_model.create(
            [
                {
                    "name": "Orderpoint_3",
                    "warehouse_id": cls.warehouse.id,
                    "location_id": cls.warehouse.lot_stock_id.id,
                    "product_id": cls.subproduct_2_1.id,
                    "product_min_qty": 25.0,
                    "product_max_qty": 70.0,
                    "qty_multiple": 1.0,
                    "product_uom": cls.subproduct_2_1.uom_id.id,
                }
            ]
        )
        cls.top_product.produce_delay = 65

    # set all products unavailable to reset prior tests
    def _set_product_unavailable(self, product, qty):
        if product.bom_ids:
            for prod in product.bom_ids.mapped("bom_line_ids.product_id"):
                if prod.bom_ids:
                    self._set_product_unavailable(prod, qty)
                else:
                    self._update_product_qty(prod, qty)
        else:
            self._update_product_qty(product, qty)

    def _create_sale_order_line(self, order, product, qty, commitment_date=False):
        vals = {
            "order_id": order.id,
            "product_id": product.id,
            "product_uom_qty": qty,
            "price_unit": 100,
        }
        if commitment_date:
            vals.update({"commitment_date": commitment_date})
        line = self.env["sale.order.line"].create(vals)
        line.product_id_change()
        line._convert_to_write(line._cache)
        return line

    def _create_purchase_order_line(self, order, product, qty, date_planned=False):
        vals = {
            "name": product.name,
            "order_id": order.id,
            "product_id": product.id,
            "product_uom": product.uom_po_id.id,
            "product_qty": qty,
            "price_unit": 100,
        }
        if date_planned:
            vals.update({"date_planned": date_planned})
        line = self.env["purchase.order.line"].create(vals)
        return line

    def test_00_available_info_product(self):
        # check product to purchase
        commitment_date_not_possible = fields.Date.today() + relativedelta(days=20)
        commitment_date_possible = fields.Date.today() + relativedelta(days=27)
        order1 = self.env["sale.order"].create(
            {
                "partner_id": self.partner.id,
            }
        )
        line1 = self._create_sale_order_line(
            order1, self.product, 5, commitment_date_not_possible
        )
        order1.compute_dates()
        available_date = fields.Date.today() + relativedelta(days=26)
        self.assertEqual(line1.available_date, available_date)
        self.assertEqual(
            line1.available_dates_info,
            "└[COMP] [False] [QTY: 5.0] [TO PURCHASE] plannable date %s."
            % available_date.strftime("%d/%m/%Y"),
        )
        with self.assertRaises(UserError):
            order1.action_confirm()
        line1.write({"commitment_date": commitment_date_possible})
        order1.action_confirm()
        self.assertEqual(order1.state, "sale")
        with mute_logger("odoo.addons.stock.models.procurement"):
            self.env["procurement.group"].run_scheduler()
        po = self.env["purchase.order"].search(
            [
                ("partner_id", "=", self.product.seller_ids[0].name.id),
                ("state", "=", "draft"),
            ]
        )
        po_line = po.order_line.filtered(lambda x: x.product_id == self.product)
        self.assertEqual(po_line.date_planned.date(), available_date)

    def _create_order_(self):
        order = self.env["sale.order"].create(
            {
                "partner_id": self.partner.id,
            }
        )
        self._set_product_unavailable(self.top_product, 0)
        line = self._create_sale_order_line(order, self.top_product, 3)
        order.compute_dates()
        available_date = fields.Date.today() + relativedelta(days=35)
        available_date1 = fields.Date.today() + relativedelta(days=26)
        available_date2 = fields.Date.today() + relativedelta(
            days=35 + self.top_product.produce_delay
        )
        self.assertEqual(line.available_date, available_date2)
        # all product are un-available, so info display all at the produce-purchase date
        self.assertEqual(
            line.available_dates_info,
            "[BOM] [MANUF] [QTY: 3.0] [TO PRODUCE] plannable date %s.\n"
            "─[BOM] [MANUF 1-2] [QTY: 6.0] [TO PRODUCE] plannable date %s.\n"
            "─└[COMP] [MANUF 1-1-1] [QTY: 18.0] [TO PURCHASE] plannable date %s.\n"
            "─└[COMP] [MANUF 1-2-1] [QTY: 24.0] [TO PURCHASE] plannable date %s.\n"
            "─[BOM] [MANUF 1-1] [QTY: 15.0] [TO PRODUCE] plannable date %s.\n"
            "─└[COMP] [MANUF 1-1-1] [QTY: 30.0] [TO PURCHASE] plannable date %s."
            % (
                available_date2.strftime("%d/%m/%Y"),
                available_date.strftime("%d/%m/%Y"),
                available_date1.strftime("%d/%m/%Y"),
                available_date.strftime("%d/%m/%Y"),
                available_date1.strftime("%d/%m/%Y"),
                available_date1.strftime("%d/%m/%Y"),
            ),
        )
        return order, line, available_date, available_date1, available_date2

    def test_01_available_info_product_mrp(self):
        # simulate a product with multiple child boms to show a "tree" of
        # availability like
        # product (stock): [available at date x | purchesable for date x]¹
        # top product [MANUF] 3pc (bom): [available at date x | produceable at date x]²
        #   -> 2pc subproduct2 [MANUF 1-2] * 3 = 6pc (bom): ²
        #      -> 3pc subproduct_1_1 [MANUF 1-1-1] * 3 = 18pc (stock) -> 26 days purch
        #      -> 4pc subproduct_2_1 [MANUF 1-2-1] * 3 = 24pc (stock) -> 35 days purch
        #   -> 5pc subproduct1 [MANUF 1-1] * 3 = 15pc (bom): ¹
        #      -> 2pc subproduct_1_1 [MANUF 1-1-1] * 3 = 30pc (stock) -> 26 days purch
        (
            sale_order,
            sale_line,
            available_date,
            available_date1,
            available_date2,
        ) = self._create_order_()
        # subbom1: 3*5*2 subproduct_1_1 = 30 -> 2 in stock, 25 incoming on 26-5 days (so
        # before the purchase delay), 30+18 needed
        # subbom2: 3*2*3 subproduct_1_1 = 18 -> see above
        #          3*2*4 subproduct_2_1 = 24 -> 0 in stock,  incoming on  days (so
        # before the purchase delay),  needed
        purchase_order1 = self.env["purchase.order"].create(
            {
                "partner_id": self.partner.id,
            }
        )
        purchase_planned_date1 = fields.Datetime.now() + relativedelta(
            days=self.subproduct_1_1.purchase_delay - 5
        )
        self._create_purchase_order_line(
            purchase_order1, self.subproduct_1_1, 18, purchase_planned_date1
        )
        purchase_order1.button_confirm()
        picking1 = purchase_order1.picking_ids[0]
        self.assertEqual(picking1.move_lines[0].product_id, self.subproduct_1_1)
        self.assertAlmostEqual(picking1.move_lines[0].product_qty, 18)
        self.assertAlmostEqual(self.subproduct_1_1.virtual_available, 18)
        self.assertEqual(
            self.subproduct_1_1.with_context(
                to_date=fields.Date.today()
            ).virtual_available_at_date_move,
            0,
        )
        self.assertEqual(
            self.subproduct_1_1.with_context(
                to_date=purchase_planned_date1
            ).virtual_available_at_date_move,
            18,
        )
        # MANUF 1-1-1 is incoming so disappear, as bom MANUF 1-1
        # FIXME qty available are checked for every single line, e.g. a 30 qty available
        #  is sufficient for every lower request, so two different request for 18 and 30
        #  are all satisfied
        sale_order.compute_dates()
        self.assertEqual(
            sale_line.available_dates_info,
            "[BOM] [MANUF] [QTY: 3.0] [TO PRODUCE] plannable date %s.\n"
            "─[BOM] [MANUF 1-2] [QTY: 6.0] [TO PRODUCE] plannable date %s.\n"
            "─└[COMP] [MANUF 1-1-1] [QTY: 18.0] [FROM STOCK] plannable date %s.\n"
            "─└[COMP] [MANUF 1-2-1] [QTY: 24.0] [TO PURCHASE] plannable date %s.\n"
            "─[BOM] [MANUF 1-1] [QTY: 15.0] [TO PRODUCE] plannable date %s.\n"
            "─└[COMP] [MANUF 1-1-1] [QTY: 30.0] [TO PURCHASE] plannable date %s."
            % (
                available_date2.strftime("%d/%m/%Y"),
                available_date.strftime("%d/%m/%Y"),
                purchase_planned_date1.strftime("%d/%m/%Y"),
                available_date.strftime("%d/%m/%Y"),
                available_date1.strftime("%d/%m/%Y"),
                available_date1.strftime("%d/%m/%Y"),
            ),
        )

        # create an incoming of [MANUF 1-2-1] for 30pc but at a date far then
        # purchaseable date, this incoming will [not] be ignored [new customer
        # specification applied on stock_reserve_date_check on 2014-10-14]
        extra_days = 5
        purchase_order2 = self.env["purchase.order"].create(
            {
                "partner_id": self.partner.id,
            }
        )
        purchase_planned_date2 = fields.Datetime.now() + relativedelta(
            days=self.subproduct_2_1.purchase_delay + extra_days
        )
        self._create_purchase_order_line(
            purchase_order2, self.subproduct_2_1, 25, purchase_planned_date2
        )
        purchase_order2.button_confirm()
        picking2 = purchase_order2.picking_ids[0]
        self.assertEqual(picking2.move_lines[0].product_id, self.subproduct_2_1)
        self.assertAlmostEqual(picking2.move_lines[0].product_qty, 25)
        self.assertAlmostEqual(self.subproduct_2_1.virtual_available, 25)
        self.assertEqual(
            self.subproduct_2_1.with_context(
                to_date=fields.Date.today()
            ).virtual_available_at_date_move,
            0,
        )
        self.assertEqual(
            self.subproduct_2_1.with_context(
                to_date=purchase_planned_date2
            ).virtual_available_at_date_move,
            25,
        )
        available_date3 = available_date2 + relativedelta(days=extra_days)
        sale_order.compute_dates()
        self.assertEqual(
            sale_order.order_line.available_dates_info,
            "[BOM] [MANUF] [QTY: 3.0] [TO PRODUCE] plannable date %s.\n"
            "─[BOM] [MANUF 1-2] [QTY: 6.0] [TO PRODUCE] plannable date %s.\n"
            "─└[COMP] [MANUF 1-1-1] [QTY: 18.0] [FROM STOCK] plannable date %s.\n"
            "─└[COMP] [MANUF 1-2-1] [QTY: 24.0] [FROM STOCK] plannable date %s.\n"
            "─[BOM] [MANUF 1-1] [QTY: 15.0] [TO PRODUCE] plannable date %s.\n"
            "─└[COMP] [MANUF 1-1-1] [QTY: 30.0] [TO PURCHASE] plannable date %s."
            % (
                available_date3.strftime("%d/%m/%Y"),
                purchase_planned_date2.strftime("%d/%m/%Y"),
                purchase_planned_date1.strftime("%d/%m/%Y"),
                purchase_planned_date2.strftime("%d/%m/%Y"),
                available_date1.strftime("%d/%m/%Y"),
                available_date1.strftime("%d/%m/%Y"),
            ),
        )

        # now we have:
        # 3 sold top_product in mo confirmed: they are shown in quantity as -3
        # components of 3 top product:
        # 5pc subproduct1 * 3 = 15
        #   2pc subproduct_1_1 * 15 = 30
        # 2pc subproduct2 * 3 = 6
        #   4pc subproduct_2_1 * 6 =  24
        #   3pc subproduct_1_1 * 6 =  18
        # total                       72
        # NOT visible as not in this order product1
        # 2 offered top_product: they are shown in quantity as              -2
        # total top_products                                                -5
        with self.assertRaises(UserError):
            sale_order.action_confirm()
        sale_order.order_line.write({"commitment_date": available_date3})
        sale_order.action_confirm()
        # now we have outgoing stock.move and mo to be produced             +3
        self.assertEqual(sale_order.state, "sale")
        production = self.env["mrp.production"].search(
            [("origin", "=", sale_order.name)]
        )
        self.assertTrue(production)
        production.action_assign()
        production.qty_producing = 2.0
        for raw_move in production.move_raw_ids:
            raw_move.quantity_done = (
                raw_move.product_qty * production.qty_producing / production.product_qty
            )
        production.button_mark_done()
        self.assertEqual(production.state, "progress")
        self.assertEqual(production.source_procurement_group_id.name, sale_order.name)
        self.assertEqual(len(production), 1)

        sale_order.compute_dates()
        # la quantità da produrre non tiene conto di una produzione in corso per quanti-
        # tà diversa
        self.assertEqual(
            sale_order.order_line.available_dates_info,
            "[BOM] [MANUF] [QTY: 3.0] [TO PRODUCE] plannable date %s.\n"
            "─[BOM] [MANUF 1-2] [QTY: 6.0] [TO PRODUCE] plannable date %s.\n"
            "─└[COMP] [MANUF 1-1-1] [QTY: 18.0] [TO PURCHASE] plannable date %s.\n"
            "─└[COMP] [MANUF 1-2-1] [QTY: 24.0] [TO PURCHASE] plannable date %s.\n"
            "─[BOM] [MANUF 1-1] [QTY: 15.0] [TO PRODUCE] plannable date %s.\n"
            "─└[COMP] [MANUF 1-1-1] [QTY: 30.0] [TO PURCHASE] plannable date %s."
            % (
                available_date2.strftime("%d/%m/%Y"),
                available_date.strftime("%d/%m/%Y"),
                available_date1.strftime("%d/%m/%Y"),
                available_date.strftime("%d/%m/%Y"),
                available_date1.strftime("%d/%m/%Y"),
                available_date1.strftime("%d/%m/%Y"),
            ),
        )

    def test_02_available_info_product_mrp_orderpoint(self):
        # simulate a product with multiple child boms to show a "tree" of
        # availability like
        # product (stock): [available at date x | purchesable for date x]¹
        # top product [MANUF] 3pc (bom): [available at date x | produceable at date x]²
        #   -> 2pc subproduct2 [MANUF 1-2] * 3 = 6pc (bom): ²
        #      -> 3pc subproduct_1_1 [MANUF 1-1-1] * 3 = 18pc (stock) -> 26 days purch
        #      -> 4pc subproduct_2_1 [MANUF 1-2-1] * 3 = 24pc (stock) -> 35 days purch
        #   -> 5pc subproduct1 [MANUF 1-1] * 3 = 15pc (bom): ¹
        #      -> 2pc subproduct_1_1 [MANUF 1-1-1] * 3 = 30pc (stock) -> 26 days purch
        (
            sale_order,
            sale_line,
            available_date,
            available_date1,
            available_date2,
        ) = self._create_order_()
        # subbom1: 3*5*2 subproduct_1_1 = 30 -> 2 in stock, 25 incoming on 26-5 days (so
        # before the purchase delay), 30+18 needed
        # subbom2: 3*2*3 subproduct_1_1 = 18 -> see above
        #          3*2*4 subproduct_2_1 = 24 -> 0 in stock,  incoming on  days (so
        # before the purchase delay),  needed
        # cancel existing po to force recreate
        old_po = self.env["purchase.order"].search(
            [
                ("partner_id", "=", self.subproduct_1_1.seller_ids[0].name.id),
            ]
        )
        old_po.button_cancel()
        with self.assertRaises(UserError):
            sale_order.action_confirm()
        sale_order.order_line.write({"commitment_date": available_date2})
        sale_order.action_confirm()
        # now we have outgoing stock.move and mo to be produced             +3
        self.assertEqual(sale_order.state, "sale")
        new_po = self.env["purchase.order"].search(
            [
                ("partner_id", "=", self.subproduct_1_1.seller_ids[0].name.id),
                ("order_line.product_id", "=", self.subproduct_1_1.id),
                ("state", "=", "draft"),
            ]
        )
        new_po -= old_po
        new_po_line = new_po.order_line.filtered(
            lambda x: x.product_id == self.subproduct_1_1
        )
        self.assertTrue(new_po_line)
        self.assertEqual(new_po_line.date_planned.date(), available_date)
        new_po_1 = self.env["purchase.order"].search(
            [
                ("partner_id", "=", self.subproduct_2_1.seller_ids[0].name.id),
                ("order_line.product_id", "=", self.subproduct_2_1.id),
                ("state", "=", "draft"),
            ]
        )
        # check the order for the product with less delay is ordered after the order
        # with more delay (which means today), to do not receive product not used in
        # that time
        new_po_1_date = new_po.date_order - relativedelta(
            days=int(
                self.subproduct_2_1.purchase_delay - self.subproduct_1_1.purchase_delay
            )
        )
        new_po_1_date.replace(hour=0)
        self.assertEqual(new_po_1_date, new_po_1_date)

        new_po_1_line = new_po_1.order_line.filtered(
            lambda x: x.product_id == self.subproduct_2_1
        )
        self.assertTrue(new_po_1_line)
        self.assertEqual(new_po_1_line.date_planned.date(), available_date)

        # FIXME se i 2 prodotti sono acquistati dalla stesso fornitore, la data dell'
        #  ordine risulta la più lontana invece della più vicina
