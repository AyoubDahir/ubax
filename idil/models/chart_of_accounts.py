from odoo import models, fields, api
from odoo.exceptions import ValidationError, UserError
import logging

_logger = logging.getLogger(__name__)


class AccountHeader(models.Model):
    _name = 'idil.chart.account.header'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _description = 'Idil Chart of Accounts Header'

    code = fields.Char(string='Header Code', required=True)
    name = fields.Char(string='Header Name', required=True)
    company_id = fields.Many2one('res.company', string='Company', required=False)  # Add this line

    sub_header_ids = fields.One2many('idil.chart.account.subheader', 'header_id', string='Sub Headers')

    # @api.model
    # def get_bs_report_data(self, currency_id, report_date):
    #     headers = self.search([])
    #     report_data = []
    #     currency_obj = self.env['res.currency'].browse(currency_id)
    #
    #     for header in headers:
    #         header_total = 0
    #         subheaders_data = []
    #
    #         for subheader in header.sub_header_ids:
    #             subheader_total = 0
    #             accounts_data = []
    #             currency_symbol = currency_obj.symbol  # Default to the report's main currency symbol
    #
    #             for account in subheader.account_ids:
    #                 if account.FinancialReporting == 'BS' and account.currency_id.id == currency_id:
    #                     balance = account.get_balance_as_of_date(report_date)  # Modify this method to handle date
    #                     formatted_balance = "{:,.2f}".format(balance)
    #
    #                     accounts_data.append({
    #                         'account_code': account.code,
    #                         'account_name': account.name,
    #                         'balance': formatted_balance,
    #                         'currency_symbol': account.currency_id.symbol
    #                     })
    #                     subheader_total += balance
    #
    #             if accounts_data:
    #                 currency_symbol = accounts_data[-1]['currency_symbol']
    #                 formatted_subheader_total = "{:,.2f}".format(subheader_total)
    #
    #                 subheaders_data.append({
    #                     'sub_header_name': subheader.name,
    #                     'accounts': accounts_data,
    #                     'sub_header_total': formatted_subheader_total,
    #                     'currency_symbol': currency_symbol
    #                 })
    #
    #             header_total += subheader_total
    #             formatted_header_total = "{:,.2f}".format(header_total)
    #
    #         if subheaders_data:
    #             report_data.append({
    #                 'header_name': header.name,
    #                 'sub_headers': subheaders_data,
    #                 'header_total': formatted_header_total,
    #                 'currency_symbol': currency_obj.symbol
    #             })
    #
    #     return report_data

    # @api.model
    # def get_bs_report_data(self, currency_id, report_date):
    #     headers = self.search([])
    #     report_data = []
    #     currency_obj = self.env['res.currency'].browse(currency_id)
    #
    #     total_income = 0
    #     total_expenses = 0
    #
    #     for header in headers:
    #         header_total = 0
    #         subheaders_data = []
    #         currency_symbol = currency_obj.symbol
    #
    #         for subheader in header.sub_header_ids:
    #             subheader_total = 0
    #             accounts_data = []
    #
    #             for account in subheader.account_ids:
    #                 if account.FinancialReporting == 'BS' and account.currency_id.id == currency_id:
    #                     balance = account.get_balance_as_of_date(report_date)
    #                     formatted_balance = "{:,.2f}".format(balance)
    #
    #                     accounts_data.append({
    #                         'account_code': account.code,
    #                         'account_name': account.name,
    #                         'balance': formatted_balance,
    #                         'currency_symbol': account.currency_id.symbol
    #                     })
    #
    #                     subheader_total += balance
    #
    #                     # Track income and expenses separately
    #                     if account.code.startswith('4'):
    #                         total_income += balance
    #                     elif account.code.startswith('5'):
    #                         total_expenses += balance
    #
    #             if accounts_data:
    #                 currency_symbol = accounts_data[-1]['currency_symbol']
    #                 formatted_subheader_total = "{:,.2f}".format(subheader_total)
    #
    #                 subheaders_data.append({
    #                     'sub_header_name': subheader.name,
    #                     'accounts': accounts_data,
    #                     'sub_header_total': formatted_subheader_total,
    #                     'currency_symbol': currency_symbol
    #                 })
    #
    #             header_total += subheader_total
    #             formatted_header_total = "{:,.2f}".format(header_total)
    #
    #         if subheaders_data:
    #             report_data.append({
    #                 'header_name': header.name,
    #                 'sub_headers': subheaders_data,
    #                 'header_total': formatted_header_total,
    #                 'currency_symbol': currency_obj.symbol
    #             })
    #
    #     # Calculate net profit/loss
    #     net_profit_loss = total_income - total_expenses
    #     formatted_net_profit_loss = "{:,.2f}".format(net_profit_loss)
    #
    #     report_data.append({
    #         'header_name': 'Net Profit/Loss',
    #         'sub_headers': [{
    #             'sub_header_name': 'Net Profit/Loss',
    #             'accounts': [],
    #             'sub_header_total': formatted_net_profit_loss,
    #             'currency_symbol': currency_obj.symbol
    #         }],
    #         'header_total': formatted_net_profit_loss,
    #         'currency_symbol': currency_obj.symbol
    #     })
    #
    #     return report_data
    # @api.model
    # def get_bs_report_data(self, currency_id, report_date):
    #     headers = self.search([])
    #     report_data = []
    #     currency_obj = self.env['res.currency'].browse(currency_id)
    #
    #     total_income = 0
    #     total_expenses = 0
    #
    #     for header in headers:
    #         header_total = 0
    #         subheaders_data = []
    #
    #         for subheader in header.sub_header_ids:
    #             subheader_total = 0
    #             accounts_data = []
    #
    #             for account in subheader.account_ids:
    #                 if account.FinancialReporting == 'BS':
    #                     account_currency = account.currency_id
    #                     if account_currency.id != currency_id:
    #                         # Convert balance to USD
    #                         conversion_rate = self._get_conversion_rate(account_currency.id, currency_id, report_date)
    #                         if not conversion_rate:
    #                             raise UserError(_('Conversion rate not found for %s to %s as of %s') % (
    #                                 account_currency.name, currency_obj.name, report_date))
    #                         balance = account.get_balance_as_of_date(report_date) * conversion_rate
    #                     else:
    #                         # Balance is already in USD
    #                         balance = account.get_balance_as_of_date(report_date)
    #
    #                     formatted_balance = "{:,.2f}".format(balance)
    #                     accounts_data.append({
    #                         'account_code': account.code,
    #                         'account_name': account.name,
    #                         'balance': formatted_balance,
    #                         'currency_symbol': currency_obj.symbol
    #                     })
    #
    #                     subheader_total += balance
    #
    #                     # Track income and expenses separately
    #                     if account.code.startswith('4'):
    #                         total_income += balance
    #                     elif account.code.startswith('5'):
    #                         total_expenses += balance
    #
    #             if accounts_data:
    #                 formatted_subheader_total = "{:,.2f}".format(subheader_total)
    #                 subheaders_data.append({
    #                     'sub_header_name': subheader.name,
    #                     'accounts': accounts_data,
    #                     'sub_header_total': formatted_subheader_total,
    #                     'currency_symbol': currency_obj.symbol
    #                 })
    #
    #             header_total += subheader_total
    #             formatted_header_total = "{:,.2f}".format(header_total)
    #
    #         if subheaders_data:
    #             report_data.append({
    #                 'header_name': header.name,
    #                 'sub_headers': subheaders_data,
    #                 'header_total': formatted_header_total,
    #                 'currency_symbol': currency_obj.symbol
    #             })
    #
    #     # Calculate net profit/loss
    #     net_profit_loss = total_income - total_expenses
    #     formatted_net_profit_loss = "{:,.2f}".format(net_profit_loss)
    #
    #     report_data.append({
    #         'header_name': 'Net Profit/Loss',
    #         'sub_headers': [{
    #             'sub_header_name': 'Net Profit/Loss',
    #             'accounts': [],
    #             'sub_header_total': formatted_net_profit_loss,
    #             'currency_symbol': currency_obj.symbol
    #         }],
    #         'header_total': formatted_net_profit_loss,
    #         'currency_symbol': currency_obj.symbol
    #     })
    #
    #     return report_data
    #
    # def _get_conversion_rate(self, from_currency_id, to_currency_id, date):
    #     currency_rate_obj = self.env['res.currency.rate']
    #     rate = currency_rate_obj.search([
    #         ('currency_id', '=', from_currency_id),
    #         ('name', '<=', date)
    #     ], limit=1, order='name desc')
    #     if rate:
    #         return rate.rate / rate.currency_id.rate  # Convert to the target currency
    #     return False
    @api.model
    def get_bs_report_data(self, company_id, report_date):
        # Retrieve all headers without filtering by company
        headers = self.search([])
        print(f"Number of headers found: {len(headers)}")

        for header in headers:
            print(f"Processing Header: {header.name} with {len(header.sub_header_ids)} sub-headers")
            for subheader in header.sub_header_ids:
                print(f"Processing Sub-header: {subheader.name} with {len(subheader.account_ids)} accounts")
                for account in subheader.account_ids:
                    # Pass company_id to get balance only for the selected company
                    balance = account.get_balance_as_of_date_for_bs(report_date, company_id)
                    print(f"Account: {account.name}, Balance: {balance}")

        report_data = []
        usd_currency = self.env.ref('base.USD')  # Ensure this XML ID is correct

        total_income = 0
        total_expenses = 0

        for header in headers:
            header_total = 0
            subheaders_data = []

            for subheader in header.sub_header_ids:
                subheader_total = 0
                accounts_data = []

                for account in subheader.account_ids:
                    if account.FinancialReporting == 'BS':
                        balance = account.get_balance_as_of_date_for_bs(report_date, company_id)

                        formatted_balance = "{:,.3f}".format(balance)
                        accounts_data.append({
                            'account_code': account.code,
                            'account_name': account.name,
                            'balance': formatted_balance,
                            'currency_symbol': usd_currency.symbol
                        })

                        subheader_total += balance

                        # Track income and expenses separately
                        if account.code.startswith('4'):
                            total_income += balance
                        elif account.code.startswith('5'):
                            total_expenses += balance

                if accounts_data:
                    formatted_subheader_total = "{:,.3f}".format(subheader_total)
                    subheaders_data.append({
                        'sub_header_name': subheader.name,
                        'accounts': accounts_data,
                        'sub_header_total': formatted_subheader_total,
                        'currency_symbol': usd_currency.symbol
                    })

                header_total += subheader_total
                formatted_header_total = "{:,.3f}".format(header_total)

            if subheaders_data:
                report_data.append({
                    'header_name': header.name,
                    'sub_headers': subheaders_data,
                    'header_total': formatted_header_total,
                    'currency_symbol': usd_currency.symbol
                })

        # Calculate net profit/loss
        net_profit_loss = total_income - total_expenses
        formatted_net_profit_loss = "{:,.3f}".format(net_profit_loss)

        report_data.append({
            'header_name': 'Net Profit/Loss',
            'sub_headers': [{
                'sub_header_name': 'Net Profit/Loss',
                'accounts': [],
                'sub_header_total': formatted_net_profit_loss,
                'currency_symbol': usd_currency.symbol
            }],
            'header_total': formatted_net_profit_loss,
            'currency_symbol': usd_currency.symbol
        })

        return report_data

    class ReportCurrencyWizard(models.TransientModel):
        _name = 'report.currency.wizard'
        _description = 'Currency Selection Wizard for Reports'

        company_id = fields.Many2one('res.company', string='Company', required=True,
                                     help='Select the company for the report.')
        report_date = fields.Date(string="Report Date", required=True,
                                  default=fields.Date.context_today,
                                  help="Select the date for which the report is to be generated.")

        def generate_report(self):
            self.ensure_one()
            data = {
                'report_name': 'Balance Sheet for ' + self.company_id.name,
                'report_date': self.report_date,  # Pass the selected date to the report
                'company_id': self.company_id.id  # Pass the selected company to the report
            }
            context = dict(self.env.context, company_id=self.company_id.id)
            return {
                'type': 'ir.actions.report',
                'report_name': 'idil.report_bs_template',
                'report_type': 'qweb-html',
                'context': context,
                'data': data
            }

    # class ReportCurrencyWizard(models.TransientModel):
    #     _name = 'report.currency.wizard'
    #     _description = 'Currency Selection Wizard for Reports'
    #
    #     currency_id = fields.Many2one('res.currency', string='Currency', required=True,
    #                                   help='Select the currency for the report.')
    #     report_date = fields.Date(string="Report Date", required=True,
    #                               default=fields.Date.context_today,
    #                               help="Select the date for which the report is to be generated.")
    #
    #     def generate_report(self):
    #         self.ensure_one()
    #         data = {
    #             'currency_id': self.currency_id.id,
    #             'report_name': 'Balance Sheet for ' + self.currency_id.name,
    #             # Custom dynamic report name based on currency
    #             'report_date': self.report_date  # Pass the selected date to the report
    #         }
    #         context = dict(self.env.context, currency_id=self.currency_id.id)
    #         return {
    #             'type': 'ir.actions.report',
    #             'report_name': 'idil.report_bs_template',
    #             'report_type': 'qweb-html',
    #             'context': context,
    #             'data': data
    #         }

    @api.model
    def get_pl_report_data(self, currency_id, report_date):
        headers = self.search([('code', 'in', ['4', '5', '6'])])  # Adjust the domain as needed
        report_data = []
        currency_obj = self.env['res.currency'].browse(currency_id)

        for header in headers:
            header_data = {
                'header_name': header.name,
                'subheaders': []
            }
            for subheader in header.sub_header_ids:
                subheader_data = {
                    'sub_header_name': subheader.name,
                    'accounts': [],
                    'subheader_total': 0.0
                }
                for account in subheader.account_ids:
                    if account.currency_id.id == currency_id:
                        balance = account.get_balance_as_of_date(report_date)
                        subheader_data['accounts'].append({
                            'account_code': account.code,
                            'account_name': account.name,
                            'balance': "{:,.2f}".format(balance),
                            'currency_symbol': account.currency_id.symbol
                        })
                        subheader_data['subheader_total'] += balance

                subheader_data['subheader_total'] = "{:,.2f}".format(subheader_data['subheader_total'])
                header_data['subheaders'].append(subheader_data)

            report_data.append(header_data)

        return {
            'report_date': report_date,
            'currency_symbol': currency_obj.symbol,
            'report_data': report_data
        }


