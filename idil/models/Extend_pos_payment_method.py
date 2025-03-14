from odoo import models, fields, api


class PosPaymentMethod(models.Model):
    _inherit = "pos.payment.method"

    idil_payment_method_id = fields.Many2one('idil.payment.method', string='Idil Payment Method')
