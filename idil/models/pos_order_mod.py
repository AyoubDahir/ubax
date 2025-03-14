from odoo import models, fields


class PosOrder(models.Model):
    _inherit = 'pos.order'

    customer_id = fields.Many2one('idil.customer.registration', string='Customer')