# class ReportCurrencyWizard(models.TransientModel):
#     _name = 'report.currency.wizard'
#     _description = 'Currency Selection Wizard for Reports'
#
#     currency_id = fields.Many2one('res.currency', string='Currency', required=True,
#                                   help='Select the currency for the report.')
#     report_date = fields.Date(string="Report Date", required=True,
#                               default=fields.Date.context_today,
#                               help="Select the date for which the report is to be generated.")
#
#     def generate_report(self):
#         self.ensure_one()
#         data = {
#             'currency_id': self.currency_id.id,
#             'report_name': 'Balance Sheet for ' + self.currency_id.name,  # Custom dynamic report name based on currency
#             'report_date': self.report_date  # Pass the selected date to the report
#         }
#         context = dict(self.env.context, currency_id=self.currency_id.id)
#         return {
#             'type': 'ir.actions.report',
#             'report_name': 'idil.report_bs_template',
#             'report_type': 'qweb-html',
#             'context': context,
#             'data': data
#         }


class IncomeReportCurrencyWizard(models.TransientModel):
    _name = 'report.income.currency.wizard'
    _description = 'Currency Selection Wizard for Income Reports'

    currency_id = fields.Many2one('res.currency', string='Currency', required=True,
                                  help='Select the currency for the Income report.')
    report_date = fields.Date(string="Report Date", required=True,
                              default=fields.Date.context_today,
                              help="Select the date for which the Income report is to be generated.")

    def generate_income_report(self):
        self.ensure_one()
        data = {
            'currency_id': self.currency_id.id,
            'report_date': self.report_date  # Pass the selected date to the report
        }
        context = dict(self.env.context, currency_id=self.currency_id.id)
        return {
            'type': 'ir.actions.report',
            'report_name': 'idil.report_income_statement_template',
            'report_type': 'qweb-html',
            'context': context,
            'data': data
        }


