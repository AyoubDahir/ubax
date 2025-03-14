odoo.define('idil.custom_pos_customers', function (require) {
    var models = require('point_of_sale.models');
    var PosModel = models.PosModel;
    var _super_posmodel = models.PosModel.prototype;

    PosModel.prototype.initialize = function (session, attributes) {
        // Extend the model to include your custom customer model
        models.load_models({
            model: 'idil.customer.registration',
            fields: ['name', 'phone', 'email', 'gender', 'status', 'image'],
            domain: function(self) { return [['active', '=', true]]; },
            loaded: function(self, customers) {
                self.db.add_customers(customsers);
            },
        });

        _super_posmodel.initialize.call(this, session, attributes);
    };
});
