# Copyright 2025 Sergio Corato <https://github.com/sergiocorato>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
{
    "name": "Sale Order Calendar State Rerpot",
    "version": "14.0.1.0.0",
    "summary": "Statistics from sale order calendar state",
    "license": "AGPL-3",
    "category": "Manufacturing",
    "author": "Sergio Corato",
    "website": "https://github.com/efatto/efatto",
    "depends": [
        "sale_order_calendar_state",
    ],
    "data": [
        "security/ir.model.access.csv",
        "report/sale_order_calendar_state_report.xml",
    ],
    "installable": True,
}
