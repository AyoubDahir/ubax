# Import necessary modules
from odoo import api, models, fields


class TRX_source(models.Model):
    _name = 'idil.transaction.source'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _description = 'Transaction Sources'

    # Define fields
    name = fields.Char(string='Name', required=True, tracking=True)
    description = fields.Char(string='Description', tracking=True)

    # Define a display_name field to represent the name of the type
    display_name = fields.Char(compute='_compute_display_name', store=True)

    @api.depends('name', 'description')
    def _compute_display_name(self):
        for record in self:
            record.display_name = f"{record.name} - {record.description}" if record.name and record.description else record.name
