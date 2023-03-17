# Copyright 2021 Sergio Corato <https://github.com/sergiocorato>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
{
    'name': 'Stock picking update move price',
    'version': '12.0.1.0.1',
    'license': 'AGPL-3',
    'author': 'Sergio Corato',
    'website': 'https://github.com/sergiocorato/efatto',
    'category': 'other',
    'depends': [
        'mrp',
        'stock',
    ],
    'data': [
        'wizard/update_stock_move_price.xml',
        'views/stock_picking.xml',
    ],
    'summary': "This module add an action on stock move to update stock move price "
               "unit for evaluation purposes.",
    'installable': True,
}
