from odoo import models, fields, api, exceptions
from datetime import datetime
from datetime import date
import re
from odoo.exceptions import ValidationError, UserError
import logging

_logger = logging.getLogger(__name__)


class SalesOpeningBalance(models.Model):
    _name = "idil.sales.opening.balance"
    _description = "Sales Team Opening Balance"
    _order = "id desc"

    name = fields.Char(string="Reference", default="New", readonly=True, copy=False)
    date = fields.Date(
        string="Opening Date", default=fields.Date.context_today, required=True
    )
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("posted", "Posted"),
            ("cancel", "Cancelled"),
        ],
        string="Status",
        default="draft",
        readonly=True,
    )
    line_ids = fields.One2many(
        "idil.sales.opening.balance.line", "opening_balance_id", string="Lines"
    )
    internal_comment = fields.Text(string="Internal Comment")
    # Currency fields
    currency_id = fields.Many2one(
        "res.currency",
        string="Currency",
        required=True,
        default=lambda self: self.env["res.currency"].search(
            [("name", "=", "SL")], limit=1
        ),
        readonly=True,
    )

    rate = fields.Float(
        string="Exchange Rate",
        compute="_compute_exchange_rate",
        store=True,
        readonly=True,
    )

    total_due_balance = fields.Float(
        string="Total Due Balance",
        compute="_compute_total_due_balance",
        store=False,  # Optional: set to True if you want it stored in DB
    )

    @api.depends("line_ids")
    def _compute_total_due_balance(self):
        for record in self:
            receipts = self.env["idil.sales.receipt"].search(
                [("sales_opening_balance_id", "=", record.id)]
            )
            record.total_due_balance = sum(receipts.mapped("remaining_amount"))

    @api.depends("currency_id")
    def _compute_exchange_rate(self):
        for order in self:
            if order.currency_id:
                rate = self.env["res.currency.rate"].search(
                    [
                        ("currency_id", "=", order.currency_id.id),
                        ("name", "=", fields.Date.today()),
                        ("company_id", "=", self.env.company.id),
                    ],
                    limit=1,
                )
                order.rate = rate.rate if rate else 0.0
            else:
                order.rate = 0.0

    @api.constrains("currency_id")
    def _check_exchange_rate_exists(self):
        for order in self:
            if order.currency_id:
                rate = self.env["res.currency.rate"].search_count(
                    [
                        ("currency_id", "=", order.currency_id.id),
                        ("name", "=", fields.Date.today()),
                        ("company_id", "=", self.env.company.id),
                    ]
                )
                if rate == 0:
                    raise exceptions.ValidationError(
                        "No exchange rate found for today. Please insert today's rate before saving."
                    )

    @api.model
    def create(self, vals):
        # Generate sequence if needed

        # Check for duplicate opening balance lines for any salesperson in line_ids
        # vals['line_ids'] is a list of (0, 0, values) tuples for new records
        try:
            with self.env.cr.savepoint():
                line_vals_list = vals.get("line_ids", [])
                for command in line_vals_list:
                    if command[0] == 0:  # Only check for new lines
                        line_vals = command[2]
                        sales_person_id = line_vals.get("sales_person_id")
                        if sales_person_id:
                            # Check for existing opening balance line for this salesperson
                            existing_line = self.env[
                                "idil.sales.opening.balance.line"
                            ].search(
                                [
                                    ("sales_person_id", "=", sales_person_id),
                                    ("opening_balance_id.state", "!=", "cancel"),
                                ],
                                limit=1,
                            )
                            if existing_line:
                                raise ValidationError(
                                    f"Salesperson '{existing_line.sales_person_id.name}' already has an opening balance entry. "
                                    f"You cannot create another one for this salesperson."
                                )

                if vals.get("name", "New") == "New":
                    vals["name"] = (
                        self.env["ir.sequence"].next_by_code(
                            "idil.sales.opening.balance"
                        )
                        or "New"
                    )

                currency_id = (
                    vals.get("currency_id")
                    or self.env["res.currency"]
                    .search([("name", "=", "SL")], limit=1)
                    .id
                )
                rate = vals.get("rate", 0)
                date_val = vals.get("date", fields.Date.context_today(self))
                name = vals["name"]

                # --- 1. General validations ---
                EquityAccount = self.env["idil.chart.account"].search(
                    [("name", "=", "Opening Balance Account")], limit=1
                )
                if not EquityAccount:
                    raise ValidationError(
                        "Opening Balance Account not found. Please configure it."
                    )

                # If currency rate not provided, try to get it
                if not rate:
                    currency = self.env["res.currency"].browse(currency_id)
                    rate_obj = self.env["res.currency.rate"].search(
                        [
                            ("currency_id", "=", currency.id),
                            ("name", "=", fields.Date.today()),
                            ("company_id", "=", self.env.company.id),
                        ],
                        limit=1,
                    )
                    rate = rate_obj.rate if rate_obj else 0.0
                if rate <= 0:
                    raise ValidationError("Rate cannot be zero.")

                trx_source_id = self.env["idil.transaction.source"].search(
                    [("name", "=", "Sales Opening Balance")], limit=1
                )
                if not trx_source_id:
                    raise ValidationError(
                        'Transaction source "Sales Opening Balance" not found.'
                    )

                # --- 2. Create actual record first ---
                vals["state"] = "posted"
                record = super(SalesOpeningBalance, self).create(vals)

                # --- 3. For each line, create bookings, receipts, transactions ---
                for line in record.line_ids:
                    # --- Find clearing accounts ---
                    source_clearing_account = self.env["idil.chart.account"].search(
                        [
                            ("name", "=", "Exchange Clearing Account"),
                            (
                                "currency_id",
                                "=",
                                line.sales_person_id.account_receivable_id.currency_id.id,
                            ),
                        ],
                        limit=1,
                    )
                    target_clearing_account = self.env["idil.chart.account"].search(
                        [
                            ("name", "=", "Exchange Clearing Account"),
                            ("currency_id", "=", EquityAccount.currency_id.id),
                        ],
                        limit=1,
                    )
                    if not source_clearing_account or not target_clearing_account:
                        raise ValidationError(
                            "Exchange clearing accounts are required for currency conversion."
                        )

                    cost_amount_usd = line.amount / rate

                    # --- Booking (Header) ---
                    transaction_booking = self.env["idil.transaction_booking"].create(
                        {
                            "trx_date": date_val,
                            "reffno": name,
                            "payment_status": "pending",
                            "payment_method": "opening_balance",
                            "amount": line.amount,
                            "amount_paid": 0.0,
                            "remaining_amount": line.amount,
                            "trx_source_id": trx_source_id.id,
                            "sales_person_id": line.sales_person_id.id,
                            "sales_opening_balance_id": record.id,
                        }
                    )
                    # --- Booking lines ---
                    # 1. Debit salesperson receivable
                    self.env["idil.transaction_bookingline"].create(
                        {
                            "transaction_booking_id": transaction_booking.id,
                            "sales_opening_balance_id": record.id,
                            "account_number": line.sales_person_id.account_receivable_id.id,
                            "transaction_type": "dr",
                            "dr_amount": line.amount,
                            "cr_amount": 0,
                            "transaction_date": date_val,
                            "description": f"Opening Balance for {line.sales_person_id.name}",
                        }
                    )
                    # 2. Source clearing (local, credit)
                    self.env["idil.transaction_bookingline"].create(
                        {
                            "transaction_booking_id": transaction_booking.id,
                            "sales_opening_balance_id": record.id,
                            "account_number": source_clearing_account.id,
                            "transaction_type": "cr",
                            "dr_amount": 0.0,
                            "cr_amount": line.amount,
                            "transaction_date": date_val,
                            "description": f"Opening Balance for {line.sales_person_id.name}",
                        }
                    )
                    # 3. Owners Equity (credit, USD)
                    self.env["idil.transaction_bookingline"].create(
                        {
                            "transaction_booking_id": transaction_booking.id,
                            "sales_opening_balance_id": record.id,
                            "account_number": EquityAccount.id,
                            "transaction_type": "cr",
                            "dr_amount": 0.0,
                            "cr_amount": cost_amount_usd,
                            "transaction_date": date_val,
                            "description": f"Opening Balance for {line.sales_person_id.name}",
                        }
                    )
                    # 4. Target clearing (debit, USD)
                    self.env["idil.transaction_bookingline"].create(
                        {
                            "transaction_booking_id": transaction_booking.id,
                            "sales_opening_balance_id": record.id,
                            "account_number": target_clearing_account.id,
                            "transaction_type": "dr",
                            "dr_amount": cost_amount_usd,
                            "cr_amount": 0.0,
                            "transaction_date": date_val,
                            "description": f"Opening Balance for {line.sales_person_id.name}",
                        }
                    )
                    # --- Receipt ---
                    self.env["idil.sales.receipt"].create(
                        {
                            "salesperson_id": line.sales_person_id.id,
                            "due_amount": line.amount,
                            "paid_amount": 0.0,
                            "remaining_amount": line.amount,
                            "receipt_date": date_val,
                            "sales_opening_balance_id": record.id,
                        }
                    )
                    # --- Salesperson Transaction ---
                    self.env["idil.salesperson.transaction"].create(
                        {
                            "sales_person_id": line.sales_person_id.id,
                            "sales_opening_balance_id": record.id,
                            "date": fields.Date.today(),
                            "transaction_type": "out",  # Or use logic if you have other types
                            "amount": line.amount,
                            "description": f" Opening Balance for ({line.sales_person_id.name})",
                        }
                    )

                return record
        except Exception as e:
            _logger.error(f"transaction failed: {str(e)}")
            raise ValidationError(f"Transaction failed: {str(e)}")

    def write(self, vals):
        try:
            with self.env.cr.savepoint():
                res = super().write(vals)
                for opening_balance in self:
                    updated_date = vals.get("date", opening_balance.date)
                    updated_rate = vals.get("rate", opening_balance.rate)
                    equity_account = self.env["idil.chart.account"].search(
                        [("name", "=", "Opening Balance Account")], limit=1
                    )
                    trx_source_id = self.env["idil.transaction.source"].search(
                        [("name", "=", "Sales Opening Balance")], limit=1
                    )

                    for line in opening_balance.line_ids:
                        # Check if this line has any receipt paid
                        receipt = self.env["idil.sales.receipt"].search(
                            [
                                ("sales_opening_balance_id", "=", opening_balance.id),
                                ("salesperson_id", "=", line.sales_person_id.id),
                                ("paid_amount", ">", 0),
                            ],
                            limit=1,
                        )
                        if receipt:
                            raise ValidationError(
                                f"Cannot update opening balance for {line.sales_person_id.name}: payment already received."
                            )

                        # Check if external transactions exist for the salesperson
                        external_txn = self.env["idil.salesperson.transaction"].search(
                            [
                                ("sales_person_id", "=", line.sales_person_id.id),
                                ("sales_opening_balance_id", "!=", opening_balance.id),
                                ("amount", ">", 0),
                            ],
                            limit=1,
                        )
                        if external_txn:
                            raise ValidationError(
                                f"Cannot update opening balance for {line.sales_person_id.name}: another transaction already exists."
                            )

                        # Check for an existing booking
                        booking = self.env["idil.transaction_booking"].search(
                            [
                                ("sales_opening_balance_id", "=", opening_balance.id),
                                ("sales_person_id", "=", line.sales_person_id.id),
                            ],
                            limit=1,
                        )

                        if booking:
                            # --- Update existing booking and lines ---
                            cost_amount_usd = line.amount / (updated_rate or 1.0)
                            booking.write(
                                {
                                    "trx_date": updated_date,
                                    "amount": line.amount,
                                    "remaining_amount": line.amount
                                    - booking.amount_paid,
                                }
                            )

                            for bl in booking.booking_lines:
                                if (
                                    bl.transaction_type == "dr"
                                    and bl.account_number.id == line.account_id.id
                                ):
                                    bl.write(
                                        {
                                            "dr_amount": line.amount,
                                            "transaction_date": updated_date,
                                        }
                                    )
                                elif (
                                    bl.transaction_type == "cr"
                                    and bl.account_number.name
                                    == "Exchange Clearing Account"
                                    and bl.account_number.currency_id
                                    == line.account_id.currency_id
                                ):
                                    bl.write(
                                        {
                                            "cr_amount": line.amount,
                                            "transaction_date": updated_date,
                                        }
                                    )
                                elif (
                                    bl.transaction_type == "cr"
                                    and bl.account_number == equity_account
                                ):
                                    bl.write(
                                        {
                                            "cr_amount": cost_amount_usd,
                                            "transaction_date": updated_date,
                                        }
                                    )
                                elif (
                                    bl.transaction_type == "dr"
                                    and bl.account_number.name
                                    == "Exchange Clearing Account"
                                    and bl.account_number.currency_id
                                    == equity_account.currency_id
                                ):
                                    bl.write(
                                        {
                                            "dr_amount": cost_amount_usd,
                                            "transaction_date": updated_date,
                                        }
                                    )
                                else:
                                    bl.write({"transaction_date": updated_date})

                            # --- Update receipt ---
                            receipt = self.env["idil.sales.receipt"].search(
                                [
                                    (
                                        "sales_opening_balance_id",
                                        "=",
                                        opening_balance.id,
                                    ),
                                    ("salesperson_id", "=", line.sales_person_id.id),
                                ],
                                limit=1,
                            )
                            if receipt:
                                receipt.write(
                                    {
                                        "due_amount": line.amount,
                                        "remaining_amount": line.amount
                                        - receipt.paid_amount,
                                        "receipt_date": updated_date,
                                    }
                                )

                            # --- Update salesperson transaction ---
                            sp_txn = self.env["idil.salesperson.transaction"].search(
                                [
                                    (
                                        "sales_opening_balance_id",
                                        "=",
                                        opening_balance.id,
                                    ),
                                    ("sales_person_id", "=", line.sales_person_id.id),
                                ],
                                limit=1,
                            )
                            if sp_txn:
                                sp_txn.write(
                                    {
                                        "amount": line.amount,
                                        "date": updated_date,
                                        "description": f"Opening Balance for ({line.sales_person_id.name})",
                                    }
                                )

                        else:
                            # --- New line: create full set of records ---
                            source_clearing_account = self.env[
                                "idil.chart.account"
                            ].search(
                                [
                                    ("name", "=", "Exchange Clearing Account"),
                                    (
                                        "currency_id",
                                        "=",
                                        line.account_id.currency_id.id,
                                    ),
                                ],
                                limit=1,
                            )
                            target_clearing_account = self.env[
                                "idil.chart.account"
                            ].search(
                                [
                                    ("name", "=", "Exchange Clearing Account"),
                                    ("currency_id", "=", equity_account.currency_id.id),
                                ],
                                limit=1,
                            )

                            cost_amount_usd = line.amount / (updated_rate or 1.0)

                            booking = self.env["idil.transaction_booking"].create(
                                {
                                    "trx_date": updated_date,
                                    "reffno": opening_balance.name,
                                    "payment_status": "pending",
                                    "payment_method": "opening_balance",
                                    "amount": line.amount,
                                    "amount_paid": 0.0,
                                    "remaining_amount": line.amount,
                                    "trx_source_id": trx_source_id.id,
                                    "sales_person_id": line.sales_person_id.id,
                                    "sales_opening_balance_id": opening_balance.id,
                                }
                            )

                            self.env["idil.transaction_bookingline"].create(
                                [
                                    {
                                        "transaction_booking_id": booking.id,
                                        "sales_opening_balance_id": opening_balance.id,
                                        "account_number": line.account_id.id,
                                        "transaction_type": "dr",
                                        "dr_amount": line.amount,
                                        "transaction_date": updated_date,
                                        "description": f"Opening Balance for {line.sales_person_id.name}",
                                    },
                                    {
                                        "transaction_booking_id": booking.id,
                                        "sales_opening_balance_id": opening_balance.id,
                                        "account_number": source_clearing_account.id,
                                        "transaction_type": "cr",
                                        "cr_amount": line.amount,
                                        "transaction_date": updated_date,
                                        "description": f"Opening Balance for {line.sales_person_id.name}",
                                    },
                                    {
                                        "transaction_booking_id": booking.id,
                                        "sales_opening_balance_id": opening_balance.id,
                                        "account_number": equity_account.id,
                                        "transaction_type": "cr",
                                        "cr_amount": cost_amount_usd,
                                        "transaction_date": updated_date,
                                        "description": f"Opening Balance for {line.sales_person_id.name}",
                                    },
                                    {
                                        "transaction_booking_id": booking.id,
                                        "sales_opening_balance_id": opening_balance.id,
                                        "account_number": target_clearing_account.id,
                                        "transaction_type": "dr",
                                        "dr_amount": cost_amount_usd,
                                        "transaction_date": updated_date,
                                        "description": f"Opening Balance for {line.sales_person_id.name}",
                                    },
                                ]
                            )

                            self.env["idil.sales.receipt"].create(
                                {
                                    "salesperson_id": line.sales_person_id.id,
                                    "due_amount": line.amount,
                                    "paid_amount": 0.0,
                                    "remaining_amount": line.amount,
                                    "receipt_date": updated_date,
                                    "sales_opening_balance_id": opening_balance.id,
                                }
                            )

                            self.env["idil.salesperson.transaction"].create(
                                {
                                    "sales_person_id": line.sales_person_id.id,
                                    "sales_opening_balance_id": opening_balance.id,
                                    "date": updated_date,
                                    "transaction_type": "out",
                                    "amount": line.amount,
                                    "description": f"Opening Balance for ({line.sales_person_id.name})",
                                }
                            )

                return res
        except Exception as e:
            _logger.error(f"transaction failed: {str(e)}")
            raise ValidationError(f"Transaction failed: {str(e)}")

    def unlink(self):
        try:
            with self.env.cr.savepoint():
                for opening_balance in self:
                    for line in opening_balance.line_ids:
                        # 1. Prevent delete if payment already received on receipt
                        receipt = self.env["idil.sales.receipt"].search(
                            [
                                ("sales_opening_balance_id", "=", opening_balance.id),
                                ("salesperson_id", "=", line.sales_person_id.id),
                                ("paid_amount", ">", 0),
                            ],
                            limit=1,
                        )
                        if receipt:
                            raise ValidationError(
                                f"Cannot delete opening balance for {line.sales_person_id.name}: payment already received."
                            )
                        # 2. Prevent delete if other sales transaction exists for this salesperson
                        other_txn = self.env["idil.salesperson.transaction"].search(
                            [
                                ("sales_person_id", "=", line.sales_person_id.id),
                                ("sales_opening_balance_id", "!=", opening_balance.id),
                                ("amount", ">", 0),
                            ],
                            limit=1,
                        )
                        if other_txn:
                            raise ValidationError(
                                f"Cannot delete opening balance for {line.sales_person_id.name}: "
                                "another sales transaction already exists for this salesperson."
                            )

                    bookings = self.env["idil.transaction_booking"].search(
                        [("sales_opening_balance_id", "=", opening_balance.id)]
                    )
                    for booking in bookings:
                        booking.booking_lines.unlink()
                        booking.unlink()

                    sales_transactions = self.env[
                        "idil.salesperson.transaction"
                    ].search([("sales_opening_balance_id", "=", opening_balance.id)])
                    sales_transactions.unlink()

                return super().unlink()
        except Exception as e:
            _logger.error(f"transaction failed: {str(e)}")
            raise ValidationError(f"Transaction failed: {str(e)}")


