from odoo import models, fields, api, _
from odoo.exceptions import UserError


class KitchenTransfer(models.Model):
    _name = 'idil.kitchen.transfer'
    _description = 'Kitchen Transfer'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(string='Transfer Reference', required=True, copy=False, default='New')
    transfer_date = fields.Datetime(string='Transfer Date', default=fields.Datetime.now, required=True, tracking=True)
    kitchen_id = fields.Many2one('idil.kitchen', string='Kitchen', required=True, tracking=True)
    transferred_by = fields.Many2one('res.users', string='Transferred By', default=lambda self: self.env.user,
                                     required=True, tracking=True)
    transfer_line_ids = fields.One2many('idil.kitchen.transfer.line', 'transfer_id', string='Transfer Lines',
                                        tracking=True)
    transaction_booking_id = fields.Many2one('idil.transaction_booking', string='Transaction Booking', readonly=True)
    subtotal = fields.Float(string='Subtotal', compute='_compute_subtotal', store=True)
    state = fields.Selection([('draft', 'Draft'), ('processed', 'Processed')], default='draft', tracking=True)

    @api.depends('transfer_line_ids.total')
    def _compute_subtotal(self):
        for transfer in self:
            transfer.subtotal = sum(line.total for line in transfer.transfer_line_ids)

    @api.model
    def create(self, vals):
        if vals.get('name', _('New')) == _('New'):
            vals['name'] = self.env['ir.sequence'].next_by_code('idil.kitchen.transfer') or _('New')

        # Check and update item quantities
        self._update_item_quantities(vals.get('transfer_line_ids', []), 'create')

        # Create the kitchen transfer record
        transfer = super(KitchenTransfer, self).create(vals)

        # Create a corresponding transaction booking record
        transaction_booking = self._create_transaction_booking(transfer)
        transfer.transaction_booking_id = transaction_booking.id

        return transfer

    def write(self, vals):
        if 'transfer_line_ids' in vals:
            self._update_item_quantities(vals['transfer_line_ids'], 'write')

        # Write the updated transfer
        result = super(KitchenTransfer, self).write(vals)

        # Update the corresponding transaction booking record
        if 'transfer_line_ids' in vals:
            self._update_transaction_booking()

        return result

    def unlink(self):
        # Adjust the transaction booking and booking lines before deleting the transfer
        for transfer in self:
            if transfer.transaction_booking_id:
                # Remove related booking lines
                self.env['idil.transaction_bookingline'].search([
                    ('transaction_booking_id', '=', transfer.transaction_booking_id.id)
                ]).unlink()

                # Remove the transaction booking
                transfer.transaction_booking_id.unlink()

        return super(KitchenTransfer, self).unlink()

    def _update_item_quantities(self, transfer_lines, operation_type):
        for line in transfer_lines:
            if line[0] == 0:  # New line
                item_id = line[2].get('item_id')
                quantity = line[2].get('quantity')
                if item_id and quantity:
                    item = self.env['idil.item'].browse(item_id)
                    if item.quantity < quantity:
                        raise UserError(_('Not enough quantity for item: %s' % item.name))
                    item.quantity -= quantity
            elif line[0] == 1:  # Updated line
                existing_line = self.env['idil.kitchen.transfer.line'].browse(line[1])
                new_quantity = line[2].get('quantity')
                if existing_line and new_quantity:
                    diff_quantity = new_quantity - existing_line.quantity
                    item = existing_line.item_id
                    if diff_quantity > 0:  # Increasing quantity
                        if item.quantity < diff_quantity:
                            raise UserError(_('Not enough quantity for item: %s' % item.name))
                        item.quantity -= diff_quantity
                    elif diff_quantity < 0:  # Decreasing quantity
                        item.quantity += abs(diff_quantity)  # Adjust quantity

    def _create_transaction_booking(self, transfer):
        # Create a transaction booking record
        transaction_booking_vals = {
            'transaction_number': self.env['ir.sequence'].next_by_code('idil.transaction.booking') or 0,
            'reffno': transfer.name,
            'trx_date': transfer.transfer_date,
            'payment_method': 'internal',
            'amount': transfer.subtotal,
        }

        transaction_booking = self.env['idil.transaction_booking'].create(transaction_booking_vals)

        # Create corresponding transaction booking lines
        for line in transfer.transfer_line_ids:
            # Validate the existence of required accounts
            if not transfer.kitchen_id.inventory_account:
                raise UserError(_('Inventory account is not set for the kitchen: %s' % transfer.kitchen_id.name))
            if not line.item_id.asset_account_id:
                raise UserError(_('Credit account is not set for the item: %s' % line.item_id.name))

            # Add debit line
            self.env['idil.transaction_bookingline'].create({
                'transaction_booking_id': transaction_booking.id,
                'description': f'Debit of Kitchen Transfer for {line.item_id.name}',
                'item_id': line.item_id.id,
                'account_number': transfer.kitchen_id.inventory_account.id,
                'transaction_type': 'dr',
                'dr_amount': line.total,
                'cr_amount': 0,
                'transaction_date': fields.Date.today(),
            })
            # Add credit line
            self.env['idil.transaction_bookingline'].create({
                'transaction_booking_id': transaction_booking.id,
                'description': f'Credit of Kitchen Transfer for {line.item_id.name}',
                'item_id': line.item_id.id,
                'account_number': line.item_id.asset_account_id.id,
                'transaction_type': 'cr',
                'cr_amount': line.total,
                'dr_amount': 0,
                'transaction_date': fields.Date.today(),
            })

        return transaction_booking

    def _update_transaction_booking(self):
        for transfer in self:
            if not transfer.transaction_booking_id:
                continue

            transaction_booking = transfer.transaction_booking_id

            # Update the amount in the transaction booking
            transaction_booking.amount = transfer.subtotal

            # Remove existing booking lines
            self.env['idil.transaction_bookingline'].search([
                ('transaction_booking_id', '=', transaction_booking.id)
            ]).unlink()

            # Create updated booking lines
            for line in transfer.transfer_line_ids:
                # Add debit line
                self.env['idil.transaction_bookingline'].create({
                    'transaction_booking_id': transaction_booking.id,
                    'description': f'Debit of Kitchen Transfer for {line.item_id.name}',
                    'item_id': line.item_id.id,
                    'account_number': transfer.kitchen_id.inventory_account.id,
                    'transaction_type': 'dr',
                    'dr_amount': line.total,
                    'cr_amount': 0,
                    'transaction_date': fields.Date.today(),
                })
                # Add credit line
                self.env['idil.transaction_bookingline'].create({
                    'transaction_booking_id': transaction_booking.id,
                    'description': f'Credit of Kitchen Transfer for {line.item_id.name}',
                    'item_id': line.item_id.id,
                    'account_number': line.item_id.asset_account_id.id,
                    'transaction_type': 'cr',
                    'cr_amount': line.total,
                    'dr_amount': 0,
                    'transaction_date': fields.Date.today(),
                })


class KitchenTransferLine(models.Model):
    _name = 'idil.kitchen.transfer.line'
    _description = 'Kitchen Transfer Line'

    transfer_id = fields.Many2one('idil.kitchen.transfer', string='Transfer Reference', required=True,
                                  ondelete='cascade')
    item_id = fields.Many2one('idil.item', string='Item', required=True)
    quantity = fields.Float(string='Quantity', required=True)
    uom_id = fields.Many2one('idil.unit.measure', string='Unit of Measurement', related='item_id.unitmeasure_id',
                             readonly=True)

    quantity_item = fields.Float(string='QTY', related='item_id.quantity', readonly=True)  # Corrected field type

    unit_price = fields.Float(string='Unit Price', related='item_id.cost_price', readonly=True, store=True)
    total = fields.Float(string='Total', compute='_compute_total', store=True)

    @api.depends('quantity', 'unit_price')
    def _compute_total(self):
        for line in self:
            line.total = line.quantity * line.unit_price
