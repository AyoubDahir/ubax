from odoo import api, models, fields


class itemcategory(models.Model):
    _name = 'idil.item.category'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _description = 'Item Category'

    name = fields.Char(string='name', required=True, tracking=True)
    description = fields.Char(string='description', tracking=True)

# Define a display_name field to represent the name of the type
