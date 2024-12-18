# Copyright 2013 Stefano Siccardi creativiquadrati snc
# Copyright 2020 Sergio Corato <https://github.com/sergiocorato>
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).
{
    "name": "Sale calendar state",
    "author": "Sergio Corato",
    "website": "https://github.com/efatto/efatto",
    "license": "AGPL-3",
    "version": "14.0.1.0.1",
    "summary": "Add states to sale order based on manufacturing, deliveries and "
    "purchase, used in calendar view.",
    "depends": [
        "connector_whs",
        "delivery",
        "l10n_it_delivery_note",
        "mrp_production_demo",
        "mrp_sale_info_link_notes",
        "partner_priority",
        "product_is_kit",
        "purchase_line_procurement_group",
        "sale_force_invoiced",
        "sale_order_line_date",
        "sale_procurement_group_by_line",
        "sale_stock",
        "sale_order_line_date",
        "web",
    ],
    "data": [
        "security/ir.model.access.csv",
        "data/cron.xml",
        "wizard/mark_blocked.xml",
        "views/assets.xml",
        "views/orders_view.xml",
        "views/mrp_view.xml",
        "views/picking_view.xml",
        "views/sale_data.xml",
    ],
    # "qweb": ["static/src/xml/web_calendar.xml"],
    "installable": True,
}