class AccountSubHeader(models.Model):
    _name = 'idil.chart.account.subheader'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _description = 'Idil Chart of Accounts Sub Header'

    sub_header_code = fields.Char(string='Sub Header Code', required=True)
    name = fields.Char(string='Sub Header Name', required=True)
    header_id = fields.Many2one('idil.chart.account.header', string='Header')
    account_ids = fields.One2many('idil.chart.account', 'subheader_id', string='Accounts')

    @api.constrains('sub_header_code')
    def _check_subheader_code_length(self):
        for subheader in self:
            if len(subheader.sub_header_code) != 6:
                raise ValidationError("Sub Header Code must be 6 characters long.")

    @api.constrains('sub_header_code', 'header_id')
    def _check_subheader_assignment(self):
        for subheader in self:
            header_code = subheader.header_id.code[:3]
            subheader_code = subheader.sub_header_code[:3]
            if not subheader_code.startswith(header_code):
                raise ValidationError("The first three digits of Sub Header Code must match the Header Code.")


class Account(models.Model):
    _name = 'idil.chart.account'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _description = 'Idil Chart of Accounts'

    SIGN_SELECTION = [
        ('Dr', 'Dr'),
        ('Cr', 'Cr'),

    ]

    FINANCIAL_REPORTING_SELECTION = [
        ('BS', 'Balance Sheet'),
        ('PL', 'Profit and Loss'),
    ]
    account_type = [
        ("cash", "Cash"),
        ("bank_transfer", "Bank"),
        ("payable", "Account Payable"),
        ("discount", "Account Discount"),
        ("commission", "Account Commission"),
        ("receivable", "Account Receivable"),
        ("COGS", "COGS"),
        ("kitchen", "kitchen"),
        ("Owners Equity", "Owners Equity"),
        ("Adjustment", "Adjustment"),
    ]

    code = fields.Char(string='Account Code', required=True, tracking=True)
    name = fields.Char(string='Account Name', required=True, tracking=True)
    sign = fields.Selection(
        SIGN_SELECTION,
        string='Account Sign',
        compute='_compute_account_sign',
        store=True,
        tracking=True)
    FinancialReporting = fields.Selection(
        FINANCIAL_REPORTING_SELECTION,
        string='Financial Reporting',
        compute='_compute_financial_reporting',
        store=True,
        tracking=True
    )
    account_type = fields.Selection(
        account_type,
        string='Account Type',
        store=True,
        tracking=True
    )
    subheader_id = fields.Many2one('idil.chart.account.subheader', string='Sub Header', required=True, tracking=True)

    subheader_code = fields.Char(related='subheader_id.sub_header_code', string='Sub Header Code', readonly=True)
    subheader_name = fields.Char(related='subheader_id.name', string='Sub Header Name', readonly=True)
    header_code = fields.Char(related='subheader_id.header_id.code', string='Header Code', readonly=True)

    header_name = fields.Char(related='subheader_id.header_id.name', string='Header Name', readonly=True, store=True)
    # Add currency field
    currency_id = fields.Many2one('res.currency', string='Currency', required=True)

    balance = fields.Float(string='Current Balance', compute='_compute_balance', store=True)

    transaction_bookingline_ids = fields.One2many(
        'idil.transaction_bookingline', 'account_number', string='Transaction Booking Lines'
    )

    @api.depends('transaction_bookingline_ids.dr_amount', 'transaction_bookingline_ids.cr_amount')
    def _compute_balance(self):
        for account in self:
            # Clear the balance before calculation
            account.balance = 0
            debit_sum = sum(
                account.transaction_bookingline_ids.filtered(lambda l: l.transaction_type == 'dr').mapped('dr_amount'))
            credit_sum = sum(
                account.transaction_bookingline_ids.filtered(lambda l: l.transaction_type == 'cr').mapped('cr_amount'))
            account.balance = debit_sum - credit_sum

    @api.model
    def read_group(self, domain, fields, groupby, offset=0, limit=None, orderby=False, lazy=True):
        if 'balance' in fields:
            fields.remove('balance')
        res = super(Account, self).read_group(domain, fields, groupby, offset, limit, orderby, lazy)
        if 'balance' not in fields:
            fields.append('balance')
        if 'balance' in fields:
            for line in res:
                if '__domain' in line:
                    accounts = self.search(line['__domain'])
                    # Ensure balances are computed
                    accounts._compute_balance()
                    balance = sum(account.balance for account in accounts)
                    line['balance'] = balance
        return res

    @api.model
    def read(self, fields=None, load='_classic_read'):
        res = super(Account, self).read(fields, load)
        for record in self:
            record._compute_balance()
        return res

    def name_get(self):
        result = []
        for record in self:
            name = f"{record.name} ({record.currency_id.name})"
            result.append((record.id, name))
        return result

    @api.depends('code')
    def _compute_account_sign(self):
        for account in self:
            if account.code:
                first_digit = account.code[0]
                # Determine sign based on the first digit of the account code
                if first_digit in ['1', '5', '6', '8']:  # Dr accounts
                    account.sign = 'Dr'
                elif first_digit in ['2', '3', '4', '7', '9']:  # Cr accounts
                    account.sign = 'Cr'
                else:
                    account.sign = False
            else:
                account.sign = False

    @api.depends('code')
    def _compute_financial_reporting(self):
        for account in self:
            if account.code:
                first_digit = account.code[0]
                # Determine financial reporting based on the first digit of the account code
                if first_digit in ['1', '2', '3']:  # Assuming 1, 2, 3 represent BS, adjust as needed
                    account.FinancialReporting = 'BS'
                elif first_digit in ['4', '5', '6', '7', '8', '9']:  # Assuming 4, 5 represent PL, adjust as needed
                    account.FinancialReporting = 'PL'
                else:
                    account.FinancialReporting = False
            else:
                account.FinancialReporting = False

    def get_balance_as_of_date(self, date):
        self.ensure_one()  # Ensures this is called on a single record
        transactions = self.env['idil.transaction_bookingline'].search([
            ('account_number', '=', self.id),
            ('transaction_date', '<=', date)  # Filter transactions up to the specified date
        ])
        debit = sum(transaction.dr_amount for transaction in transactions if transaction.transaction_type == 'dr')
        credit = sum(transaction.cr_amount for transaction in transactions if transaction.transaction_type == 'cr')
        return abs(debit - credit)

    @api.model
    def get_balance_as_of_date_for_bs(self, date, company_id):
        self.ensure_one()

        # Fetch transactions for the specific account, date, and company
        transactions = self.env['idil.transaction_bookingline'].search([
            ('account_number', '=', self.id),
            ('transaction_date', '<=', date),
            ('company_id', '=', company_id)  # Ensure transactions are filtered by company
        ])

        total_debit = 0
        total_credit = 0

        if not transactions:
            print(f"No transactions found for Account: {self.name} up to {date} for Company ID: {company_id}")

        for transaction in transactions:
            print(
                f"Processing Transaction ID: {transaction.id} on {transaction.transaction_date} with amount {transaction.dr_amount if transaction.transaction_type == 'dr' else transaction.cr_amount}")

            conversion_rate = self._get_conversion_rate(
                transaction.currency_id.id,
                transaction.transaction_date
            )

            if not conversion_rate:
                raise UserError(_('Conversion rate not found for %s to %s as of %s') % (
                    transaction.currency_id.name,
                    self.env.ref('base.USD').name,
                    transaction.transaction_date
                ))

            if transaction.transaction_type == 'dr':
                total_debit += transaction.dr_amount / conversion_rate
            elif transaction.transaction_type == 'cr':
                total_credit += transaction.cr_amount / conversion_rate

        balance = total_debit - total_credit
        print(f"Final Balance for Account: {self.name} as of {date}: {balance}")

        return balance

    @api.model
    def _get_conversion_rate(self, from_currency_id, date):
        # Get the currency models
        from_currency = self.env['res.currency'].browse(from_currency_id)
        to_currency = self.env.ref('base.USD')  # Assuming USD is the to_currency, adjust as needed

        # Ensure currencies are valid
        if not from_currency or not to_currency:
            raise UserError(_('Invalid currency provided'))

        # Get the currency rate model
        currency_rate = self.env['res.currency.rate']

        # Search for the conversion rate
        # We should filter by 'currency_id' (the currency field) and not 'rate_currency_id'
        rate_record = currency_rate.search([
            ('currency_id', '=', from_currency.id),
            ('name', '<=', date)
        ], limit=1, order='name desc')

        if not rate_record:
            raise UserError(_('Conversion rate not found for %s to %s as of %s') % (
                from_currency.name,
                to_currency.name,
                date
            ))

        return rate_record.rate


