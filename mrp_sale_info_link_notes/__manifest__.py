# Copyright 2022 Sergio Corato <https://github.com/sergiocorato>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
{
    "name": "Sale notes on MRP",
    "summary": "Show info on manufacturing orders from sales order",
    "version": "14.0.1.0.0",
    "category": "Sales Management",
    "website": "https://github.com/efatto/efatto",
    "author": "Sergio Corato",
    "license": "AGPL-3",
    "application": False,
    "installable": True,
    "depends": [
        "mrp_sale_info_link",
    ],
    "data": [
        "data/calendar.xml",
        "views/sale_order.xml",
        "views/mrp_production.xml",
        "report/stock_report_deliveryslip.xml",
    ],
}
