from odoo import fields, models


class ProductProduct(models.Model):
    _inherit = "product.product"

    def _compute_historic_sale_quantities_dict(
        self, location_id=False, from_date=False, to_date=False, compute_on_sale=False
    ):
        product_moves_dict = super()._compute_historic_sale_quantities_dict(
            location_id=location_id,
            from_date=from_date,
            to_date=to_date,
            compute_on_sale=compute_on_sale,
        )
        lead_obj = self.env["crm.lead"]
        domain_lead = [
            ("product_id", "in", self.ids),
        ]
        leads = lead_obj.search_read(
            domain_lead,
            ["product_id", "lead_product_qty", "date_deadline", "create_date"],
            order="date_deadline asc",
        )
        for lead in leads:
            product_id = lead["product_id"][0]
            lead_product_qty = lead["lead_product_qty"]
            date_deadline = fields.Datetime.to_datetime(
                lead["date_deadline"] or lead["create_date"]
            )
            # If no there are no moves for a product we default the stock
            # to the one for the given period nevermind the dates
            if len(
                product_moves_dict[product_id]["stock_history"]
            ) == 1 and product_moves_dict[product_id]["stock_history"] == [0.0]:
                # replace initial void value
                product_moves_dict[product_id] = {
                    date_deadline: {
                        "prod_qty": lead_product_qty,
                        "stock": lead_product_qty,
                    },
                    "stock_history": [0.0],
                    "move_history": [lead_product_qty],
                }
            else:
                # add to other values
                product_moves_dict[product_id].update(
                    {
                        date_deadline: {
                            "prod_qty": lead_product_qty,
                            "stock": lead_product_qty
                            + product_moves_dict[product_id]["stock_history"][-1],
                        }
                    }
                )
                product_moves_dict[product_id]["move_history"] += [lead_product_qty]
            product_moves_dict[product_id]["stock_history"] += [
                lead_product_qty + product_moves_dict[product_id]["stock_history"][-1]
            ]
        return product_moves_dict
