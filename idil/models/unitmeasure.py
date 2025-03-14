from odoo import api, models, fields


class unitmeasure(models.Model):
    _name = 'idil.unit.measure'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _description = 'Unit of Measure'

    name = fields.Char(string='name', required=True, tracking=True)
    description = fields.Char(string='description', tracking=True)
