odoo.define('idil.progress_bar_widget', function (require) {
    'use strict';

    var AbstractField = require('web.AbstractField');
    var field_registry = require('web.field_registry');

    var ProgressBar = AbstractField.extend({
        supportedFieldTypes: ['integer'],

        init: function () {
            this._super.apply(this, arguments);
            this.update_value();
        },

        update_value: function () {
            var totalFields = 10; // Update with the total number of fields in the form
            var filledFields = this.recordData.filled_fields; // Implement logic to count filled fields

            var percentage = (filledFields / totalFields) * 100;
            this.$el.text(percentage.toFixed(2) + '%');
        },
    });

    field_registry.add('progress_bar', ProgressBar);

    return {
        ProgressBar: ProgressBar,
    };
});
