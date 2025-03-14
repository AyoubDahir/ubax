from odoo import api, models, fields
from odoo.exceptions import UserError

from odoo import models, fields, api


class PaymentMethod(models.Model):
    _name = 'idil.payment.method'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _description = 'Payment Method'

    PAYMENT_TYPE_SELECTION = [
        ('cash', 'Cash'),
        ('bank', 'Bank'),
        ('credit', 'Credit'),
    ]

    name = fields.Char(string='Name', required=True, tracking=True)
    type = fields.Selection(PAYMENT_TYPE_SELECTION, string='Type', required=True, tracking=True)
    account_number = fields.Many2one('idil.chart.account', string='Account Number', required=True,
                                     domain="[('account_type', '=', account_type_filter)]")
    customer_type_id = fields.Many2one('idil.customer.type.registration', string='Customer Type',
                                       domain="[('name', '=', 'credit')]",
                                       compute='_compute_customer_type_id', store=True)

    pos_config_ids = fields.Many2many('pos.config', string='Point of Sale')

    account_type_filter = fields.Char(string='Account Type Filter', compute='_compute_account_type_filter', store=True)
    image = fields.Image("Image", max_width=50, max_height=50)

    @api.depends('type')
    def _compute_account_type_filter(self):
        for record in self:
            if record.type == 'cash':
                record.account_type_filter = 'cash'
            elif record.type == 'bank':
                record.account_type_filter = 'bank_transfer'
            elif record.type == 'credit':
                record.account_type_filter = 'receivable'
            else:
                record.account_type_filter = False

    @api.depends('type')
    def _compute_customer_type_id(self):
        for record in self:
            if record.type != 'credit':
                record.customer_type_id = False

    @api.model
    def create(self, vals):
        # Create the custom payment method
        payment_method = super(PaymentMethod, self).create(vals)

        pos_payment_vals = {
            'name': payment_method.name,
            'company_id': self.env.company.id,
            'config_ids': [(6, 0, payment_method.pos_config_ids.ids)],  # Link POS configurations
            'image': payment_method.image,
            'idil_payment_method_id': payment_method.id  # Link the custom payment method

        }
        if payment_method.type == 'cash':
            pos_payment_vals['is_cash_count'] = True
        elif payment_method.type == 'bank':
            pos_payment_vals['is_cash_count'] = False
        elif payment_method.type == 'credit':
            pos_payment_vals['type'] = 'pay_later'

        pos_payment_method = self.env['pos.payment.method'].create(pos_payment_vals)

        # Ensure linking to POS configurations without duplicates
        if payment_method.pos_config_ids:
            for config in payment_method.pos_config_ids:
                # Check if the relation already exists
                self.env.cr.execute("""
                    SELECT COUNT(*)
                    FROM pos_config_pos_payment_method_rel
                    WHERE pos_config_id = %s AND pos_payment_method_id = %s
                """, (config.id, pos_payment_method.id))
                count = self.env.cr.fetchone()[0]
                if count == 0:
                    # Insert the relation if it doesn't already exist
                    self.env.cr.execute("""
                        INSERT INTO pos_config_pos_payment_method_rel (pos_config_id, pos_payment_method_id)
                        VALUES (%s, %s)
                    """, (config.id, pos_payment_method.id))

        return payment_method

    def write(self, vals):
        res = super(PaymentMethod, self).write(vals)

        for payment_method in self:
            pos_payment_vals = {
                'name': payment_method.name,
                'company_id': self.env.company.id,
                'config_ids': [(6, 0, payment_method.pos_config_ids.ids)],  # Link POS configurations
                'image': payment_method.image,
            }
            if payment_method.type == 'cash':
                pos_payment_vals['is_cash_count'] = True
            elif payment_method.type == 'bank':
                pos_payment_vals['is_cash_count'] = False
            elif payment_method.type == 'credit':
                pos_payment_vals['type'] = 'pay_later'

            pos_payment_method = self.env['pos.payment.method'].search([('name', '=', payment_method.name)])
            if pos_payment_method:
                pos_payment_method.write(pos_payment_vals)
            else:
                pos_payment_method = self.env['pos.payment.method'].create(pos_payment_vals)

            # Ensure linking to POS configurations without duplicates
            if payment_method.pos_config_ids:
                for config in payment_method.pos_config_ids:
                    # Check if the relation already exists
                    self.env.cr.execute("""
                        SELECT COUNT(*)
                        FROM pos_config_pos_payment_method_rel
                        WHERE pos_config_id = %s AND pos_payment_method_id = %s
                    """, (config.id, pos_payment_method.id))
                    count = self.env.cr.fetchone()[0]
                    if count == 0:
                        # Insert the relation if it doesn't already exist
                        self.env.cr.execute("""
                            INSERT INTO pos_config_pos_payment_method_rel (pos_config_id, pos_payment_method_id)
                            VALUES (%s, %s)
                        """, (config.id, pos_payment_method.id))

        return res
