# -*- coding: utf-8 -*-
#
#
#    Copyright (C) 2016 Sergio Corato (<http://www.librerp.it>)
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU Affero General Public License as published
#    by the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU Affero General Public License for more details.
#
#    You should have received a copy of the GNU Affero General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
#
{
    'name': 'Termini di pagamento per ricevute bancarie',
    'version': '8.0.1.0',
    'category': 'Localisation/Italy',
    'description': """Ri.ba. sbf data.""",
    'author': 'Corato Sergio',
    'website': 'http://www.efatto.it',
    'license': 'AGPL-3',
    "depends": [
        'l10n_it_ricevute_bancarie',
    ],
    "data": [
        'data/payment_data.xml',
    ],
    "installable": True
}