class AccountBalanceReport(models.TransientModel):
    _name = 'idil.account.balance.report'
    _description = 'Account Balance Report'

    type = fields.Char(string="Type")
    subtype = fields.Char(string="subtype")
    account_name = fields.Char(string="Account Name")
    # account_code = fields.Char(string="Account Code")
    account_id = fields.Many2one('idil.chart.account', string="Account", store=True)
    balance = fields.Float(compute='_compute_balance', store=True)
    currency_id = fields.Many2one('res.currency', string='Currency', related='account_id.currency_id', store=True,
                                  readonly=True)

    @api.depends('account_id')
    def _compute_balance(self):
        for report in self:
            # Initialize balance to 0 for each report entry
            report.balance = 0
            # Find transactions related to this account_code
            transactions = self.env['idil.transaction_bookingline'].search(
                [('account_number', '=', report.account_id.id)])
            debit = sum(transactions.filtered(lambda r: r.transaction_type == 'dr').mapped('dr_amount'))
            credit = sum(transactions.filtered(lambda r: r.transaction_type == 'cr').mapped('cr_amount'))
            # Calculate balance
            report.balance = abs(debit - credit)

    @api.model
    def generate_account_balances_report(self):
        self.search([]).unlink()  # Clear existing records to avoid stale data

        account_balances = self._get_account_balances()
        for balance in account_balances:
            self.create({
                'type': balance['type'],
                'subtype': balance['subtype'],
                'account_name': balance['account_name'],
                'account_id': balance['account_id'],

            })

        return {
            'type': 'ir.actions.act_window',
            'name': 'Account Balances',
            'view_mode': 'tree',
            'res_model': 'idil.account.balance.report',
            'domain': [('balance', '<>', 0)],  # Ensures only accounts with non-zero balances are shown
            'context': {'group_by': ['type', 'subtype']},
            'target': 'new',
        }

    def _get_account_balances(self):
        account_balances = []
        accounts = self.env['idil.chart.account'].search([])

        for account in accounts:
            account_balances.append({
                'type': account.header_name,
                'subtype': account.subheader_name,
                'account_name': account.name,
                'account_id': account.id,
            })
        return account_balances
