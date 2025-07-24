from odoo import models, fields, api, exceptions
from datetime import datetime
from datetime import date
import re
from odoo.exceptions import ValidationError, UserError
import logging

_logger = logging.getLogger(__name__)


class CustomerOpeningBalance(models.Model):
    _name = "idil.customer.opening.balance"
    _description = "Customer Opening Balance"
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
        "idil.customer.opening.balance.line", "opening_balance_id", string="Lines"
    )
    internal_comment = fields.Text(string="Internal Comment")
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

    customer_sale_order_id = fields.Many2one(
        "idil.customer.sale.order",
        string="First Customer Sale Order",
        readonly=True,
        help="First sale order created from this opening balance.",
    )
    total_amount = fields.Monetary(
        string="Total Amount",
        compute="_compute_total_amount",
        store=True,
        currency_field="currency_id",
    )

    @api.depends("line_ids.amount")
    def _compute_total_amount(self):
        for record in self:
            record.total_amount = sum(record.line_ids.mapped("amount"))

    @api.depends("currency_id")
    def _compute_exchange_rate(self):
        for record in self:
            if record.currency_id:
                rate = self.env["res.currency.rate"].search(
                    [
                        ("currency_id", "=", record.currency_id.id),
                        ("name", "=", fields.Date.today()),
                        ("company_id", "=", self.env.company.id),
                    ],
                    limit=1,
                )
                record.rate = rate.rate if rate else 0.0
            else:
                record.rate = 0.0

    @api.constrains("currency_id")
    def _check_exchange_rate_exists(self):
        for record in self:
            if record.currency_id:
                rate = self.env["res.currency.rate"].search_count(
                    [
                        ("currency_id", "=", record.currency_id.id),
                        ("name", "=", fields.Date.today()),
                        ("company_id", "=", self.env.company.id),
                    ]
                )
                if rate == 0:
                    raise ValidationError(
                        "No exchange rate found for today. Please insert today's rate before saving."
                    )

    @api.model
    def create(self, vals):
        # Set the name from sequence if needed
        try:
            with self.env.cr.savepoint():
                if vals.get("name", "New") == "New":
                    vals["name"] = (
                        self.env["ir.sequence"].next_by_code(
                            "idil.customer.opening.balance"
                        )
                        or "New"
                    )
                # Create the record in memory, but not yet committed to DB
                record = super(CustomerOpeningBalance, self).create(vals)

                # === All your action_post logic starts here ===
                if record.state == "posted":
                    raise ValidationError(
                        "This opening balance has already been posted and cannot be processed again."
                    )
                if not record.line_ids:
                    raise ValidationError(
                        "You must add at least one customer to set an opening balance."
                    )

                EquityAccount = self.env["idil.chart.account"].search(
                    [("name", "=", "Opening Balance Account")], limit=1
                )
                if not EquityAccount:
                    raise ValidationError(
                        "Opening Balance Account not found. Please configure it."
                    )

                if record.rate <= 0:
                    raise ValidationError("Rate cannot be zero.")

                trx_source_id = record.env["idil.transaction.source"].search(
                    [("name", "=", "Customer Opening Balance")], limit=1
                )
                if not trx_source_id:
                    raise ValidationError(
                        'Transaction source "Customer Opening Balance" not found.'
                    )

                for line in record.line_ids:
                    # Get clearing accounts
                    source_clearing_account = record.env["idil.chart.account"].search(
                        [
                            ("name", "=", "Exchange Clearing Account"),
                            (
                                "currency_id",
                                "=",
                                line.customer_id.account_receivable_id.currency_id.id,
                            ),
                        ],
                        limit=1,
                    )
                    target_clearing_account = record.env["idil.chart.account"].search(
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

                    cost_amount_usd = line.amount / record.rate if record.rate else 0.0

                    # Create booking entry
                    transaction_booking = record.env["idil.transaction_booking"].create(
                        {
                            "trx_date": record.date,
                            "reffno": record.name,
                            "payment_status": "pending",
                            "payment_method": "opening_balance",
                            "amount": line.amount,
                            "amount_paid": 0.0,
                            "remaining_amount": line.amount,
                            "trx_source_id": trx_source_id.id,
                            "customer_id": line.customer_id.id,
                            "customer_opening_balance_id": line.id,
                        }
                    )
                    # Debit the customer receivable account
                    record.env["idil.transaction_bookingline"].create(
                        {
                            "transaction_booking_id": transaction_booking.id,
                            "customer_opening_balance_id": line.id,
                            "account_number": line.account_id.id,
                            "transaction_type": "dr",
                            "dr_amount": line.amount,
                            "cr_amount": 0,
                            "transaction_date": record.date,
                            "description": f"Opening Balance for {line.customer_id.name}",
                        }
                    )
                    # Credit source clearing account (local currency)
                    record.env["idil.transaction_bookingline"].create(
                        {
                            "transaction_booking_id": transaction_booking.id,
                            "customer_opening_balance_id": line.id,
                            "account_number": source_clearing_account.id,
                            "transaction_type": "cr",
                            "dr_amount": 0.0,
                            "cr_amount": line.amount,
                            "transaction_date": record.date,
                            "description": f"Opening Balance for {line.customer_id.name}",
                        }
                    )
                    # Credit target clearing account (USD)
                    record.env["idil.transaction_bookingline"].create(
                        {
                            "transaction_booking_id": transaction_booking.id,
                            "customer_opening_balance_id": line.id,
                            "account_number": EquityAccount.id,
                            "transaction_type": "cr",
                            "dr_amount": 0.0,
                            "cr_amount": cost_amount_usd,
                            "transaction_date": record.date,
                            "description": f"Opening Balance for {line.customer_id.name}",
                        }
                    )
                    # Debit target clearing account (USD)
                    record.env["idil.transaction_bookingline"].create(
                        {
                            "transaction_booking_id": transaction_booking.id,
                            "customer_opening_balance_id": line.id,
                            "account_number": target_clearing_account.id,
                            "transaction_type": "dr",
                            "dr_amount": cost_amount_usd,
                            "cr_amount": 0.0,
                            "transaction_date": record.date,
                            "description": f"Opening Balance for {line.customer_id.name}",
                        }
                    )
                    # Create customer receipt
                    record.env["idil.sales.receipt"].create(
                        {
                            "customer_id": line.customer_id.id,
                            "due_amount": line.amount,
                            "paid_amount": 0.0,
                            "remaining_amount": line.amount,
                            "receipt_date": record.date,
                            "customer_opening_balance_id": line.id,
                        }
                    )

                    sale_order = record.env["idil.customer.sale.order"].create(
                        {
                            "name": f"OB-{record.name}-{line.customer_id.name}",
                            "customer_id": line.customer_id.id,
                            "order_date": record.date,
                            "payment_method": "receivable",
                            "account_number": line.account_id.id,
                            "state": "confirmed",
                            "currency_id": record.currency_id.id,
                            "rate": record.rate,
                            "customer_opening_balance_id": line.id,
                            "order_total": line.amount,
                            "total_paid": 0.0,
                            "balance_due": line.amount,
                        }
                    )
                    record.customer_sale_order_id = sale_order.id
                    # Create a line without a product
                    record.env["idil.customer.sale.order.line"].create(
                        {
                            "order_id": sale_order.id,
                            "product_id": False,  # No product
                            "quantity": 1,
                            "cost_price": line.amount,
                            "price_unit": line.amount,
                            "customer_opening_balance_line_id": line.id,
                        }
                    )

                record.state = "posted"
                return record
        except Exception as e:
            _logger.error(f"transaction failed: {str(e)}")
            raise ValidationError(f"Transaction failed: {str(e)}")

    def unlink(self):
        try:
            with self.env.cr.savepoint():
                for opening_balance in self:
                    for line in opening_balance.line_ids:
                        # 1. Prevent delete if payment already received
                        receipt = self.env["idil.sales.receipt"].search(
                            [
                                ("customer_opening_balance_id", "=", line.id),
                                ("paid_amount", ">", 0),
                            ],
                            limit=1,
                        )
                        if receipt:
                            raise ValidationError(
                                f"Cannot delete opening balance for {line.customer_id.name}: payment already received."
                            )
                        # 2. Prevent delete if any OTHER sale order exists for this customer
                        other_sale_order = self.env["idil.customer.sale.order"].search(
                            [
                                ("customer_id", "=", line.customer_id.id),
                                ("state", "=", "confirmed"),
                                ("customer_opening_balance_id", "!=", line.id),
                            ],
                            limit=1,
                        )
                        if other_sale_order:
                            raise ValidationError(
                                f"Cannot delete opening balance for {line.customer_id.name}: "
                                "another sale order already exists for this customer."
                            )
                return super().unlink()
        except Exception as e:
            _logger.error(f"transaction failed: {str(e)}")
            raise ValidationError(f"Transaction failed: {str(e)}")

    def write(self, vals):
        try:
            with self.env.cr.savepoint():
                for opening_balance in self:
                    # Save old line IDs and amounts before write
                    old_line_ids = set(opening_balance.line_ids.ids)
                    old_amounts = {
                        line.id: line.amount for line in opening_balance.line_ids
                    }

                res = super().write(vals)

                for opening_balance in self:
                    trx_source = self.env["idil.transaction.source"].search(
                        [("name", "=", "Customer Opening Balance")], limit=1
                    )
                    equity_account = self.env["idil.chart.account"].search(
                        [("name", "=", "Opening Balance Account")], limit=1
                    )

                    if not trx_source or not equity_account:
                        raise ValidationError(
                            "Missing transaction source or opening balance account."
                        )

                    for line in opening_balance.line_ids:
                        is_new = line.id not in old_line_ids
                        old_amount = old_amounts.get(line.id)
                        amount_changed = (
                            old_amount is not None and old_amount != line.amount
                        )

                        # === Prevent update if payment already received ===
                        receipt_check = self.env["idil.sales.receipt"].search(
                            [
                                ("customer_opening_balance_id", "=", line.id),
                                ("paid_amount", ">", 0),
                            ],
                            limit=1,
                        )
                        if not is_new and receipt_check:
                            raise ValidationError(
                                f"Cannot update opening balance for {line.customer_id.name}: payment already received."
                            )

                        # === Prevent update if other sale order exists ===
                        # other_so_check = self.env["idil.customer.sale.order"].search(
                        #     [
                        #         ("customer_id", "=", line.customer_id.id),
                        #         ("state", "=", "confirmed"),
                        #         ("customer_opening_balance_id", "!=", line.id),
                        #     ],
                        #     limit=1,
                        # )
                        # if not is_new and other_so_check:
                        #     raise ValidationError(
                        #         f"Cannot update opening balance for {line.customer_id.name}: another sale order already exists."
                        #     )

                        # === NEW LINE PROCESSING ===
                        if is_new:
                            source_clearing = self.env["idil.chart.account"].search(
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
                            target_clearing = self.env["idil.chart.account"].search(
                                [
                                    ("name", "=", "Exchange Clearing Account"),
                                    ("currency_id", "=", equity_account.currency_id.id),
                                ],
                                limit=1,
                            )
                            if not source_clearing or not target_clearing:
                                raise ValidationError(
                                    "Clearing accounts not configured properly."
                                )
                            cost_usd = line.amount / (opening_balance.rate or 1.0)
                            booking = self.env["idil.transaction_booking"].create(
                                {
                                    "trx_date": opening_balance.date,
                                    "reffno": opening_balance.name,
                                    "payment_status": "pending",
                                    "payment_method": "opening_balance",
                                    "amount": line.amount,
                                    "amount_paid": 0.0,
                                    "remaining_amount": line.amount,
                                    "trx_source_id": trx_source.id,
                                    "customer_id": line.customer_id.id,
                                    "customer_opening_balance_id": line.id,
                                }
                            )
                            self.env["idil.transaction_bookingline"].create(
                                [
                                    {
                                        "transaction_booking_id": booking.id,
                                        "customer_opening_balance_id": line.id,
                                        "account_number": line.account_id.id,
                                        "transaction_type": "dr",
                                        "dr_amount": line.amount,
                                        "cr_amount": 0,
                                        "transaction_date": opening_balance.date,
                                        "description": f"Opening Balance for {line.customer_id.name}",
                                    },
                                    {
                                        "transaction_booking_id": booking.id,
                                        "customer_opening_balance_id": line.id,
                                        "account_number": source_clearing.id,
                                        "transaction_type": "cr",
                                        "dr_amount": 0,
                                        "cr_amount": line.amount,
                                        "transaction_date": opening_balance.date,
                                        "description": f"Opening Balance for {line.customer_id.name}",
                                    },
                                    {
                                        "transaction_booking_id": booking.id,
                                        "customer_opening_balance_id": line.id,
                                        "account_number": equity_account.id,
                                        "transaction_type": "cr",
                                        "dr_amount": 0,
                                        "cr_amount": cost_usd,
                                        "transaction_date": opening_balance.date,
                                        "description": f"Opening Balance for {line.customer_id.name}",
                                    },
                                    {
                                        "transaction_booking_id": booking.id,
                                        "customer_opening_balance_id": line.id,
                                        "account_number": target_clearing.id,
                                        "transaction_type": "dr",
                                        "dr_amount": cost_usd,
                                        "cr_amount": 0,
                                        "transaction_date": opening_balance.date,
                                        "description": f"Opening Balance for {line.customer_id.name}",
                                    },
                                ]
                            )
                            self.env["idil.sales.receipt"].create(
                                {
                                    "customer_id": line.customer_id.id,
                                    "due_amount": line.amount,
                                    "paid_amount": 0.0,
                                    "remaining_amount": line.amount,
                                    "receipt_date": opening_balance.date,
                                    "customer_opening_balance_id": line.id,
                                }
                            )
                            sale_order = self.env["idil.customer.sale.order"].create(
                                {
                                    "name": f"OB-{opening_balance.name}-{line.customer_id.name}",
                                    "customer_id": line.customer_id.id,
                                    "order_date": opening_balance.date,
                                    "account_number": line.account_id.id,
                                    "state": "confirmed",
                                    "currency_id": opening_balance.currency_id.id,
                                    "rate": opening_balance.rate,
                                    "customer_opening_balance_id": line.id,
                                    "order_total": line.amount,
                                    "total_paid": 0.0,
                                    "balance_due": line.amount,
                                }
                            )
                            opening_balance.customer_sale_order_id = sale_order.id
                            self.env["idil.customer.sale.order.line"].create(
                                {
                                    "order_id": sale_order.id,
                                    "product_id": False,
                                    "quantity": 1,
                                    "cost_price": line.amount,
                                    "price_unit": line.amount,
                                    "customer_opening_balance_line_id": line.id,
                                }
                            )
                        # === EXISTING LINE UPDATE ===
                        elif amount_changed:
                            cost_usd = line.amount / (opening_balance.rate or 1.0)

                            sale_order = self.env["idil.customer.sale.order"].search(
                                [("customer_opening_balance_id", "=", line.id)], limit=1
                            )
                            if sale_order:
                                sale_order.write(
                                    {
                                        "order_total": line.amount,
                                        "order_date": opening_balance.date,
                                        "balance_due": line.amount
                                        - sale_order.total_paid,
                                        "currency_id": opening_balance.currency_id.id,
                                        "rate": opening_balance.rate,
                                    }
                                )
                                order_line = self.env[
                                    "idil.customer.sale.order.line"
                                ].search(
                                    [
                                        ("order_id", "=", sale_order.id),
                                        (
                                            "customer_opening_balance_line_id",
                                            "=",
                                            line.id,
                                        ),
                                    ],
                                    limit=1,
                                )
                                if order_line:
                                    order_line.write(
                                        {
                                            "cost_price": line.amount,
                                            "price_unit": line.amount,
                                            "quantity": 1,
                                        }
                                    )
                            receipt = self.env["idil.sales.receipt"].search(
                                [("customer_opening_balance_id", "=", line.id)], limit=1
                            )
                            if receipt:
                                receipt.write(
                                    {
                                        "due_amount": line.amount,
                                        "remaining_amount": line.amount
                                        - receipt.paid_amount,
                                        "receipt_date": opening_balance.date,
                                    }
                                )
                            booking = self.env["idil.transaction_booking"].search(
                                [("customer_opening_balance_id", "=", line.id)], limit=1
                            )
                            if booking:
                                booking.write(
                                    {
                                        "trx_date": opening_balance.date,
                                        "amount": line.amount,
                                        "remaining_amount": line.amount
                                        - booking.amount_paid,
                                    }
                                )
                                source_clearing = self.env["idil.chart.account"].search(
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
                                target_clearing = self.env["idil.chart.account"].search(
                                    [
                                        ("name", "=", "Exchange Clearing Account"),
                                        (
                                            "currency_id",
                                            "=",
                                            equity_account.currency_id.id,
                                        ),
                                    ],
                                    limit=1,
                                )
                                for booking_line in booking.booking_lines:
                                    if (
                                        booking_line.transaction_type == "dr"
                                        and booking_line.account_number.id
                                        == line.account_id.id
                                    ):
                                        booking_line.write(
                                            {
                                                "dr_amount": line.amount,
                                                "cr_amount": 0,
                                                "transaction_date": opening_balance.date,
                                            }
                                        )
                                    elif (
                                        booking_line.transaction_type == "cr"
                                        and booking_line.account_number.name
                                        == "Exchange Clearing Account"
                                        and booking_line.account_number.currency_id.id
                                        == line.account_id.currency_id.id
                                    ):
                                        booking_line.write(
                                            {
                                                "cr_amount": line.amount,
                                                "dr_amount": 0,
                                                "transaction_date": opening_balance.date,
                                                "account_number": source_clearing.id,
                                            }
                                        )
                                    elif (
                                        booking_line.transaction_type == "cr"
                                        and booking_line.account_number.name
                                        == "Opening Balance Account"
                                    ):
                                        booking_line.write(
                                            {
                                                "cr_amount": cost_usd,
                                                "dr_amount": 0,
                                                "transaction_date": opening_balance.date,
                                                "account_number": equity_account.id,
                                            }
                                        )
                                    elif (
                                        booking_line.transaction_type == "dr"
                                        and booking_line.account_number.name
                                        == "Exchange Clearing Account"
                                        and booking_line.account_number.currency_id.id
                                        == equity_account.currency_id.id
                                    ):
                                        booking_line.write(
                                            {
                                                "dr_amount": cost_usd,
                                                "cr_amount": 0,
                                                "transaction_date": opening_balance.date,
                                                "account_number": target_clearing.id,
                                            }
                                        )
                return res

        except Exception as e:
            _logger.error(f"transaction failed: {str(e)}")
            raise ValidationError(f"Transaction failed: {str(e)}")


class CustomerOpeningBalanceLine(models.Model):
    _name = "idil.customer.opening.balance.line"
    _description = "Customer Opening Balance Line"
    _order = "id desc"

    opening_balance_id = fields.Many2one(
        "idil.customer.opening.balance", string="Opening Balance", ondelete="cascade"
    )

    customer_id = fields.Many2one(
        "idil.customer.registration",
        string="Customer",
        required=True,
        domain=[("account_receivable_id", "!=", False)],
    )

    account_id = fields.Many2one(
        "idil.chart.account", string="Account", readonly=True, store=True
    )
    currency_id = fields.Many2one(
        "res.currency",
        string="Account Currency",
        related="account_id.currency_id",
        readonly=True,
        store=True,
    )

    amount = fields.Float(string="Opening Amount", required=True)

    @api.onchange("customer_id")
    def _onchange_customer_id(self):
        for line in self:
            if line.customer_id:
                line.account_id = line.customer_id.account_receivable_id.id
            else:
                line.account_id = False

    @api.constrains("account_id")
    def _check_account_id(self):
        for rec in self:
            if not rec.account_id:
                raise ValidationError(
                    "Please select a customer with a valid Receivable Account."
                )

    @api.model
    def create(self, vals):
        customer_id = vals.get("customer_id")
        if customer_id:
            existing_line = self.env["idil.customer.opening.balance.line"].search(
                [
                    ("customer_id", "=", customer_id),
                    ("opening_balance_id.state", "!=", "cancel"),
                ],
                limit=1,
            )
            if existing_line:
                raise ValidationError(
                    "This customer already has an opening balance entry. You cannot create another one."
                )
        # Auto-fill account_id if missing
        if not vals.get("account_id") and customer_id:
            customer = self.env["idil.customer.registration"].browse(customer_id)
            vals["account_id"] = customer.account_receivable_id.id
        return super().create(vals)
