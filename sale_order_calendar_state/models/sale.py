# Copyright (C) 2013 Stefano Siccardi creativiquadrati snc
# Copyright 2020 Sergio Corato <https://github.com/sergiocorato>
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models

BLOCKED = "blocked"
TOPROCESS = "to_process"
NOT_EVALUATED = "to_evaluate"
PRODUCTION_NOT_EVALUATED = "to_evaluate_production"
MISSING_COMPONENTS_PRODUCE = "to_produce"
MISSING_COMPONENTS_BUY = "to_receive"
PRODUCTION_PLANNED = "production_planned"
PRODUCTION_READY = "production_ready"
PRODUCTION_STARTED = "production_started"
WAITING_FOR_PACKING = "to_pack"
TO_ASSEMBLY = "to_assembly"
TO_SUBMANUFACTURE = "to_submanufacture"
TO_TEST = "to_test"
DONE = "production_done"
DELIVERY_READY = "delivery_ready"
DONE_DELIVERY = "delivery_done"
HAS_DDT = "has_ddt"
AVAILABLEREADY = "available"
PARTIALLYDELIVERED = "partially_delivered"
INVOICED = "invoiced"
SHIPPED = "shipped"


class SaleOrder(models.Model):
    _inherit = "sale.order"

    STATES = [
        ("blocked", "BLOCKED"),
        ("to_process", "TO PROCESS"),
        ("to_evaluate", "TO EVALUATE"),
        ("to_evaluate_production", "TO EVALUATE - production"),
        ("to_produce", "WAIT MATERIAL manufacture"),
        ("to_receive", "WAIT MATERIAL"),
        ("production_planned", "WAIT PRODUCTION"),
        ("production_ready", "Production ready"),
        ("production_started", "READY TO PACK - production"),
        ("to_pack", "READY TO PACK"),
        ("to_assembly", "To assembly"),
        ("to_submanufacture", "To submanufacture"),
        ("to_test", "Test check"),
        ("delivery_ready", "READY TO DELIVER"),
        ("production_done", "DONE - production"),
        ("partially_delivered", "Partially delivered"),
        ("delivery_done", "Delivery done"),
        ("available", "Available"),
        ("has_ddt", "Has DDT"),
        ("invoiced", "Invoiced"),
        ("shipped", "Shipped"),
    ]
    STATES_COLOR_INDEX_MAP = {
        "blocked": 321,
        "to_process": 301,
        "to_evaluate": 301,
        "to_evaluate_production": 301,
        "to_produce": 302,
        "to_receive": 302,
        "production_planned": 302,
        "production_ready": 302,
        "production_started": 303,
        "to_pack": 303,
        "to_assembly": 304,
        "to_submanufacture": 305,
        "to_test": 306,
        "delivery_ready": 302,
        "production_done": 310,
        "partially_delivered": 308,
        "delivery_done": 307,
        "has_ddt": 311,
        "available": 301,
        "invoiced": 309,
        "shipped": 301,
    }

    # quando si crea l'ordine di vendita e quindi arriva in WHS automaticamente,
    # lo stato dell'SO deve essere 'da processare' (o 'disponibile'),
    # dunque di colore bianco - il magazzino lo vede in WHS ma non lo sta ancora
    # preparando, in attesa di miei comandi.
    #
    # Quando poi l'utente clicca su 'controlla disponibilità' e lo stato dell'OUT
    # passa a 'pronto', lo stato dell'SO deve passare a 'in preparazione a magazzino'
    # e prendere il colore giallo - il magazzino ha l'autorizzazione a prelevare
    # l'ordine (anche perché porto giù il foglio con la distinta di prelievo dell'OUT).
    #
    # Lo stato 'disponibile' evidenzia ordini per il quale materiale è appunto
    # disponibile, ma seguendo la logica di cui sopra, dovrebbe rimanere bianco,
    # in quanto sarebbe ancora da processare/stampare.
    #
    # Detto ciò, l'unico colore giallo deve essere dato allo stato SO
    # 'in preparazione a magazzino', tutto il resto rimane bianco.

    commitment_date = fields.Datetime(string="Scheduled Delivery Date")
    color = fields.Integer(compute="_compute_color")
    calendar_state = fields.Selection(
        selection=STATES,
        default="to_process",
        required=True,
        compute="_compute_calendar_state",
        store=True,
    )
    max_commitment_date = fields.Datetime(
        compute="_compute_max_commitment_date",
        inverse="_inverse_max_commitment_date",
        store=True,
        string="Max Scheduled Delivery Date",
    )
    delivery_week = fields.Integer(compute="_compute_delivery_week", store=True)
    custom_production_qty = fields.Integer(
        compute="_compute_custom_production", store=True
    )
    custom_production_qty_calendar = fields.Text(
        compute="_compute_custom_production", store=True
    )
    is_blocked = fields.Boolean()
    blocked_note = fields.Char()
    blocked_note_calendar = fields.Char(
        compute="_compute_blocked_note_calendar", store=True
    )
    production_id = fields.Many2one(
        comodel_name="mrp.production", compute="_compute_production_id", store=True
    )
    product_production_id = fields.Many2one(
        comodel_name="product.product",
        related="production_id.product_id",
        store=True,
        index=True,
        string="Manucfactured product",
    )
    partner_priority_id = fields.Many2one(
        related="partner_id.priority_id",
        string="Partner Priority",
        index=True,
        store=True,
    )
    has_kit = fields.Boolean(
        string="Has kit",
        compute="_compute_has_kit",
        index=True,
        store=True,
    )
    in_kit = fields.Boolean(
        compute="_compute_in_kit",
        store=True,
        index=True,
        string="Has a Kit and is Custom",
    )

    @api.depends("has_kit", "custom_production_qty")
    def _compute_in_kit(self):
        for order in self:
            order.in_kit = bool(order.custom_production_qty > 0 and order.has_kit)

    # temporary solution to show more info in calendar view
    @api.depends(
        "partner_id",
        "name",
        "production_id",
        "custom_production_qty_calendar",
        "production_notes_calendar",
        "blocked_note_calendar",
        "is_prototype_calendar",
    )
    def name_get(self):
        if self.env.context.get("default_calendar_state", False):
            result = []
            for order in self:
                name = "%s %s" % (order.partner_id.name, order.name)
                if order.production_id:
                    name += " %s" % order.production_id.name
                if order.custom_production_qty_calendar:
                    name += " %s" % order.custom_production_qty_calendar
                if order.production_notes_calendar:
                    name += " %s" % order.production_notes_calendar
                if order.blocked_note_calendar:
                    name += " %s" % order.blocked_note_calendar
                if order.is_prototype_calendar:
                    name += " %s" % order.is_prototype_calendar
                result.append((order.id, name))
        else:
            result = super(SaleOrder, self).name_get()
        return result

    @api.depends("production_ids")
    def _compute_production_id(self):
        for order in self:
            order.production_id = (
                False
                if not order.production_ids
                else order.production_ids.sorted(
                    key=lambda x: x.partner_id, reverse=True
                )[0]
            )

    @api.depends("blocked_note", "production_ids.blocked_note")
    def _compute_blocked_note_calendar(self):
        for order in self:
            order.blocked_note_calendar = (
                order.blocked_note
                if order.blocked_note
                else " ".join(
                    order.production_ids.filtered(lambda x: x.state != "cancel").mapped(
                        "blocked_note"
                    )
                )
                if order.production_ids
                and any(x.blocked_note for x in order.production_ids)
                else ""
            )

    @api.depends("order_line.product_id", "state")
    def _compute_has_kit(self):
        # Compute and store on product_id only, because bom type can be changed by the
        # user only for this sale order. Add state to recompute when user confirm the
        # order.
        for order in self:
            if any(
                x.product_id.bom_ids.filtered(
                    lambda bom: bom.type == "phantom"
                    and self.env.ref("mrp.route_warehouse0_manufacture")
                    in x.product_id.route_ids
                )
                for x in order.order_line
            ):
                order.has_kit = True
            else:
                order.has_kit = False

    @api.depends("order_line.product_id.categ_id")
    def _compute_custom_production(self):
        custom_ctg_id = self.env["product.category"].search([("name", "=", "CUSTOM")])
        for order in self:
            if any([x.product_id.categ_id == custom_ctg_id for x in order.order_line]):
                order.custom_production_qty = sum(
                    [
                        x.product_qty
                        for x in order.order_line.filtered(
                            lambda y: y.product_id.categ_id == custom_ctg_id
                        )
                    ]
                )
                order.custom_production_qty_calendar = (
                    "Q.tà produzione Custom: %s" % order.custom_production_qty
                )
            else:
                order.custom_production_qty = 0
                order.custom_production_qty_calendar = ""

    @api.depends("commitment_date")
    def _compute_delivery_week(self):
        for order in self:
            if not order.commitment_date:
                order.delivery_week = False
                continue
            order.delivery_week = order.commitment_date.isocalendar()[1]

    def _inverse_max_commitment_date(self):
        for order in self:
            if not order.commitment_date:
                order_line_commitment_dates = order.order_line.filtered(
                    "commitment_date"
                ).mapped("commitment_date")
                if order_line_commitment_dates:
                    order.commitment_date = max(order_line_commitment_dates)
            order.order_line.commitment_date = order.max_commitment_date

    @api.depends("order_line.commitment_date", "commitment_date", "date_order")
    def _compute_max_commitment_date(self):
        # todo check confirmation_date has been replaced by date_order, is it updated?
        for order in self.filtered(
            lambda x: x.commitment_date
            or x.date_order
            or any(x.mapped("order_line.commitment_date"))
        ):
            dates = []
            if order.commitment_date:
                dates.extend([order.commitment_date])
            if order.date_order:
                dates.extend([order.date_order])
            line_dates = order.order_line.filtered(lambda z: z.commitment_date).mapped(
                "commitment_date"
            )
            if line_dates:
                dates.extend(line_dates)
            order.max_commitment_date = max(dates)

    def _compute_color(self):
        for order in self:
            color = order.STATES_COLOR_INDEX_MAP.get(order.calendar_state, 0)
            order.color = color * -1

    @api.onchange("order_line", "commitment_date")
    def _onchange_commitment_date(self):
        if self.commitment_date or self.order_line.mapped("commitment_date"):
            # if not self.calendar_state:
            # and self.state not in ('draft', 'sent')
            # perchè c'era questo onchange solo in questi stati?
            max_commitment_date = self.max_commitment_date or self.commitment_date
            if max_commitment_date:
                calendar_state = self.get_forecast_calendar_state(
                    max_commitment_date=max_commitment_date
                )
                if calendar_state:
                    self.calendar_state = calendar_state

    def get_forecast_calendar_state(self, max_commitment_date=False):
        # Ordine dei vari stati calendar_state in base a quelli più importanti
        # esempio: se un ordine ha una produzione con stato PRODUCTION_STARTED
        # ed una con stato MISSING_COMPONENTS_BUY
        # l'ordine deve avere stato MISSING_COMPONENTS_BUY, perchè peggiore.
        cal_order = [
            BLOCKED,
            TOPROCESS,
            PRODUCTION_NOT_EVALUATED,
            TO_ASSEMBLY,
            TO_SUBMANUFACTURE,
            TO_TEST,
            MISSING_COMPONENTS_PRODUCE,
            PRODUCTION_PLANNED,
            PRODUCTION_READY,
            PRODUCTION_STARTED,
            DONE,
            NOT_EVALUATED,
            MISSING_COMPONENTS_BUY,
            PARTIALLYDELIVERED,
            AVAILABLEREADY,
            WAITING_FOR_PACKING,
            DELIVERY_READY,
            DONE_DELIVERY,
            HAS_DDT,
            INVOICED,
            SHIPPED,
        ]
        calendar_state = False
        if self.procurement_group_id or self.order_line.mapped("procurement_group_id"):
            calendar_states = []
            procurement_group_ids = self.procurement_group_id
            procurement_group_ids |= self.order_line.mapped("procurement_group_id")
            for procurement_group in procurement_group_ids:
                states = self.forecast_procurement(
                    procurement_group, max_commitment_date
                )
                if states:
                    calendar_states += states
            if self.is_blocked:
                calendar_states.append(("blocked", fields.Datetime.now()))
            if calendar_states:
                calendar_states = filter(None, calendar_states)
                calendar_state = min(
                    calendar_states, key=lambda x: cal_order.index(x[0])
                )[0]
        return calendar_state

    def forecast_procurement(self, procurement, max_commitment_date):  # noqa: C901
        calendar_states = []
        # group_id in stock.move
        picking_ids = self.env["stock.picking"].search(
            [
                ("group_id", "=", procurement.id),
                ("state", "!=", "cancel"),
            ]
        )
        if picking_ids:
            # all'inserimento -> bianco to_process
            # tutti i prodotti riservati -> bianco NOT_EVALUATED
            # materiali mancanti > arancioni (con righe in rosso) MISSING_COMPONENTS_BUY
            # stampo > giallo (sempre anche se mancano materiali) WAITING_FOR_PACKING
            calendar_state = []
            if all(
                [
                    x.delivery_note_id and x.delivery_note_state != "cancel"
                    for x in picking_ids
                ]
            ):
                calendar_state = HAS_DDT
            elif all([x.state == "done" for x in picking_ids]):
                delivery_notes = picking_ids.mapped("delivery_note_id")
                if delivery_notes and any([x.state != "done" for x in delivery_notes]):
                    calendar_state = WAITING_FOR_PACKING
                else:
                    calendar_state = DONE_DELIVERY
            elif all(x.is_assigned for x in picking_ids):
                calendar_state = WAITING_FOR_PACKING
            elif all(
                move.state in ["cancel", "done", "assigned"]
                for move in picking_ids.mapped("move_lines")
            ):
                if all([x.carrier_tracking_ref for x in picking_ids]):
                    calendar_state = DELIVERY_READY
                else:
                    calendar_state = NOT_EVALUATED
            elif any([x.state == "done" for x in picking_ids]) and any(
                [x.state != "done" for x in picking_ids]
            ):
                calendar_state = PARTIALLYDELIVERED
            elif any(
                [
                    move.state in ["confirmed", "waiting", "partially_available"]
                    for move in picking_ids.mapped("move_lines")
                ]
            ):
                calendar_state = MISSING_COMPONENTS_BUY
            if calendar_state:
                calendar_states.append((calendar_state, fields.Datetime.now()))
        # this is needed if picking are not done only i presume, tocheck
        # group_id in purchase.order
        purchase_line_ids = self.env["purchase.order.line"].search(
            [
                ("procurement_group_id", "=", procurement.id),
                ("state", "!=", "cancel"),
            ]
        )
        if purchase_line_ids:
            for purchase_line in purchase_line_ids:
                if purchase_line.state == "done":
                    calendar_states.append((WAITING_FOR_PACKING, fields.Datetime.now()))
                elif purchase_line.state == "purchase":
                    # I think if we have not recieved the product yet, it should
                    # just return missing buy component. but I kept the old version
                    planned_date = max(
                        fields.Datetime.now(), purchase_line.order_id.date_planned
                    )
                    if (
                        planned_date
                        and max_commitment_date
                        and planned_date > max_commitment_date
                    ):
                        calendar_states.append((MISSING_COMPONENTS_BUY, planned_date))
                    else:
                        # availableready is just a state i made to make a difference
                        # between buy and maufacture products
                        calendar_states.append((AVAILABLEREADY, fields.Datetime.now()))
        mrp_production_ids = self.env["mrp.production"].search(
            [
                ("sale_id", "=", procurement.sale_id.id),
                ("state", "!=", "cancel"),
            ]
        )
        if mrp_production_ids:
            mrp_states = set(mrp_production_ids.mapped("state"))
            mrp_additional_states = mrp_production_ids.filtered(
                lambda x: x.additional_state
            ).mapped("additional_state")
            if mrp_additional_states:
                mrp_additional_states = set(mrp_additional_states)
            if mrp_states & {"to_close", "done"} and (
                calendar_states
                and not any(
                    x[0] in (DONE_DELIVERY, WAITING_FOR_PACKING, HAS_DDT)
                    for x in calendar_states
                )
            ):
                # set DONE state only when there are not open or done pickings
                calendar_states.append((DONE, fields.Datetime.now()))
            if mrp_states & {"draft", "confirmed"}:
                for mrp_production in mrp_production_ids:
                    if mrp_production.move_raw_ids.filtered(
                        lambda x: x.state != "cancel"
                    ).mapped("state") == ["done"]:
                        calendar_states.append((DONE, fields.Datetime.now()))
                    elif mrp_production.move_raw_ids.filtered(
                        lambda x: x.state != "cancel"
                    ).mapped("state") != ["assigned"]:
                        datetime_planned = fields.Datetime.now() + relativedelta(
                            days=int(mrp_production.product_id.produce_delay)
                        )
                        calendar_states.append(
                            (MISSING_COMPONENTS_PRODUCE, datetime_planned)
                        )
                    elif mrp_production.date_planned_start >= fields.Datetime.now():
                        calendar_states.append(
                            (PRODUCTION_PLANNED, fields.Datetime.now())
                        )
                    else:
                        calendar_states.append(
                            (PRODUCTION_READY, fields.Datetime.now())
                        )
            # elif mrp_production.state == "planned": # does not more exist
            #    calendar_states.append((PRODUCTION_PLANNED, fields.Datetime.now()))
            if mrp_states & {"progress"}:
                calendar_states.append((PRODUCTION_STARTED, fields.Datetime.now()))
            if mrp_states & {"consumed"}:
                calendar_states.append((PRODUCTION_STARTED, fields.Datetime.now()))
            if mrp_additional_states:
                for mrp_additional_state in mrp_additional_states:
                    calendar_states.append(
                        (mrp_additional_state, fields.Datetime.now())
                    )
            if any(production.is_blocked for production in mrp_production_ids):
                calendar_states.append(("blocked", fields.Datetime.now()))
        # check if all lines of type product or consu of so are invoiced
        if (
            all(
                [
                    ol.qty_delivered == ol.qty_invoiced == ol.product_uom_qty
                    for ol in self.order_line
                    if ol.product_id and ol.product_id.type in ["product", "consu"]
                ]
            )
            or self.invoice_status == "invoiced"
            or self.force_invoiced
        ):
            calendar_states = [(INVOICED, fields.Datetime.now())]
            # check if all invoices linked to SO have tracking_ref filled
            # DISABLED by direct request
            # if all([inv.carrier_tracking_ref for inv in self.invoice_ids]):
            #     calendar_states = [(SHIPPED, fields.Datetime.now())]
        if not (picking_ids or purchase_line_ids or mrp_production_ids):
            # ignore this procurement as it is cancelled
            return False
        if not calendar_states:
            calendar_states = [(TOPROCESS, fields.Datetime.now())]
        return calendar_states

    @api.depends(
        "order_line.qty_invoiced",
        "is_blocked",
        "picking_ids.move_lines.state",
        "picking_ids.state",
        "picking_ids.is_assigned",
        "production_ids.additional_state",
        "production_ids.is_blocked",
        "production_ids.move_raw_ids.state",
    )
    def _compute_calendar_state(self):
        for order in self:
            order.calendar_state = (
                order.get_forecast_calendar_state()
                or order.calendar_state
                or "to_process"
            )

    def update_forecast_state(self):
        for order in self:
            calendar_state = order.get_forecast_calendar_state()
            if calendar_state:
                order.calendar_state = calendar_state

    def alert_customer_changed_delivery(self):
        """
        This function opens a window to compose an email,
        with the "changed delivery date" template opened by default.
        """
        self.ensure_one()
        template = self.env.ref(
            "sale_order_calendar_state.email_template_delivery_date_changed",
            raise_if_not_found=False,
        )
        local_context = dict(
            default_model="sale.order",
            default_res_id=self.id,
            default_use_template=bool(template),
            default_template_id=template and template.id or False,
            default_composition_mode="comment",
            change_agreed_date=True,
        )
        return {
            "type": "ir.actions.act_window",
            "view_type": "form",
            "view_mode": "form",
            "res_model": "mail.compose.message",
            "views": [(False, "form")],
            "target": "new",
            "context": local_context,
        }

    def button_mark_not_blocked(self):
        for rec in self:
            rec.write(
                {
                    "is_blocked": False,
                    "blocked_note": False,
                }
            )
