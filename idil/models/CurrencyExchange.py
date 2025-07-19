from datetime import datetime

from odoo import models, fields, api
from odoo.exceptions import ValidationError


class CurrencyExchange(models.Model):
    _name = "idil.currency.exchange"
    _description = "Currency Exchange"
    _order = "id desc"

    name = fields.Char(
        string="Reference", required=True, default="New", copy=False, readonly=True
    )

    sourcecy_currency_id = fields.Many2one(
        "res.currency", string="Source Currency", required=True
    )
    targetcy_currency_id = fields.Many2one(
        "res.currency", string="Target Currency", required=True
    )

    source_account_id = fields.Many2one(
        "idil.chart.account",
        string="Source Account",
        required=True,
        domain="[('currency_id', '=', sourcecy_currency_id)]",
    )
    target_account_id = fields.Many2one(
        "idil.chart.account",
        string="Target Account",
        required=True,
        domain="[('currency_id', '=', targetcy_currency_id)]",
    )
    source_currency_id = fields.Many2one(
        "res.currency",
        related="source_account_id.currency_id",
        readonly=True,
        string="Source Account Currency",
    )
    target_currency_id = fields.Many2one(
        "res.currency",
        related="target_account_id.currency_id",
        readonly=True,
        string="Target Account Currency",
    )
    amount = fields.Float(string="Amount in Source Currency", required=True)

    transaction_date = fields.Date(
        string="Transaction Date", default=fields.Date.context_today, required=True
    )

    source_account_balance = fields.Float(
        string="Account Balance",
        compute="_compute_account_balances",
        currency_field="source_currency_id",
    )
    target_account_balance = fields.Float(
        string="Account Balance",
        compute="_compute_account_balances",
        currency_field="target_currency_id",
    )

    state = fields.Selection(
        [("draft", "Draft"), ("confirmed", "Confirmed")],
        default="draft",
        string="Status",
        tracking=True,
        readonly=True,
    )
    currencycy_id = fields.Many2one(
        "res.currency",
        string="Currency",
        required=True,
        default=lambda self: self.env["res.currency"].search(
            [("name", "=", "SL")], limit=1
        ),
        readonly=True,
    )
    exchange_rate = fields.Float(
        string="Exchange Rate",
        compute="_compute_exchange_rate",
        store=True,
        readonly=True,
        required=True,
        help="Exchange rate from source to target currency",
    )

    @api.depends("currencycy_id")
    def _compute_exchange_rate(self):
        for order in self:
            if order.currencycy_id:
                exchange_rate = self.env["res.currency.rate"].search(
                    [
                        ("currency_id", "=", order.currencycy_id.id),
                        ("name", "=", fields.Date.today()),
                        ("company_id", "=", self.env.company.id),
                    ],
                    limit=1,
                )
                order.exchange_rate = exchange_rate.rate if exchange_rate else 0.0
            else:
                order.exchange_rate = 0.0

    @api.onchange("sourcecy_currency_id")
    def _onchange_source_currency_id(self):
        for rec in self:
            if rec.sourcecy_currency_id.name == "SL":
                rec.targetcy_currency_id = self.env["res.currency"].search(
                    [("name", "=", "USD")], limit=1
                )
            elif rec.sourcecy_currency_id.name == "USD":
                rec.targetcy_currency_id = self.env["res.currency"].search(
                    [("name", "=", "SL")], limit=1
                )
            else:
                rec.targetcy_currency_id = False

    @api.onchange("targetcy_currency_id")
    def _onchange_target_currency_id(self):
        for rec in self:
            if rec.targetcy_currency_id.name == "SL":
                rec.sourcecy_currency_id = self.env["res.currency"].search(
                    [("name", "=", "USD")], limit=1
                )
            elif rec.targetcy_currency_id.name == "USD":
                rec.sourcecy_currency_id = self.env["res.currency"].search(
                    [("name", "=", "SL")], limit=1
                )
            else:
                rec.sourcecy_currency_id = False

    @api.model
    def create(self, vals):
        if vals.get("name", "New") == "New":
            # Generate the name as "Exchange" followed by the current date
            vals["name"] = f"Exchange {datetime.now().strftime('%Y-%m-%d')}"
        return super(CurrencyExchange, self).create(vals)

    @api.depends("source_account_id", "target_account_id")
    def _compute_account_balances(self):
        for record in self:
            # Compute source account balance
            record.source_account_balance = self._get_account_balance(
                record.source_account_id.id
            )
            # Compute target account balance
            record.target_account_balance = self._get_account_balance(
                record.target_account_id.id
            )

    def _get_account_balance(self, account_id):
        if not account_id:
            return 0.0
        self.env.cr.execute(
            """
             SELECT COALESCE(SUM(dr_amount) - SUM(cr_amount), 0) AS balance
             FROM idil_transaction_bookingline
             WHERE account_number = %s
         """,
            (account_id,),
        )
        result = self.env.cr.fetchone()
        return result[0] if result else 0.0

    def perform_exchange(self):
        for record in self:
            if record.state == "confirmed":
                raise ValidationError("This exchange has already been processed.")
            if record.source_currency_id == record.target_currency_id:
                raise ValidationError(
                    "The source and target accounts must have different currencies."
                )

        for record in self:
            if record.amount <= 0:
                raise ValidationError(
                    "The amount to exchange must be greater than zero."
                )

            # Calculate the source account balance
            self.env.cr.execute(
                """
                SELECT SUM(dr_amount) - SUM(cr_amount)
                FROM idil_transaction_bookingline
                WHERE account_number = %s
            """,
                (record.source_account_id.id,),
            )
            source_account_balance = self.env.cr.fetchone()[0] or 0.0

            # Check if there is enough balance in the source account
            if source_account_balance < record.amount:
                raise ValidationError(
                    f"Insufficient balance in the source account. Available balance is {source_account_balance}, "
                    f"but the required amount is {record.amount}."
                )

            # Calculate the equivalent amount in the target currency
            if record.target_currency_id.name == "SL":
                # If the target currency is Somali Shillings, multiply
                equivalent_amount_target = record.amount * record.exchange_rate
            elif record.target_currency_id.name == "USD":
                # Otherwise, divide for other currencies
                equivalent_amount_target = record.amount / record.exchange_rate
            else:
                raise ValidationError("Unsupported target currency.")

            # Get the Exchange Clearing Account for the source currency
            source_clearing_account = self.env["idil.chart.account"].search(
                [
                    ("name", "=", "Exchange Clearing Account"),
                    ("currency_id", "=", record.source_currency_id.id),
                ],
                limit=1,
            )

            # Get the Exchange Clearing Account for the target currency
            target_clearing_account = self.env["idil.chart.account"].search(
                [
                    ("name", "=", "Exchange Clearing Account"),
                    ("currency_id", "=", record.target_currency_id.id),
                ],
                limit=1,
            )

            if not source_clearing_account or not target_clearing_account:
                raise ValidationError(
                    "Please configure the Exchange Clearing Accounts for both currencies with the name"
                    " 'Exchange Clearing Account'."
                )

            # Create transaction booking
            try:
                transaction_booking = self.env["idil.transaction_booking"].create(
                    {
                        "transaction_number": self.env["ir.sequence"].next_by_code(
                            "idil.currency.exchange"
                        ),
                        "reffno": record.name,
                        "currency_exchange_id": record.id,
                        "trx_date": record.transaction_date,
                        "amount": record.amount,
                        "payment_status": "paid",
                        "booking_lines": [
                            # Credit the source account
                            (
                                0,
                                0,
                                {
                                    "description": "Currency Exchange - Credit Source Account",
                                    "account_number": record.source_account_id.id,
                                    "transaction_type": "cr",
                                    "dr_amount": 0.0,
                                    "cr_amount": record.amount,
                                    "transaction_date": record.transaction_date,
                                },
                            ),
                            # Debit the source clearing account
                            (
                                0,
                                0,
                                {
                                    "description": "Currency Exchange - Debit Source Clearing Account",
                                    "account_number": source_clearing_account.id,
                                    "transaction_type": "dr",
                                    "dr_amount": record.amount,
                                    "cr_amount": 0.0,
                                    "transaction_date": record.transaction_date,
                                },
                            ),
                            # Debit the target account
                            (
                                0,
                                0,
                                {
                                    "description": "Currency Exchange - Debit Target Account",
                                    "account_number": record.target_account_id.id,
                                    "transaction_type": "dr",
                                    "dr_amount": equivalent_amount_target,
                                    "cr_amount": 0.0,
                                    "transaction_date": record.transaction_date,
                                },
                            ),
                            # Credit the target clearing account
                            (
                                0,
                                0,
                                {
                                    "description": "Currency Exchange - Credit Target Clearing Account",
                                    "account_number": target_clearing_account.id,
                                    "transaction_type": "cr",
                                    "dr_amount": 0.0,
                                    "cr_amount": equivalent_amount_target,
                                    "transaction_date": record.transaction_date,
                                },
                            ),
                        ],
                    }
                )

                # Check if transaction booking was created successfully
                if not transaction_booking:
                    raise ValidationError(
                        "Transaction booking could not be created. Please check your configuration and try again."
                    )

            except Exception as e:
                raise ValidationError(
                    f"An error occurred while creating the transaction booking: {str(e)}"
                )

    def write(self, vals):
        for record in self:

            res = super(CurrencyExchange, record).write(vals)

            # Delete old transaction booking(s)
            bookings = self.env["idil.transaction_booking"].search(
                [("currency_exchange_id", "=", record.id)]
            )
            bookings.unlink()

            # Re-perform the exchange using updated values
            equivalent_amount_target = (
                record.amount * record.exchange_rate
                if record.target_currency_id.name == "SL"
                else record.amount / record.exchange_rate
            )

            source_clearing_account = self.env["idil.chart.account"].search(
                [
                    ("name", "=", "Exchange Clearing Account"),
                    ("currency_id", "=", record.source_currency_id.id),
                ],
                limit=1,
            )
            target_clearing_account = self.env["idil.chart.account"].search(
                [
                    ("name", "=", "Exchange Clearing Account"),
                    ("currency_id", "=", record.target_currency_id.id),
                ],
                limit=1,
            )

            if not source_clearing_account or not target_clearing_account:
                raise ValidationError(
                    "Please configure the Exchange Clearing Accounts for both currencies with the name 'Exchange Clearing Account'."
                )

            # Calculate the source account balance
            self.env.cr.execute(
                """
                SELECT SUM(dr_amount) - SUM(cr_amount)
                FROM idil_transaction_bookingline
                WHERE account_number = %s
            """,
                (record.source_account_id.id,),
            )
            source_account_balance = self.env.cr.fetchone()[0] or 0.0

            # Check if there is enough balance in the source account
            if source_account_balance < record.amount:
                raise ValidationError(
                    f"Insufficient balance in the source account. Available balance is {source_account_balance}, "
                    f"but the required amount is {record.amount}."
                )

            self.env["idil.transaction_booking"].create(
                {
                    "transaction_number": self.env["ir.sequence"].next_by_code(
                        "idil.currency.exchange"
                    ),
                    "reffno": record.name,
                    "currency_exchange_id": record.id,
                    "trx_date": record.transaction_date,
                    "amount": record.amount,
                    "payment_status": "paid",
                    "booking_lines": [
                        (
                            0,
                            0,
                            {
                                "description": "Currency Exchange - Credit Source Account",
                                "account_number": record.source_account_id.id,
                                "transaction_type": "cr",
                                "dr_amount": 0.0,
                                "cr_amount": record.amount,
                                "transaction_date": record.transaction_date,
                            },
                        ),
                        (
                            0,
                            0,
                            {
                                "description": "Currency Exchange - Debit Source Clearing Account",
                                "account_number": source_clearing_account.id,
                                "transaction_type": "dr",
                                "dr_amount": record.amount,
                                "cr_amount": 0.0,
                                "transaction_date": record.transaction_date,
                            },
                        ),
                        (
                            0,
                            0,
                            {
                                "description": "Currency Exchange - Debit Target Account",
                                "account_number": record.target_account_id.id,
                                "transaction_type": "dr",
                                "dr_amount": equivalent_amount_target,
                                "cr_amount": 0.0,
                                "transaction_date": record.transaction_date,
                            },
                        ),
                        (
                            0,
                            0,
                            {
                                "description": "Currency Exchange - Credit Target Clearing Account",
                                "account_number": target_clearing_account.id,
                                "transaction_type": "cr",
                                "dr_amount": 0.0,
                                "cr_amount": equivalent_amount_target,
                                "transaction_date": record.transaction_date,
                            },
                        ),
                    ],
                }
            )

        return res
