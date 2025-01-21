odoo.define("sale_order_calendar_state.CalendarColors", function (require) {
  "use strict";
  var CalendarRenderer = require("web.CalendarRenderer");
  var CalendarModel = require("web.CalendarModel");
  const core = require("web.core");
  var qweb = core.qweb;

  CalendarModel.include({
    __load: function (params) {
      // Reset fieldColor to color as calendar_state is not a valid color, but it is
      // used to replace color with custom defined in calendar.scss
      if (params.modelName === "sale.order") {
        params.fieldColor = "color";
      }
      return this._super(params);
    },
  });

  CalendarRenderer.include({
    getColor: function (key) {
      if (!key) {
        return;
      }
      if (this.color_map[key]) {
        return Math.abs(this.color_map[key]);
      }
      if (typeof key === "number" && key < 0) {
        return Math.abs(key);
      }
      return this._super(key);
    },
    init: function (parent, state, params) {
      this._super(parent, state, params);
      if (this.model === "sale.order") {
        this.color_map = {
          blocked: -321,
          to_process: -301,
          to_evaluate: -301,
          to_evaluate_production: -301,
          to_produce: -302,
          to_receive: -302,
          production_planned: -302,
          production_ready: -302,
          production_started: -303,
          to_pack: -303,
          to_assembly: -304,
          to_submanufacture: -305,
          to_test: -306,
          delivery_ready: -302,
          production_done: -310,
          partially_delivered: -308,
          delivery_done: -307,
          has_ddt: -311,
          available: -301,
          invoiced: -309,
          shipped: -301,
        };
      }
    },
    _eventRender: function (event) {
      var result = this._super(event);
      if (this.model === "sale.order") {
        var qweb_context = {
          event: event,
          record: event.extendedProps.record,
          color: this.getColor(event.extendedProps.record.color),
          showTime: !self.hideTime && event.extendedProps.showTime,
        };
        this.qweb_context = qweb_context;
        if (_.isEmpty(qweb_context.record)) {
          return "";
        }
        return qweb.render("calendar-box", qweb_context);
      }
      return result;
    },
  });
});
