# Copyright 2023 Sergio Corato <https://github.com/sergiocorato>
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).
{
    "name": "Website slide stop resend mail",
    "version": "14.0.1.0.0",
    "category": "other",
    "author": "Sergio Corato",
    "website": "https://github.com/efatto/efatto",
    "license": "AGPL-3",
    "summary": "Block re-sending mail for slides un-published and re-published",
    "depends": [
        "website_slides",
    ],
    "data": [
        "views/slide.xml",
    ],
    "installable": True,
    "post_init_hook": "post_init_hook",
}
