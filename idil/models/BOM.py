from odoo import models, fields, api


# BOM Model
class BOM(models.Model):
    _name = 'idil.bom'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _description = 'Bill of Materials'

    name = fields.Char(string='BOM Name', required=True)
    type_id = fields.Many2one(comodel_name='idil.bom.type', string='BOM Types', required=True,
                              help='Select type of BOM', tracking=True)
    product_id = fields.Many2one('my_product.product', string='Component', required=True, tracking=True)

    bom_line_ids = fields.One2many('idil.bom.line', 'bom_id', string='BOM Lines', tracking=True)

    # Computed field to calculate total cost based on BOM lines
    total_cost = fields.Float(string='Total Cost', digits=(16, 5), compute='_compute_total_cost', store=True,
                              tracking=True)

    @api.depends('bom_line_ids', 'bom_line_ids.Item_id', 'bom_line_ids.quantity')
    def _compute_total_cost(self):
        for bom in self:
            total_cost = 0.0
            for bom_line in bom.bom_line_ids:
                total_cost += bom_line.Item_id.cost_price * bom_line.quantity
            bom.total_cost = total_cost


# BOM Line Model
class BOMLine(models.Model):
    _name = 'idil.bom.line'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _description = 'BOM Line'

    Item_id = fields.Many2one('idil.item', string='Component', required=True, tracking=True)
    quantity = fields.Float(string='Quantity', digits=(16, 5), required=True)
    bom_id = fields.Many2one('idil.bom', string='BOM',
                             ondelete='cascade', tracking=True)

    # Ensure that a BOM line is not duplicated for the same item
    _sql_constraints = [
        ('unique_bom_line_item', 'unique(bom_id, Item_id)', 'Item already exists in BOM lines!'),
    ]

    @api.model
    def create(self, values):
        # Check if the item already exists in BOM lines for this BOM
        existing_line = self.search([
            ('bom_id', '=', values.get('bom_id')),
            ('Item_id', '=', values.get('Item_id')),
        ], limit=1)

        if existing_line:
            # If the item exists, update the quantity instead of creating a new line
            existing_line.write({'quantity': existing_line.quantity + values.get('quantity', 0)})
            return existing_line
        else:
            # If the item doesn't exist, proceed with normal creation
            return super(BOMLine, self).create(values)
