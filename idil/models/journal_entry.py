from odoo import models, fields, api, _
from odoo.exceptions import ValidationError


class JournalEntry(models.Model):
    _name = 'idil.journal.entry'
    _description = 'Journal Entry'

    name = fields.Char(string='Journal no', required=True, copy=False, readonly=True, index=True,
                       default=lambda self: _('New'))
    date = fields.Date(string='Journal Date', required=True, default=fields.Date.context_today)
    line_ids = fields.One2many('idil.journal.entry.line', 'entry_id', string='Journal Lines')
    currency_id = fields.Many2one('res.currency', string='Currency', required=True,
                                  default=lambda self: self.env.company.currency_id)

    total_debit = fields.Monetary(string='Total Debit', compute='_compute_totals', store=True)
    total_credit = fields.Monetary(string='Total Credit', compute='_compute_totals', store=True)

    @api.model
    def default_get(self, fields_list):
        res = super(JournalEntry, self).default_get(fields_list)
        if 'line_ids' in fields_list:
            res.update({
                'line_ids': [(0, 0, {}) for _ in range(8)]
            })
        return res

    @api.model
    def create(self, vals):
        if vals.get('name', _('New')) == _('New'):
            vals['name'] = self.env['ir.sequence'].next_by_code('idil.journal.entry') or _('New')

        # Filter out empty lines
        if 'line_ids' in vals:
            vals['line_ids'] = [line for line in vals['line_ids'] if line[2].get('account_id')]

        result = super(JournalEntry, self).create(vals)
        result.validate_account_balances()
        result.create_transaction_booking()

        return result

    def write(self, vals):
        result = super(JournalEntry, self).write(vals)
        for entry in self:
            entry.validate_account_balances()
            entry.update_transaction_booking()
        return result

    def unlink(self):
        for entry in self:
            self.env['idil.transaction_booking'].search([('journal_entry_id', '=', entry.id)]).unlink()
        return super(JournalEntry, self).unlink()

    @api.depends('line_ids.debit', 'line_ids.credit')
    def _compute_totals(self):
        for entry in self:
            entry.total_debit = sum(line.debit for line in entry.line_ids)
            entry.total_credit = sum(line.credit for line in entry.line_ids)

    @api.constrains('line_ids')
    def _check_debit_credit(self):
        for entry in self:
            if entry.total_debit != entry.total_credit:
                raise ValidationError(
                    _('Total debit (%s) is not equal to total credit (%s).') % (entry.total_debit, entry.total_credit))

    def validate_account_balances(self):
        for entry in self:
            for line in entry.line_ids:
                account = self.env['idil.chart.account'].browse(line.account_id.id)
                account_balance = self.env['idil.transaction_bookingline'].search([
                    ('account_number', '=', account.id)
                ])
                debit_total = sum(line.dr_amount for line in account_balance)
                credit_total = sum(line.cr_amount for line in account_balance)
                current_balance = debit_total - credit_total

                if account.sign == 'Dr':
                    if line.credit and current_balance < line.credit:
                        raise ValidationError(
                            _('Insufficient funds in account ( %s ) for credit amount %s. '
                              'The current account balance is %s.') % (account.name, line.credit, current_balance))
                elif account.sign == 'Cr':
                    if line.debit and current_balance < line.debit:
                        raise ValidationError(
                            _('Insufficient funds in account ( %s ) for debit amount %s. '
                              'The current account balance is %s.') % (account.name, line.debit, current_balance))

    def get_manual_transaction_source_id(self):
        trx_source = self.env['idil.transaction.source'].search([('name', '=', 'Manual Transaction')], limit=1)
        if not trx_source:
            raise ValidationError(_('Transaction source "Manual Transaction" not found.'))
        return trx_source.id

    def create_transaction_booking(self):
        trx_source_id = self.get_manual_transaction_source_id()
        for entry in self:
            # Remove existing transaction bookings
            self.env['idil.transaction_booking'].search([('journal_entry_id', '=', entry.id)]).unlink()

            booking_vals = {
                'transaction_number': self.env['ir.sequence'].next_by_code('idil.transaction_booking.sequence') or _(
                    'New'),
                'reffno': entry.name,
                'trx_date': entry.date,
                'amount': entry.total_debit,  # Assuming total_debit equals the total amount of the transaction
                'debit_total': entry.total_debit,
                'credit_total': entry.total_credit,
                'payment_method': "other",
                'payment_status': "paid",
                'trx_source_id': trx_source_id,

                'journal_entry_id': entry.id,  # Link to the journal entry
            }
            main_booking = self.env['idil.transaction_booking'].create(booking_vals)
            for line in entry.line_ids:
                if not line.account_id:
                    continue  # Skip lines without an account_id
                if line.debit:
                    self.env['idil.transaction_bookingline'].create({
                        'transaction_booking_id': main_booking.id,
                        'description': line.description,
                        'account_number': line.account_id.id,
                        'transaction_type': 'dr',
                        'dr_amount': line.debit,
                        'cr_amount': 0,
                        'transaction_date': entry.date
                    })
                if line.credit:
                    self.env['idil.transaction_bookingline'].create({
                        'transaction_booking_id': main_booking.id,
                        'description': line.description,
                        'account_number': line.account_id.id,
                        'transaction_type': 'cr',
                        'cr_amount': line.credit,
                        'dr_amount': 0,
                        'transaction_date': entry.date
                    })

    def update_transaction_booking(self):
        for entry in self:
            # Remove existing transaction bookings
            self.env['idil.transaction_booking'].search([('journal_entry_id', '=', entry.id)]).unlink()

            booking_vals = {
                'transaction_number': self.env['ir.sequence'].next_by_code('idil.transaction_booking.sequence') or _(
                    'New'),
                'reffno': entry.name,
                'trx_date': entry.date,
                'amount': entry.total_debit,  # Assuming total_debit equals the total amount of the transaction
                'debit_total': entry.total_debit,
                'credit_total': entry.total_credit,
                'journal_entry_id': entry.id,  # Link to the journal entry
            }
            main_booking = self.env['idil.transaction_booking'].create(booking_vals)
            for line in entry.line_ids:
                if not line.account_id:
                    continue  # Skip lines without an account_id
                if line.debit:
                    self.env['idil.transaction_bookingline'].create({
                        'transaction_booking_id': main_booking.id,
                        'description': line.description,
                        'account_number': line.account_id.id,
                        'transaction_type': 'dr',
                        'dr_amount': line.debit,
                        'cr_amount': 0,
                        'transaction_date': entry.date
                    })
                if line.credit:
                    self.env['idil.transaction_bookingline'].create({
                        'transaction_booking_id': main_booking.id,
                        'description': line.description,
                        'account_number': line.account_id.id,
                        'transaction_type': 'cr',
                        'cr_amount': line.credit,
                        'dr_amount': 0,
                        'transaction_date': entry.date
                    })


class JournalEntryLine(models.Model):
    _name = 'idil.journal.entry.line'
    _description = 'Journal Entry Line'

    entry_id = fields.Many2one('idil.journal.entry', string='Journal Entry', required=True, ondelete='cascade')
    account_id = fields.Many2one('idil.chart.account', string='Account', required=True)
    debit = fields.Monetary(string='Debit', currency_field='currency_id', store=True)
    credit = fields.Monetary(string='Credit', currency_field='currency_id', store=True)
    description = fields.Char(string='Description')
    name = fields.Char(string='Name')
    currency_id = fields.Many2one('res.currency', string='Currency', related='account_id.currency_id', store=True,
                                  readonly=True)

    @api.onchange('debit')
    def _onchange_debit(self):
        if self.debit:
            self.credit = 0

    @api.onchange('credit')
    def _onchange_credit(self):
        if self.credit:
            self.debit = 0

    @api.onchange('account_id')
    def _onchange_account_id(self):
        if self.account_id and self.currency_id:
            accounts = self.env['idil.chart.account'].search([('currency_id', '=', self.currency_id.id)])
            return {'domain': {'account_id': [('id', 'in', accounts.ids)]}}
