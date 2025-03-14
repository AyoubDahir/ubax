from odoo import models, fields


class PosSession(models.Model):
    _name = 'pos.session1'
    _description = 'POS Session'

    name = fields.Char(string='Session ID', required=True)
    user_id = fields.Many2one('res.users', string='Responsible', required=True)
    start_time = fields.Datetime(string='StartTime', default=fields.Datetime.now)
    end_time = fields.Datetime(string='End Time')
    customer_id = fields.Many2one('idil.customer.registration',
                                  string='Customer')  # Assuming 'your_module.customer' is your custom customer model
    order_ids = fields.One2many('pos.order1', 'session_id', string='Orders')


class PosOrder(models.Model):
    _name = 'pos.order1'
    _description = 'POS Order'

    name = fields.Char(string='Order ID', required=True)
    session_id = fields.Many2one('pos.session', string='Session')
    product_id = fields.Many2one('my_product.product',
                                 string='Product')  # Assuming 'your_module.product' is your custom product model
    quantity = fields.Integer(string='Quantity', default=1)
    price_unit = fields.Float(string='Unit Price')
