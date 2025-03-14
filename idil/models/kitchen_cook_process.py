from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError


class KitchenCookProcess(models.Model):
    _name = 'idil.kitchen.cook.process'
    _description = 'Kitchen Cook Process'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(string='Process Reference', required=True, copy=False, default='New')
    process_date = fields.Datetime(string='Process Date', default=fields.Datetime.now, required=True, tracking=True)
    kitchen_transfer_id = fields.Many2one('idil.kitchen.transfer', string='Kitchen Transfer', required=True,
                                          tracking=True)
    cook_line_ids = fields.One2many('idil.kitchen.cook.line', 'cook_process_id', string='Cook Lines', tracking=True)
    subtotal = fields.Float(string='Subtotal', compute='_compute_subtotal', store=True)
    state = fields.Selection([('draft', 'Draft'), ('processed', 'Processed')], default='draft', tracking=True)

    @api.depends('cook_line_ids.cooked_amount')
    def _compute_subtotal(self):
        for process in self:
            process.subtotal = sum(line.cooked_amount for line in process.cook_line_ids)

    @api.model
    def create(self, vals):
        if vals.get('name', _('New')) == _('New'):
            vals['name'] = self.env['ir.sequence'].next_by_code('idil.kitchen.cook.process') or _('New')

        process = super(KitchenCookProcess, self).create(vals)
        process._set_transfer_data()
        return process

    def write(self, vals):
        res = super(KitchenCookProcess, self).write(vals)
        self._set_transfer_data()
        return res

    @api.onchange('kitchen_transfer_id')
    def _onchange_kitchen_transfer_id(self):
        if self.kitchen_transfer_id:
            cook_lines = []
            for line in self.kitchen_transfer_id.transfer_line_ids:
                cook_lines.append((0, 0, {
                    'item_id': line.item_id.id,
                    'transfer_qty': line.quantity,
                    'transfer_amount': line.total,
                    'unit_price': line.unit_price,
                }))
            self.cook_line_ids = cook_lines

    def _set_transfer_data(self):
        for process in self:
            for cook_line in process.cook_line_ids:
                transfer_line = self.env['idil.kitchen.transfer.line'].search([
                    ('transfer_id', '=', process.kitchen_transfer_id.id),
                    ('item_id', '=', cook_line.item_id.id)
                ], limit=1)
                if transfer_line:
                    cook_line.write({
                        'transfer_qty': transfer_line.quantity,
                        'transfer_amount': transfer_line.total,
                    })

    def action_process(self):
        for process in self:
            if process.state == 'processed':
                raise UserError(_('This process has already been completed.'))

            for line in process.cook_line_ids:
                if line.cooked_qty <= 0:
                    raise UserError(_('Cooked quantity must be at least 1 for item %s.' % line.item_id.name))
                if line.cooked_qty > line.transfer_qty:
                    raise UserError(
                        _('Cooked quantity cannot be greater than transferred quantity for %s.' % line.item_id.name))

            # Create Transaction Booking record
            transaction_booking = self.env['idil.transaction_booking'].create({
                'reffno': process.name,
                'trx_date': fields.Date.today(),
                'amount': process.subtotal,
                'payment_method': 'internal',  # Assuming 'internal' for this example
                'payment_status': 'pending',
            })

            for line in process.cook_line_ids:
                self.env['idil.transaction_bookingline'].create({
                    'transaction_booking_id': transaction_booking.id,
                    'description': f'Cooked {line.cooked_qty} of {line.item_id.name}',
                    'item_id': line.item_id.id,
                    'account_number': line.item_id.purchase_account_id.id,  # Assuming debit account is 1
                    'transaction_type': 'dr',
                    'dr_amount': line.cooked_amount,
                    'cr_amount': 0,

                    'transaction_date': fields.Date.today(),
                })
                self.env['idil.transaction_bookingline'].create({
                    'transaction_booking_id': transaction_booking.id,
                    'description': f'Cooked {line.cooked_qty} of {line.item_id.name}',
                    'item_id': line.item_id.id,
                    'account_number': process.kitchen_transfer_id.kitchen_id.inventory_account.id,
                    # Use kitchen's inventory account for credit

                    'transaction_type': 'cr',
                    'cr_amount': line.cooked_amount,
                    'dr_amount': 0,
                    'transaction_date': fields.Date.today(),
                })

            process.state = 'processed'


class KitchenCookLine(models.Model):
    _name = 'idil.kitchen.cook.line'
    _description = 'Kitchen Cook Line'

    cook_process_id = fields.Many2one('idil.kitchen.cook.process', string='Cook Process Reference', required=True,
                                      ondelete='cascade')
    item_id = fields.Many2one('idil.item', string='Item', required=True)
    transfer_qty = fields.Float(string='Transferred Quantity', store=True)
    transfer_amount = fields.Float(string='Transferred Amount', store=True)
    cooked_qty = fields.Float(string='Cooked Quantity', required=True)
    unit_price = fields.Float(string='Unit Price', related='item_id.cost_price', readonly=True, store=True)
    cooked_amount = fields.Float(string='Cooked Amount', compute='_compute_cooked_amount', store=True)
    uom_id = fields.Many2one('idil.unit.measure', string='Unit of Measurement', related='item_id.unitmeasure_id',
                             readonly=True)

    @api.depends('cooked_qty', 'unit_price')
    def _compute_cooked_amount(self):
        for line in self:
            line.cooked_amount = line.cooked_qty * line.unit_price

    # @api.constrains('cooked_qty')
    # def _check_cooked_qty(self):
    #     for line in self:
    #         if line.cooked_qty > line.transfer_qty:
    #             raise ValidationError(
    #                 _('Cooked quantity cannot be greater than transferred quantity for %s.' % line.item_id.name))

    @api.onchange('cooked_qty')
    def _onchange_cooked_qty(self):
        if self.cooked_qty > self.transfer_qty:
            raise UserError(
                _('Cooked quantity cannot be greater than transferred quantity for %s.' % self.item_id.name))
