from odoo import api, models, fields


class Type(models.Model):
    _name = 'idil.customer.type.registration'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _description = 'Customer Type'

    name = fields.Char(string='name', required=True, tracking=True)
    description = fields.Char(string='description', tracking=True)


# Define a display_name field to represent the name of the type
