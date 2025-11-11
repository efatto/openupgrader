from collections import OrderedDict
from datetime import timedelta

from odoo import fields, models


class ProductTemplate(models.Model):
    _inherit = "product.template"

    orderpoint_generate_active = fields.Boolean(default=True)


class ProductProduct(models.Model):
    _inherit = "product.product"

    def _get_purchase_delay(self, purchase_delay=0):
        if self.env.ref("purchase_stock.route_warehouse0_buy") in self.route_ids:
            purchase_delay += self.purchase_delay
            if self.seller_ids:
                if self.seller_ids[0].overtime_purchase_delay:
                    overtime_purchase_delay = self.seller_ids[0].overtime_purchase_delay
                    purchase_delay += overtime_purchase_delay
        if (
            self.env.ref("mrp.route_warehouse0_manufacture") in self.route_ids
            and self.bom_ids
        ):
            bom_purchase_delay = max(
                [
                    p._get_purchase_delay(purchase_delay)
                    for p in self.bom_ids.bom_line_ids.mapped("product_id")
                ]
                or [0]
            )
            purchase_delay += bom_purchase_delay
        return purchase_delay

    def _get_produce_delay(self, produce_delay=0):
        if self.env.ref("mrp.route_warehouse0_manufacture") in self.route_ids:
            produce_delay += self.produce_delay
            if self.bom_ids:
                bom_produce_delay = max(
                    [
                        p._get_produce_delay(produce_delay)
                        for p in self.bom_ids.bom_line_ids.mapped("product_id")
                    ]
                    or [0]
                )
                produce_delay += bom_produce_delay
        return produce_delay

    def _compute_historic_sale_quantities_dict(
        self, location_id=False, from_date=False, to_date=False, compute_on_sale=False
    ):
        """
        Returns a dict of products with a dict of historic moves as for
        a list of historic stock values resulting from those moves. If
        a location_id is passed, we can restrict it to such location.
        With compute_on_sale=True: restrict search only on moves towards customers
        """
        # Search only stock.move not 'draft' nor 'cancel'
        location = location_id and location_id.id
        domain_quant_loc, domain_move_in_loc, domain_move_out_loc = self.with_context(
            location=location
        )._get_domain_locations()
        if compute_on_sale:
            domain_move_out_loc += [
                ("location_dest_id.usage", "=", "customer"),
            ]
        if not to_date:
            to_date = fields.Datetime.now()
        domain_move_out = [
            ("product_id", "in", self.ids),
            ("state", "not in", ["draft", "cancel"]),
            ("location_id", "child_of", location),
            ("location_id.usage", "!=", "inventory"),
            ("location_dest_id.usage", "!=", "inventory"),
        ] + domain_move_out_loc
        if from_date:
            domain_move_out += [("date", ">=", from_date)]
        domain_move_out += [("date", "<=", to_date)]
        move_obj = self.env["stock.move"]
        moves = move_obj.search_read(
            domain_move_out, ["product_id", "product_qty", "date"], order="date asc"
        )
        # Obtain a dict with the stock snapshot for the relative date_from
        # otherwise, the first move will counted as first stock value. We
        # default the compute the stock value anyway to default the value
        # for products with no moves for the given period
        # Compute the second before the given date so we don't duplicate
        # history values in case the given hour is the same than the one
        # of the first move
        from_date_stock = from_date - timedelta(seconds=1)
        to_date_stock = to_date + timedelta(seconds=1)
        initial_stock = self.with_context(location=location)._compute_quantities_dict(
            False, False, False, to_date=from_date_stock or to_date_stock
        )
        product_moves_dict = {}
        for move in moves:
            if product_moves_dict.get(
                move["product_id"][0], False
            ) and product_moves_dict[move["product_id"][0]].get(move["date"], False):
                product_moves_dict[move["product_id"][0]].update(
                    {
                        move["date"]: {
                            "prod_qty": move["product_qty"]
                            + product_moves_dict[move["product_id"][0]].get(
                                move["date"]
                            )["prod_qty"],
                        }
                    }
                )
            else:
                product_moves_dict.setdefault(move["product_id"][0], {})
                product_moves_dict[move["product_id"][0]].update(
                    {
                        move["date"]: {
                            "prod_qty": move["product_qty"],
                        }
                    }
                )
        for product in self.with_context(prefetch_fields=False):
            # If no there are no moves for a product we default the stock
            # to the one for the given period nevermind the dates
            product_moves = product_moves_dict.get(product.id)
            prod_initial_stock = initial_stock.get(product.id, {})
            if not product_moves:
                product_moves_dict[product.id] = {
                    to_date: {
                        "prod_qty": 0,
                        "stock": prod_initial_stock.get("qty_available", 0),
                    },
                    "stock_history": [prod_initial_stock.get("qty_available", 0)],
                    "move_history": [],
                }
                continue
            # Now we'll sort the moves by date and assign an initial stock so
            # we can compute the stock historical values from the moves
            # sequence so we can exploit it statisticaly
            product_moves = OrderedDict(sorted(product_moves.items()))
            product_moves_dict[product.id]["stock_history"] = [
                prod_initial_stock.get("qty_available", 0)
            ]
            product_moves_dict[product.id]["move_history"] = []
            stock = 0
            first_item = product_moves[next(iter(product_moves))]
            if from_date:
                stock = prod_initial_stock.get("qty_available")
            first_item["stock"] = stock + first_item["prod_qty"]
            stock = first_item["stock"]
            iter_moves = iter(product_moves)
            next(iter_moves, None)
            for date in iter_moves:
                stock += product_moves[date]["prod_qty"]
                product_moves[date]["stock"] = stock
            product_moves_dict[product.id]["stock_history"] += [
                v["stock"] for k, v in product_moves.items()
            ]
            product_moves_dict[product.id]["move_history"] += [
                v["prod_qty"] for k, v in product_moves.items()
            ]
        return product_moves_dict
