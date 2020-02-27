# Copyright 2016-2020 Sergio Corato <https://github.com/sergiocorato>
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl.html).

{
    'name': 'Termini di pagamento per ricevute bancarie',
    'version': '12.0.1.0.1',
    'category': 'Localisation/Italy',
    'description': 'Ri.ba. sbf data',
    'author': 'Corato Sergio',
    'website': 'https://efatto.it',
    'license': 'AGPL-3',
    'depends': [
        'l10n_it_ricevute_bancarie',
    ],
    'data': [
        'data/payment_data.xml',
    ],
    'installable': True
}
