from odoo import _, api, fields, models


class LinkTracker(models.Model):
    _inherit = "link.tracker"

    campaign_partner_ids = fields.Many2many(
        comodel_name="res.partner",
        relation="link_tracker_res_partner_campaign_rel",
        string="Campaign Partners",
        help="Partners originated from this tracker by campaign",
        compute="_compute_partner_ids",
        compute_sudo=True,
        store=True,
        index=True,
    )
    campaign_partners_count = fields.Integer(
        compute="_compute_partner_ids",
        compute_sudo=True,
    )
    source_partner_ids = fields.Many2many(
        comodel_name="res.partner",
        relation="link_tracker_res_partner_source_rel",
        string="Source Partners",
        help="Partners originated from this tracker by source",
        compute="_compute_partner_ids",
        compute_sudo=True,
        store=True,
        index=True,
    )
    source_partners_count = fields.Integer(
        compute="_compute_partner_ids",
        compute_sudo=True,
    )
    mailing_partner_ids = fields.Many2many(
        comodel_name="res.partner",
        relation="link_tracker_res_partner_mailing_rel",
        string="Mailing Partners",
        help="Partners originated from this tracker by mass mailing",
        compute="_compute_partner_ids",
        compute_sudo=True,
        store=True,
        index=True,
    )
    mailing_partners_count = fields.Integer(
        compute="_compute_partner_ids",
        compute_sudo=True,
    )

    @api.depends("source_id", "campaign_id", "mass_mailing_id")
    def _compute_partner_ids(self):
        partner_obj = self.env["res.partner"]
        campaign_assigned_partners = partner_obj.browse()
        source_assigned_partners = partner_obj.browse()
        mailing_assigned_partners = partner_obj.browse()
        for track in sorted(self, key=lambda x: x.create_date):
            track.source_partner_ids = False
            track.source_partners_count = 0
            track.mailing_partner_ids = False
            track.mailing_partners_count = 0
            track.campaign_partner_ids = False
            track.campaign_partners_count = 0
            # get partner with one or more of the fields on depends, created after the
            # creation of the depending object, to exclude false positive (a partner
            # could be in a mailing list but created before) (this logic coud be removed
            # if the field is set in the partner only if created from that)
            if (
                track.mass_mailing_id
                and track.mass_mailing_id.mailing_model_id
                != self.env["ir.model"]._get("res.partner")
            ):
                track.mailing_partner_ids = self.env["res.partner"].search(
                    [
                        ("create_date", ">=", track.mass_mailing_id.create_date),
                        (
                            "email",
                            "in",
                            track.mass_mailing_id.mailing_trace_ids.mapped("email"),
                        ),
                        ("id", "not in", mailing_assigned_partners.ids),
                        ("email", "!=", False),
                    ]
                )
                track.mailing_partners_count = len(track.mailing_partner_ids)
                mailing_assigned_partners |= track.mailing_partner_ids
            if track.source_id:
                track.source_partner_ids = self.env["res.partner"].search(
                    [
                        ("create_date", ">=", track.source_id.create_date),
                        ("source_id", "=", track.source_id.id),
                        ("id", "not in", source_assigned_partners.ids),
                    ]
                )
                track.source_partners_count = len(track.source_partner_ids)
                source_assigned_partners |= track.source_partner_ids
            if track.campaign_id:
                track.campaign_partner_ids = self.env["res.partner"].search(
                    [
                        ("create_date", ">=", track.campaign_id.create_date),
                        ("campaign_id", "=", track.campaign_id.id),
                        ("id", "not in", campaign_assigned_partners.ids),
                    ]
                )
                track.campaign_partners_count = len(track.campaign_partner_ids)
                campaign_assigned_partners |= track.campaign_partner_ids

    def action_view_mailing_partners(self):
        self.ensure_one()
        action = {
            "name": _("Mailing Partners"),
            "view_mode": "tree,form",
            "res_model": "res.partner",
            "type": "ir.actions.act_window",
            "domain": [("id", "in", self.mailing_partner_ids.ids)],
        }
        if len(self.mailing_partner_ids) == 1:
            # If there is only one partner, open it directly
            action.update({"view_mode": "form", "res_id": self.mailing_partner_ids.id})
        return action

    def action_view_source_partners(self):
        self.ensure_one()
        action = {
            "name": _("Source Partners"),
            "view_mode": "tree,form",
            "res_model": "res.partner",
            "type": "ir.actions.act_window",
            "domain": [("id", "in", self.source_partner_ids.ids)],
        }
        if len(self.source_partner_ids) == 1:
            # If there is only one partner, open it directly
            action.update({"view_mode": "form", "res_id": self.source_partner_ids.id})
        return action

    def action_view_campaign_partners(self):
        self.ensure_one()
        action = {
            "name": _("Campaign Partners"),
            "view_mode": "tree,form",
            "res_model": "res.partner",
            "type": "ir.actions.act_window",
            "domain": [("id", "in", self.campaign_partner_ids.ids)],
        }
        if len(self.campaign_partner_ids) == 1:
            # If there is only one partner, open it directly
            action.update({"view_mode": "form", "res_id": self.campaign_partner_ids.id})
        return action