class SalesOpeningBalanceLine(models.Model):
    _name = "idil.sales.opening.balance.line"
    _description = "Sales Opening Balance Line"
    _order = "id desc"

    opening_balance_id = fields.Many2one(
        "idil.sales.opening.balance", string="Opening Balance", ondelete="cascade"
    )

    sales_person_id = fields.Many2one(
        "idil.sales.sales_personnel",
        string="Salesperson",
        required=True,
        domain=[("account_receivable_id", "!=", False)],
    )

    account_id = fields.Many2one(
        "idil.chart.account", string="Account", readonly=True, store=True
    )
    account_currency_id = fields.Many2one(
        "res.currency",
        string="Account Currency",
        related="account_id.currency_id",
        readonly=True,
        store=True,
    )

    amount = fields.Float(string="Opening Amount", required=True)

    @api.onchange("sales_person_id")
    def _onchange_sales_person_id(self):
        for line in self:
            if line.sales_person_id:
                line.account_id = line.sales_person_id.account_receivable_id.id
            else:
                line.account_id = False

    @api.constrains("account_id")
    def _check_account_id(self):
        for rec in self:
            if not rec.account_id:
                raise ValidationError(
                    "Please select a salesperson with a valid Receivable Account."
                )

    @api.model
    def create(self, vals):
        # Always set account_id from salesperson if not provided
        if not vals.get("account_id") and vals.get("sales_person_id"):
            salesperson = self.env["idil.sales.sales_personnel"].browse(
                vals["sales_person_id"]
            )
            vals["account_id"] = salesperson.account_receivable_id.id
        return super().create(vals)

    # def unlink(self):
    #     Receipt = self.env["idil.sales.receipt"]
    #     Transaction = self.env["idil.salesperson.transaction"]
    #     Booking = self.env["idil.transaction_booking"]

    #     for line in self:
    #         opening_balance = line.opening_balance_id

    #         # 1. Prevent delete if receipt has payments
    #         receipt = Receipt.search(
    #             [
    #                 ("sales_opening_balance_id", "=", opening_balance.id),
    #                 ("salesperson_id", "=", line.sales_person_id.id),
    #                 ("paid_amount", ">", 0),
    #             ],
    #             limit=1,
    #         )
    #         if receipt:
    #             raise ValidationError(
    #                 f"Cannot delete opening balance line for {line.sales_person_id.name}: payment already received."
    #             )

    #         # 2. Prevent delete if external sales transaction exists
    #         external_txn = Transaction.search(
    #             [
    #                 ("sales_person_id", "=", line.sales_person_id.id),
    #                 ("sales_opening_balance_id", "!=", opening_balance.id),
    #                 ("amount", ">", 0),
    #             ],
    #             limit=1,
    #         )
    #         if external_txn:
    #             raise ValidationError(
    #                 f"Cannot delete opening balance line for {line.sales_person_id.name}: another transaction already exists."
    #             )

    #         # 3. Delete related transaction_booking + booking_lines
    #         booking = Booking.search(
    #             [
    #                 ("sales_opening_balance_id", "=", opening_balance.id),
    #                 ("sales_person_id", "=", line.sales_person_id.id),
    #             ],
    #             limit=1,
    #         )
    #         if booking:
    #             booking.booking_lines.unlink()
    #             booking.unlink()

    #         # 4. Delete related salesperson transaction
    #         txn = Transaction.search(
    #             [
    #                 ("sales_opening_balance_id", "=", opening_balance.id),
    #                 ("sales_person_id", "=", line.sales_person_id.id),
    #             ],
    #             limit=1,
    #         )
    #         if txn:
    #             txn.unlink()

    #         # 5. Delete related receipt (BEFORE line is deleted)
    #         receipt_to_delete = Receipt.search(
    #             [
    #                 ("sales_opening_balance_id", "=", opening_balance.id),
    #                 ("salesperson_id", "=", line.sales_person_id.id),
    #             ],
    #             limit=1,
    #         )
    #         if receipt_to_delete:
    #             receipt_to_delete.unlink()

    #     # 6. Delete the opening balance lines
    #     return super().unlink()
    def unlink(self):
        try:
            with self.env.cr.savepoint():
                Receipt = self.env["idil.sales.receipt"]
                Transaction = self.env["idil.salesperson.transaction"]
                Booking = self.env["idil.transaction_booking"]

                # Prepare a list of (salesperson_id, opening_balance_id) to delete receipts after unlink
                receipt_targets = []

                for line in self:
                    opening_balance = line.opening_balance_id

                    # 1. Prevent delete if receipt has payments
                    paid_receipt = Receipt.search(
                        [
                            ("sales_opening_balance_id", "=", opening_balance.id),
                            ("salesperson_id", "=", line.sales_person_id.id),
                            ("paid_amount", ">", 0),
                        ],
                        limit=1,
                    )
                    if paid_receipt:
                        raise ValidationError(
                            f"Cannot delete opening balance line for {line.sales_person_id.name}: payment already received."
                        )

                    # 2. Prevent delete if external sales transaction exists
                    external_txn = Transaction.search(
                        [
                            ("sales_person_id", "=", line.sales_person_id.id),
                            ("sales_opening_balance_id", "!=", opening_balance.id),
                            ("amount", ">", 0),
                        ],
                        limit=1,
                    )
                    if external_txn:
                        raise ValidationError(
                            f"Cannot delete opening balance line for {line.sales_person_id.name}: another transaction already exists."
                        )

                    # 3. Delete related transaction_booking + booking_lines
                    booking = Booking.search(
                        [
                            ("sales_opening_balance_id", "=", opening_balance.id),
                            ("sales_person_id", "=", line.sales_person_id.id),
                        ],
                        limit=1,
                    )
                    if booking:
                        booking.booking_lines.unlink()
                        booking.unlink()

                    # 4. Delete related salesperson transaction
                    txn = Transaction.search(
                        [
                            ("sales_opening_balance_id", "=", opening_balance.id),
                            ("sales_person_id", "=", line.sales_person_id.id),
                        ],
                        limit=1,
                    )
                    if txn:
                        txn.unlink()

                    # 5. Save values for post-deletion receipt cleanup
                    receipt_targets.append(
                        {
                            "salesperson_id": line.sales_person_id.id,
                            "opening_balance_id": opening_balance.id,
                        }
                    )

                # 6. Delete the opening balance lines
                res = super().unlink()

                # 7. Delete receipts after lines are deleted
                for target in receipt_targets:
                    receipt = Receipt.search(
                        [
                            (
                                "sales_opening_balance_id",
                                "=",
                                target["opening_balance_id"],
                            ),
                            ("salesperson_id", "=", target["salesperson_id"]),
                        ],
                        limit=1,
                    )
                    if receipt:
                        receipt.unlink()

                return res
        except Exception as e:
            _logger.error(f"transaction failed: {str(e)}")
            raise ValidationError(f"Transaction failed: {str(e)}")
