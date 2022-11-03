# Copyright 2022 Sergio Corato <https://github.com/sergiocorato>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
{
    'name': 'MRP Production Move Price Sync',
    'version': '12.0.1.0.0',
    'development_status': 'Beta',
    'license': 'AGPL-3',
    'category': 'Manufacturing',
    'author': 'Sergio Corato',
    'website': 'https://efatto.it',
    'description': 'Currently cost of moves from production are set when production is '
                   'marked done. This moment is not always appropriate as, for '
                   'example, a product could be purchased without price and price '
                   'could be known after invoice receipt.\n'
                   'With this module, cost of moves are synchronized when necessary, '
                   'even later, checking stock availability of the products.',
    'depends': [
        'account_invoice_update_purchase',
        'mrp_production_demo',
    ],
    'data': [
        'views/mrp.xml',
        'wizard/mrp_sync_price.xml',
    ],
    'installable': True,
}
