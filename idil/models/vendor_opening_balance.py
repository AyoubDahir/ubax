from odoo import models, fields, api
from odoo.exceptions import ValidationError
import logging

_logger = logging.getLogger(__name__)


class VendorOpeningBalance(models.Model):
    _name = "idil.vendor.opening.balance"
    _description = "Vendor Opening Balance"
    _order = "id desc"

    name = fields.Char(string="Reference", default="New", readonly=True, copy=False)
    date = fields.Date(
        string="Opening Date", default=fields.Date.context_today, required=True
    )
    state = fields.Selection(
        [("draft", "Draft"), ("posted", "Posted"), ("cancel", "Cancelled")],
        string="Status",
        default="draft",
        readonly=True,
    )
    line_ids = fields.One2many(
        "idil.vendor.opening.balance.line", "opening_balance_id", string="Lines"
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
    total_amount = fields.Float(
        string="Total Opening Amount",
        compute="_compute_total_amount",
        currency_field="currency_id",
        store=True,
        readonly=True,
    )

    @api.depends("line_ids.amount")
    def _compute_total_amount(self):
        for rec in self:
            rec.total_amount = sum(line.amount for line in rec.line_ids)

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
        try:
            with self.env.cr.savepoint():
                if vals.get("name", "New") == "New":
                    vals["name"] = (
                        self.env["ir.sequence"].next_by_code(
                            "idil.vendor.opening.balance"
                        )
                        or "New"
                    )
                record = super().create(vals)

                if record.state == "posted":
                    raise ValidationError(
                        "This opening balance has already been posted."
                    )
                if not record.line_ids:
                    raise ValidationError("Add at least one vendor line.")

                opening_balance_account = self.env["idil.chart.account"].search(
                    [("name", "=", "Opening Balance Account")], limit=1
                )
                if not opening_balance_account:
                    raise ValidationError(_("Opening Balance Account not found."))

                # Enforce USD currency for Opening Balance Account
                if opening_balance_account.currency_id.name != "USD":
                    raise ValidationError(
                        "The Opening Balance Account currency must always be USD!"
                    )

                trx_source_id = self.env["idil.transaction.source"].search(
                    [("name", "=", "Vendor Opening Balance")], limit=1
                )
                if not trx_source_id:
                    raise ValidationError(
                        'Transaction source "Vendor Opening Balance" not found.'
                    )

                for line in record.line_ids:
                    # Show blocking PO/vendor transactions with full info
                    purchase_orders = self.env["idil.purchase_order"].search(
                        [("vendor_id", "=", line.vendor_id.id)]
                    )
                    vendor_transactions = self.env["idil.vendor_transaction"].search(
                        [
                            ("vendor_id", "=", line.vendor_id.id),
                            ("reffno", "!=", "Opening Balance"),
                        ]
                    )
                    if purchase_orders or vendor_transactions:
                        message = f"You cannot create an opening balance for vendor '{line.vendor_id.name}' because there are already related records:\n"
                        if purchase_orders:
                            message += "\nPurchase Orders:\n"
                            for po in purchase_orders:
                                po_ref = po.name if hasattr(po, "name") else str(po.id)
                                po_date = (
                                    po.date_order if hasattr(po, "date_order") else ""
                                )
                                message += f"- PO: {po_ref}   Date: {po_date}\n"
                        if vendor_transactions:
                            message += "\nVendor Transactions:\n"
                            for vt in vendor_transactions:
                                vt_num = (
                                    vt.transaction_number
                                    if hasattr(vt, "transaction_number")
                                    else str(vt.id)
                                )
                                vt_ref = vt.reffno if hasattr(vt, "reffno") else ""
                                message += f"- Transaction: {vt_num}   Ref: {vt_ref}\n"
                        raise ValidationError(message)

                    vendor_account = line.vendor_id.account_payable_id
                    vendor_currency = vendor_account.currency_id

                    # Ensure vendor account is valid
                    if not vendor_account:
                        raise ValidationError(
                            f"Vendor '{line.vendor_id.name}' does not have a payable account."
                        )

                    # If vendor account is USD, no conversion
                    if vendor_currency.name == "USD":
                        cost_amount_usd = line.amount
                    else:
                        # Need conversion and a rate
                        if not record.rate:
                            raise ValidationError(
                                "Exchange rate is required for currency conversion."
                            )
                        cost_amount_usd = line.amount / record.rate

                    # If currencies don't match USD, require clearing accounts
                    if vendor_currency.name != "USD":
                        # Get clearing accounts
                        source_clearing_account = self.env["idil.chart.account"].search(
                            [
                                ("name", "=", "Exchange Clearing Account"),
                                ("currency_id", "=", vendor_currency.id),
                            ],
                            limit=1,
                        )
                        target_clearing_account = self.env["idil.chart.account"].search(
                            [
                                ("name", "=", "Exchange Clearing Account"),
                                (
                                    "currency_id",
                                    "=",
                                    opening_balance_account.currency_id.id,
                                ),
                            ],
                            limit=1,
                        )
                        if not source_clearing_account or not target_clearing_account:
                            raise ValidationError(
                                "Exchange clearing accounts are required for currency conversion."
                            )

                    # Final check: Opening Balance account must match with booking USD
                    if opening_balance_account.currency_id.name != "USD":
                        raise ValidationError(
                            f"The Opening Balance Account currency must be USD (found {opening_balance_account.currency_id.name})."
                        )

                    # Now create transaction booking
                    transaction_booking = self.env["idil.transaction_booking"].create(
                        {
                            "trx_date": record.date,
                            "reffno": record.name,
                            "payment_status": "pending",
                            "payment_method": "opening_balance",
                            "amount": line.amount,
                            "amount_paid": 0.0,
                            "remaining_amount": line.amount,
                            "trx_source_id": trx_source_id.id,
                            "vendor_id": line.vendor_id.id,
                            "vendor_opening_balance_id": line.id,
                        }
                    )

                    # Create clearing lines if needed (conversion)
                    if vendor_currency.name != "USD":
                        # Credit source clearing account (local)
                        self.env["idil.transaction_bookingline"].create(
                            {
                                "transaction_booking_id": transaction_booking.id,
                                "vendor_opening_balance_id": line.id,
                                "account_number": source_clearing_account.id,
                                "transaction_type": "cr",
                                "dr_amount": 0.0,
                                "cr_amount": line.amount,
                                "transaction_date": record.date,
                                "description": f"Opening Balance Clearing ({vendor_currency.name}) for {line.vendor_id.name}",
                            }
                        )
                        # Debit target clearing account (USD)
                        self.env["idil.transaction_bookingline"].create(
                            {
                                "transaction_booking_id": transaction_booking.id,
                                "vendor_opening_balance_id": line.id,
                                "account_number": target_clearing_account.id,
                                "transaction_type": "dr",
                                "dr_amount": cost_amount_usd,
                                "cr_amount": 0.0,
                                "transaction_date": record.date,
                                "description": f"Opening Balance Clearing (USD) for {line.vendor_id.name}",
                            }
                        )

                    # Owners Equity (Opening Balance Account) -- always USD
                    self.env["idil.transaction_bookingline"].create(
                        {
                            "transaction_booking_id": transaction_booking.id,
                            "vendor_opening_balance_id": line.id,
                            "account_number": opening_balance_account.id,
                            "transaction_type": "dr",
                            "cr_amount": 0.0,
                            "dr_amount": cost_amount_usd,
                            "transaction_date": record.date,
                            "description": f"Opening Balance for {line.vendor_id.name}",
                        }
                    )
                    # Vendor Payable (in vendor's currency)
                    self.env["idil.transaction_bookingline"].create(
                        {
                            "transaction_booking_id": transaction_booking.id,
                            "vendor_opening_balance_id": line.id,
                            "account_number": vendor_account.id,
                            "transaction_type": "cr",
                            "dr_amount": 0.0,
                            "cr_amount": line.amount,
                            "transaction_date": record.date,
                            "description": f"Opening Balance for {line.vendor_id.name}",
                        }
                    )

                    # Vendor transaction
                    self.env["idil.vendor_transaction"].create(
                        {
                            "transaction_number": transaction_booking.transaction_number,
                            "transaction_date": record.date,
                            "vendor_id": line.vendor_id.id,
                            "amount": line.amount,
                            "remaining_amount": line.amount,
                            "paid_amount": 0.0,
                            "payment_method": "other",
                            "reffno": record.name,
                            "transaction_booking_id": transaction_booking.id,
                            "payment_status": "pending",
                        }
                    )
                    line.vendor_id.opening_balance += line.amount

                record.state = "posted"
                return record
        except Exception as e:
            _logger.error(f"transaction failed: {str(e)}")
            raise ValidationError(f"Transaction failed: {str(e)}")

    def write(self, vals):
        try:
            with self.env.cr.savepoint():
                res = super().write(vals)

                for record in self:
                    opening_balance_account = self.env["idil.chart.account"].search(
                        [("name", "=", "Opening Balance Account")], limit=1
                    )
                    if (
                        not opening_balance_account
                        or opening_balance_account.currency_id.name != "USD"
                    ):
                        raise ValidationError(
                            "The Opening Balance Account currency must always be USD!"
                        )

                    if record.currency_id.name != "USD" and not record.rate:
                        raise ValidationError(
                            "Exchange rate is required for currency conversion."
                        )

                    trx_source_id = self.env["idil.transaction.source"].search(
                        [("name", "=", "Vendor Opening Balance")], limit=1
                    )
                    if not trx_source_id:
                        raise ValidationError(
                            "Transaction source 'Vendor Opening Balance' not found."
                        )

                    for line in record.line_ids:
                        vendor_account = line.vendor_id.account_payable_id
                        vendor_currency = vendor_account.currency_id

                        if not vendor_account:
                            raise ValidationError(
                                f"Vendor '{line.vendor_id.name}' does not have a payable account."
                            )

                        cost_amount_usd = (
                            line.amount
                            if vendor_currency.name == "USD"
                            else line.amount / record.rate
                        )

                        if vendor_currency.name != "USD":
                            source_clearing_account = self.env[
                                "idil.chart.account"
                            ].search(
                                [
                                    ("name", "=", "Exchange Clearing Account"),
                                    ("currency_id", "=", vendor_currency.id),
                                ],
                                limit=1,
                            )
                            target_clearing_account = self.env[
                                "idil.chart.account"
                            ].search(
                                [
                                    ("name", "=", "Exchange Clearing Account"),
                                    (
                                        "currency_id",
                                        "=",
                                        opening_balance_account.currency_id.id,
                                    ),
                                ],
                                limit=1,
                            )
                            if (
                                not source_clearing_account
                                or not target_clearing_account
                            ):
                                raise ValidationError(
                                    "Exchange clearing accounts are required for currency conversion."
                                )

                        booking = self.env["idil.transaction_booking"].search(
                            [("vendor_opening_balance_id", "=", line.id)], limit=1
                        )

                        # 🟩 NEW LINE (No booking exists yet)
                        if not booking:
                            booking = self.env["idil.transaction_booking"].create(
                                {
                                    "trx_date": record.date,
                                    "reffno": record.name,
                                    "payment_status": "pending",
                                    "payment_method": "opening_balance",
                                    "amount": line.amount,
                                    "amount_paid": 0.0,
                                    "remaining_amount": line.amount,
                                    "trx_source_id": trx_source_id.id,
                                    "vendor_id": line.vendor_id.id,
                                    "vendor_opening_balance_id": line.id,
                                }
                            )

                            # Create vendor transaction for new line
                            self.env["idil.vendor_transaction"].create(
                                {
                                    "transaction_number": booking.transaction_number,
                                    "transaction_date": record.date,
                                    "vendor_id": line.vendor_id.id,
                                    "amount": line.amount,
                                    "remaining_amount": line.amount,
                                    "paid_amount": 0.0,
                                    "payment_method": "other",
                                    "reffno": record.name,
                                    "transaction_booking_id": booking.id,
                                    "payment_status": "pending",
                                }
                            )
                        else:
                            # Existing booking, just update it
                            booking.write(
                                {
                                    "trx_date": record.date,
                                    "amount": line.amount,
                                    "remaining_amount": line.amount
                                    - booking.amount_paid,
                                }
                            )
                            booking.booking_lines.unlink()

                        # (Re)Create booking lines
                        if vendor_currency.name != "USD":
                            self.env["idil.transaction_bookingline"].create(
                                {
                                    "transaction_booking_id": booking.id,
                                    "vendor_opening_balance_id": line.id,
                                    "account_number": source_clearing_account.id,
                                    "transaction_type": "cr",
                                    "dr_amount": 0.0,
                                    "cr_amount": line.amount,
                                    "transaction_date": record.date,
                                    "description": f"Opening Balance Clearing ({vendor_currency.name}) for {line.vendor_id.name}",
                                }
                            )
                            self.env["idil.transaction_bookingline"].create(
                                {
                                    "transaction_booking_id": booking.id,
                                    "vendor_opening_balance_id": line.id,
                                    "account_number": target_clearing_account.id,
                                    "transaction_type": "dr",
                                    "dr_amount": cost_amount_usd,
                                    "cr_amount": 0.0,
                                    "transaction_date": record.date,
                                    "description": f"Opening Balance Clearing (USD) for {line.vendor_id.name}",
                                }
                            )

                        # Owner equity (USD)
                        self.env["idil.transaction_bookingline"].create(
                            {
                                "transaction_booking_id": booking.id,
                                "vendor_opening_balance_id": line.id,
                                "account_number": opening_balance_account.id,
                                "transaction_type": "dr",
                                "dr_amount": cost_amount_usd,
                                "cr_amount": 0.0,
                                "transaction_date": record.date,
                                "description": f"Opening Balance for {line.vendor_id.name}",
                            }
                        )

                        # Payable (local)
                        self.env["idil.transaction_bookingline"].create(
                            {
                                "transaction_booking_id": booking.id,
                                "vendor_opening_balance_id": line.id,
                                "account_number": vendor_account.id,
                                "transaction_type": "cr",
                                "dr_amount": 0.0,
                                "cr_amount": line.amount,
                                "transaction_date": record.date,
                                "description": f"Opening Balance for {line.vendor_id.name}",
                            }
                        )

                        # Update vendor transaction if exists
                        vendor_tx = self.env["idil.vendor_transaction"].search(
                            [("transaction_booking_id", "=", booking.id)], limit=1
                        )
                        if vendor_tx:
                            vendor_tx.write(
                                {
                                    "transaction_date": record.date,
                                    "amount": line.amount,
                                    "remaining_amount": line.amount
                                    - vendor_tx.paid_amount,
                                }
                            )

                        # Update vendor opening balance
                        line.vendor_id.opening_balance = line.amount

                return res
        except Exception as e:
            _logger.error(f"transaction failed: {str(e)}")
            raise ValidationError(f"Transaction failed: {str(e)}")

    def unlink(self):
        try:
            with self.env.cr.savepoint():
                for record in self:
                    if record.state == "posted":
                        # If you want to block unlink after posting, uncomment:
                        # raise ValidationError("You cannot delete a posted opening balance. Cancel it first.")

                        # Or, if you want to allow, check all lines
                        for line in record.line_ids:
                            # Check for payments in vendor_transaction
                            vendor_tx = self.env["idil.vendor_transaction"].search(
                                [
                                    ("vendor_id", "=", line.vendor_id.id),
                                    (
                                        "transaction_booking_id",
                                        "=",
                                        line.opening_balance_id
                                        and line.opening_balance_id.id,
                                    ),
                                    ("paid_amount", ">", 0),
                                ],
                                limit=1,
                            )
                            if vendor_tx:
                                raise ValidationError(
                                    f"Cannot delete opening balance for vendor '{line.vendor_id.name}': payment already received on transaction {vendor_tx.transaction_number}."
                                )
                    # Remove all related transactions and booking lines for all lines
                    for line in record.line_ids:
                        # Delete vendor_transaction
                        vendor_transactions = self.env[
                            "idil.vendor_transaction"
                        ].search(
                            [
                                ("vendor_id", "=", line.vendor_id.id),
                                (
                                    "transaction_booking_id",
                                    "=",
                                    line.opening_balance_id
                                    and line.opening_balance_id.id,
                                ),
                            ]
                        )
                        vendor_transactions.unlink()

                        # Delete booking lines and booking
                        bookings = self.env["idil.transaction_booking"].search(
                            [
                                ("vendor_opening_balance_id", "=", line.id),
                            ]
                        )
                        for booking in bookings:
                            booking.booking_lines.unlink()
                            booking.unlink()

                return super().unlink()
        except Exception as e:
            _logger.error(f"transaction failed: {str(e)}")
            raise ValidationError(f"Transaction failed: {str(e)}")


class VendorOpeningBalanceLine(models.Model):
    _name = "idil.vendor.opening.balance.line"
    _description = "Vendor Opening Balance Line"
    _order = "id desc"

    opening_balance_id = fields.Many2one(
        "idil.vendor.opening.balance", string="Opening Balance", ondelete="cascade"
    )
    vendor_id = fields.Many2one(
        "idil.vendor.registration",
        string="Vendor",
        required=True,
        domain=[("account_payable_id", "!=", False)],
    )
    account_id = fields.Many2one(
        "idil.chart.account", string="Account", readonly=True, store=True
    )
    amount = fields.Float(string="Opening Amount", required=True)

    account_currency_id = fields.Many2one(
        "res.currency",
        string="Account Currency",
        related="account_id.currency_id",
        store=True,
        readonly=True,
    )

    @api.onchange("vendor_id")
    def _onchange_vendor_id(self):
        for line in self:
            if line.vendor_id:
                line.account_id = line.vendor_id.account_payable_id.id
            else:
                line.account_id = False

    @api.constrains("account_id")
    def _check_account_id(self):
        for rec in self:
            if not rec.account_id:
                raise ValidationError(
                    "Please select a vendor with a valid Payable Account."
                )

    @api.model
    def create(self, vals):
        vendor_id = vals.get("vendor_id")
        if vendor_id:
            existing_line = self.env["idil.vendor.opening.balance.line"].search(
                [
                    ("vendor_id", "=", vendor_id),
                    ("opening_balance_id.state", "!=", "cancel"),
                ],
                limit=1,
            )
            if existing_line:
                raise ValidationError(
                    f"Vendor '{existing_line.vendor_id.name}' already has an opening balance "
                    f"of amount {existing_line.amount:.2f} in record '{existing_line.opening_balance_id.name}'. "
                    f"You cannot create another one."
                )

        # Auto-fill account_id if missing
        if not vals.get("account_id") and vendor_id:
            vendor = self.env["idil.vendor.registration"].browse(vendor_id)
            vals["account_id"] = vendor.account_payable_id.id
        return super().create(vals)

    def unlink(self):
        for line in self:
            # Check for payment in vendor_transaction
            vendor_tx = self.env["idil.vendor_transaction"].search(
                [
                    ("vendor_id", "=", line.vendor_id.id),
                    (
                        "transaction_booking_id",
                        "=",
                        line.opening_balance_id and line.opening_balance_id.id,
                    ),
                    ("paid_amount", ">", 0),
                ],
                limit=1,
            )
            if vendor_tx:
                raise ValidationError(
                    f"Cannot delete opening balance line for vendor '{line.vendor_id.name}': payment already received on transaction {vendor_tx.transaction_number}."
                )
            # Remove related vendor_transaction
            vendor_transactions = self.env["idil.vendor_transaction"].search(
                [
                    ("vendor_id", "=", line.vendor_id.id),
                    (
                        "transaction_booking_id",
                        "=",
                        line.opening_balance_id and line.opening_balance_id.id,
                    ),
                ]
            )
            line.vendor_id.opening_balance -= line.amount

            vendor_transactions.unlink()
            # Remove related booking and booking lines
            bookings = self.env["idil.transaction_booking"].search(
                [
                    ("vendor_opening_balance_id", "=", line.id),
                ]
            )
            for booking in bookings:
                booking.booking_lines.unlink()
                booking.unlink()
        return super().unlink()
