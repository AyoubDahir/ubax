from odoo import models, fields, api
from odoo.exceptions import ValidationError


class StockAdjustment(models.Model):
    _name = 'idil.stock.adjustment'
    _description = 'Stock Adjustment'

    item_id = fields.Many2one('idil.item', string='Item', required=True, help='Select the item to adjust')
    adjustment_qty = fields.Float(string='Adjustment Quantity', required=True, help='Enter the quantity to adjust')
    adjustment_type = fields.Selection([('decrease', 'Decrease')],
                                       string='Adjustment Type', required=True, help='Select adjustment type')
    adjustment_date = fields.Date(string='Adjustment Date', default=fields.Date.today, required=True)
    reason = fields.Text(string='Reason for Adjustment', help='Reason for the adjustment')
    cost_price = fields.Float(string='Cost Price', related='item_id.cost_price', store=True, readonly=True,
                              help='Cost price of the item being adjusted')

    @api.model
    def create(self, vals):
        """Override the create method to adjust item quantity and log item movement."""
        adjustment = super(StockAdjustment, self).create(vals)
        item = adjustment.item_id

        # Calculate new quantity based on the adjustment type

        if adjustment.adjustment_type == 'decrease':
            if item.quantity < adjustment.adjustment_qty:
                raise ValidationError('Cannot decrease quantity below zero.')
            new_quantity = item.quantity - adjustment.adjustment_qty
            movement_type = 'out'
            # Update the item's quantity using context to prevent triggering other actions
            item.with_context(update_transaction_booking=False).write({'quantity': new_quantity})

        # Fetch the transaction source ID for stock adjustments
        trx_source = self.env['idil.transaction.source'].search([('name', '=', 'stock_adjustments')], limit=1)

        # Book the main transaction
        transaction = self.env['idil.transaction_booking'].create({
            'reffno': 'Stock Adjustments%s' % adjustment.id,  # Corrected reference number generation

            'trx_date': adjustment.adjustment_date,
            'amount': abs(adjustment.adjustment_qty * adjustment.cost_price),
            'trx_source_id': trx_source.id if trx_source else False,  # Assign the source ID if found
        })

        # Create booking lines for the transaction
        self.env['idil.transaction_bookingline'].create([
            {
                'transaction_booking_id': transaction.id,
                'description': 'Stock Adjustment Debit',
                'item_id': item.id,
                'account_number': item.adjustment_account_id.id,
                'transaction_type': 'dr',
                'dr_amount': adjustment.adjustment_qty * item.cost_price,  # Use cost price for debit amount
                'cr_amount': 0,  # Use cost price for credit amount
                'transaction_date': adjustment.adjustment_date,
            },
            {
                'transaction_booking_id': transaction.id,
                'description': 'Stock Adjustment Credit',
                'item_id': item.id,
                'account_number': item.asset_account_id.id,
                'transaction_type': 'cr',
                'cr_amount': adjustment.adjustment_qty * item.cost_price,  # Use cost price for credit amount
                'dr_amount': 0,  # Use cost price for debit amount
                'transaction_date': adjustment.adjustment_date,
            }
        ])
        # Corrected creation of item movement
        self.env['idil.item.movement'].create({
            'item_id': item.id,
            'date': adjustment.adjustment_date,
            'quantity': adjustment.adjustment_qty * -1,
            'source': 'Stock Adjustment',
            'destination': item.name,
            'movement_type': movement_type,
            'related_document': 'idil.stock.adjustment,%d' % adjustment.id,  # Corrected value format
            'transaction_number': transaction.id or '/',
        })

        return adjustment
