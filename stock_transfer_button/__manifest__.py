# Copyright 2022 Sergio Corato <https://github.com/sergiocorato>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
{
    'name': 'Stock transfer button in dashboard',
    'summary': 'Add button for planned transfer in stock dashboard kanban',
    'version': '12.0.1.0.0',
    'author': 'Sergio Corato',
    'category': 'Warehouse',
    'license': 'AGPL-3',
    'website': 'https://github.com/sergiocorato/efatto',
    'depends': [
        'stock',
    ],
    'data': [
        'views/stock_view.xml',
    ],
    'installable': True,
}
