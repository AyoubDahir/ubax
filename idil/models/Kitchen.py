from odoo import models, fields


class Kitchen(models.Model):
    _name = 'idil.kitchen'
    _description = 'Kitchen'

    name = fields.Char(string='Name')
    location = fields.Char(string='Location')
    contact_person = fields.Char(string='Contact Person')
    contact_email = fields.Char(string='Contact Email')
    contact_phone = fields.Char(string='Contact Phone')
    notes = fields.Text(string='Notes')

    inventory_account = fields.Many2one(
        'idil.chart.account',
        string='Inventory Account Number',
        domain="[('account_type', '=', 'kitchen')]"  # Assuming 'kitchen' is a valid account_type value
    )
