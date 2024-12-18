# Copyright 2020 Sergio Corato <https://github.com/sergiocorato>
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).
{
    "name": "Event visibility",
    "version": "14.0.1.0.0",
    "category": "other",
    "author": "Sergio Corato",
    "summary": "Limit visibility of event to involved users, excluding hr employee.",
    "website": "https://github.com/efatto/efatto",
    "license": "AGPL-3",
    "depends": [
        "calendar",
        "hr",
    ],
    "data": [
        "security/calendar_event_security.xml",
    ],
    "installable": True,
}
