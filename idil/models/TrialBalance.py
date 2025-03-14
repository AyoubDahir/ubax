from datetime import datetime

from odoo import models, fields, api, exceptions
from odoo.exceptions import UserError, ValidationError
import re
import logging

_logger = logging.getLogger(__name__)


class TrialBalanceWizard(models.TransientModel):
    _name = 'idil.trial.balance.wizard'
    _description = 'Trial Balance Wizard'

    report_currency_id = fields.Many2one('res.currency', string='Report Currency', required=True)

    def action_compute_trial_balance(self):
        self.ensure_one()
        action = self.env['idil.transaction_bookingline'].compute_trial_balance(self.report_currency_id)
        action['``context```'] = {'default_name': f'Trial Balance for {self.report_currency_id.name}'}
        return action


class TrialBalance(models.Model):
    _name = 'idil.trial.balance'
    _description = 'Trial Balance'

    account_number = fields.Many2one('idil.chart.account', string='Account Number')
    header_name = fields.Char(string='Account Type')
    dr_balance = fields.Float(string='Dr')
    cr_balance = fields.Float(string='Cr')
    currency_id = fields.Many2one('res.currency', string='Currency', related='account_number.currency_id', store=True,
                                  readonly=True)
    label = fields.Char(string='Label', compute='_compute_label')

    @api.depends('account_number', 'dr_balance', 'cr_balance')
    def _compute_label(self):
        for record in self:
            if not record.account_number:
                record.label = 'Grand Total'

            else:
                record.label = ''
