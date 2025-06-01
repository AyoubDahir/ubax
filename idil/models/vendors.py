from odoo.exceptions import ValidationError
from odoo import models, fields, api, _

import logging

_logger = logging.getLogger(__name__)


class Vendor(models.Model):
    _name = "idil.vendor.registration"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _description = "Vendor Registration"
    _sql_constraints = [
        ("unique_email", "UNIQUE(email)", "The email must be unique."),
        ("unique_phone", "UNIQUE(phone)", "The phone number must be unique."),
    ]

    # Basic Details
    name = fields.Char(string="Name", required=True, tracking=True)
    phone = fields.Char(string="Phone", required=True, tracking=True)
    email = fields.Char(string="Email", tracking=True)
    type = fields.Selection(
        [("company", "Company"), ("individual", "Individual")],
        string="Type",
        required=True,
        tracking=True,
    )
    status = fields.Boolean(string="Status", tracking=True)
    active = fields.Boolean(string="Active", default=True, tracking=True)
    image = fields.Binary(string="Image")
    currency_id = fields.Many2one(
        "res.currency",
        string="Currency",
        required=True,
        default=lambda self: self.env.company.currency_id,
    )

    # Accounting Section
    account_payable_id = fields.Many2one(
        "idil.chart.account",
        string="Account Payable",
        domain="[('account_type', '=', 'payable'), ('currency_id', '=', currency_id)]",
        help="This account will be used instead of the default one as the payable account for the current vendor",
        required=True,
    )

    account_receivable_id = fields.Many2one(
        "idil.chart.account",
        string="Account Receivable",
        domain=[("account_type", "=", "receivable"), ("currency_id", "=", currency_id)],
        help="This account will be used instead of the default one as the receivable account for the current vendor",
    )
    financial_transactions = fields.One2many(
        "idil.transaction_booking",
        "vendor_id",
        string="Financial Transactions",
        help="Displays financial transactions related to this vendor.",
    )

    # Opening Balance
    opening_balance = fields.Float(
        string="Opening Balance",
        default=0.0,
        help="The initial balance for the vendor when they are registered.",
    )
    vendor_transaction_ids = fields.One2many(
        "idil.vendor_transaction", "vendor_id", string="Vendor Transactions"
    )

    total_due_amount = fields.Float(
        string="Total Due Amount",
        compute="_compute_total_due_amount",
        store=False,  # Change to True if you want it stored
    )

    @api.depends("vendor_transaction_ids.remaining_amount")
    def _compute_total_due_amount(self):
        for vendor in self:
            vendor.total_due_amount = sum(
                vendor.vendor_transaction_ids.mapped("remaining_amount")
            )

    @api.model
    def create(self, vals):
        vendor = super(Vendor, self).create(vals)
        if vals.get("opening_balance", 0.0) > 0.0:
            _logger.info(
                "Invoking create_opening_balance_transaction during vendor creation for Vendor ID: %s",
                vendor.id,
            )
            vendor.create_opening_balance_transaction()
        return vendor

    def create_opening_balance_transaction(self):
        for vendor in self:
            if vendor.opening_balance > 0:
                try:
                    _logger.info(
                        "Attempting to create or update an opening balance transaction for vendor ID %s",
                        vendor.id,
                    )

                    # Find the "Vendor Balance" transaction source
                    trx_source = self.env["idil.transaction.source"].search(
                        [("name", "=", "Vendor Balance")], limit=1
                    )
                    if not trx_source:
                        raise ValidationError(
                            _("Transaction source 'Vendor Balance' not found.")
                        )

                    # Check if an "Opening Balance" transaction already exists
                    transaction = self.env["idil.transaction_booking"].search(
                        [
                            ("vendor_id", "=", vendor.id),
                            ("reffno", "=", "Opening Balance"),
                        ],
                        limit=1,
                    )

                    transaction_data = {
                        "reffno": "Opening Balance",
                        "vendor_id": vendor.id,
                        "trx_date": fields.Date.today(),
                        "amount": vendor.opening_balance,
                        "amount_paid": 0,
                        "remaining_amount": vendor.opening_balance,
                        "payment_status": "pending",
                        "trx_source_id": trx_source.id,
                    }

                    if transaction:
                        transaction.write(transaction_data)
                        _logger.info(
                            "Updating existing opening balance transaction ID: %s",
                            transaction.id,
                        )
                    else:
                        transaction_data["transaction_number"] = self.env[
                            "ir.sequence"
                        ].next_by_code("idil.transaction_booking")
                        transaction = self.env["idil.transaction_booking"].create(
                            transaction_data
                        )
                        _logger.info(
                            "Opening balance transaction created with ID: %s",
                            transaction.id,
                        )

                    # Commit to ensure data is saved
                    self.env.cr.commit()

                    # Update or create booking lines for the transaction
                    self._update_or_create_booking_lines(transaction, vendor)

                    # Create or update vendor transaction record
                    self._create_or_update_vendor_transaction(transaction, vendor)

                except Exception as e:
                    _logger.error(
                        "Error occurred while creating or updating the opening balance transaction: %s",
                        str(e),
                    )
                    raise ValidationError(
                        "Failed to create or update opening balance transaction: %s"
                        % str(e)
                    )

    def _create_or_update_vendor_transaction(self, transaction, vendor):
        try:
            # Check if a Vendor Transaction already exists for the opening balance
            vendor_transaction = self.env["idil.vendor_transaction"].search(
                [("vendor_id", "=", vendor.id), ("reffno", "=", "Opening Balance")],
                limit=1,
            )

            transaction_data = {
                "order_number": "OB" + str(vendor.id),
                "transaction_number": transaction.transaction_number,
                "transaction_date": fields.Date.today(),
                "vendor_id": vendor.id,
                "amount": vendor.opening_balance,
                "paid_amount": 0,
                "remaining_amount": vendor.opening_balance,
                "payment_status": "pending",
                "payment_method": "ap",
                "reffno": "Opening Balance",
                "transaction_booking_id": transaction.id,
            }

            if vendor_transaction:
                vendor_transaction.write(transaction_data)
                _logger.info(
                    "Updated existing vendor transaction for Vendor ID: %s", vendor.id
                )
            else:
                self.env["idil.vendor_transaction"].create(transaction_data)
                _logger.info(
                    "Created new vendor transaction for Vendor ID: %s", vendor.id
                )

        except Exception as e:
            _logger.error(
                "Error occurred while creating or updating vendor transaction for Vendor ID %s: %s",
                vendor.id,
                str(e),
            )
            raise ValidationError(
                "Failed to create or update vendor transaction for Vendor ID %s: %s"
                % (vendor.id, str(e))
            )

    def _update_or_create_booking_lines(self, transaction, vendor):
        try:
            # Find existing debit and credit booking lines for the transaction
            debit_line = self.env["idil.transaction_bookingline"].search(
                [
                    ("transaction_booking_id", "=", transaction.id),
                    ("transaction_type", "=", "dr"),
                ],
                limit=1,
            )
            credit_line = self.env["idil.transaction_bookingline"].search(
                [
                    ("transaction_booking_id", "=", transaction.id),
                    ("transaction_type", "=", "cr"),
                ],
                limit=1,
            )

            # Update or create the debit line
            if debit_line:
                debit_line.write(
                    {
                        "dr_amount": vendor.opening_balance,
                        "cr_amount": 0,
                        "description": "Opening Balance Debit Entry",
                    }
                )
                _logger.info(
                    "Updated debit entry booking line for Vendor ID: %s", vendor.id
                )
            else:
                self.env["idil.transaction_bookingline"].create(
                    {
                        "transaction_booking_id": transaction.id,
                        "account_number": vendor.account_payable_id.id,
                        "transaction_type": "dr",
                        "dr_amount": vendor.opening_balance,
                        "cr_amount": 0,
                        "description": "Opening Balance Debit Entry",
                        "transaction_date": fields.Date.today(),
                    }
                )
                _logger.info(
                    "Created new debit entry booking line for Vendor ID: %s", vendor.id
                )

            # Update or create the credit line
            opening_balance_account = self.env["idil.chart.account"].search(
                [("name", "=", "Opening Balance Account")], limit=1
            )
            if not opening_balance_account:
                raise ValidationError(_("Opening Balance Account not found."))

            if credit_line:
                credit_line.write(
                    {
                        "cr_amount": vendor.opening_balance,
                        "dr_amount": 0,
                        "description": "Opening Balance Credit Entry",
                    }
                )
                _logger.info(
                    "Updated credit entry booking line for Vendor ID: %s", vendor.id
                )
            else:
                self.env["idil.transaction_bookingline"].create(
                    {
                        "transaction_booking_id": transaction.id,
                        "account_number": opening_balance_account.id,
                        "transaction_type": "cr",
                        "cr_amount": vendor.opening_balance,
                        "dr_amount": 0,
                        "description": "Opening Balance Credit Entry",
                        "transaction_date": fields.Date.today(),
                    }
                )
                _logger.info(
                    "Created new credit entry booking line for Vendor ID: %s", vendor.id
                )

        except Exception as e:
            _logger.error(
                "Error occurred while updating or creating booking lines for Vendor ID %s: %s",
                vendor.id,
                str(e),
            )
            raise ValidationError(
                "Failed to update or create booking lines for Vendor ID %s: %s"
                % (vendor.id, str(e))
            )

    def write(self, vals):
        res = super(Vendor, self).write(vals)
        if "opening_balance" in vals and vals.get("opening_balance", 0.0) > 0.0:
            for vendor in self:
                _logger.info(
                    "Invoking create_opening_balance_transaction during vendor update for Vendor ID: %s",
                    vendor.id,
                )
                vendor.create_opening_balance_transaction()
        return res

    @api.constrains("phone")
    def _check_phone(self):
        for record in self:
            if not record.phone.isdigit() or len(record.phone) < 10:
                raise ValidationError(
                    "Phone number must be at least 10 digits and contain only numbers."
                )

    # Method to set vendor as inactive
    def set_inactive(self):
        self.active = False

    # Method to set vendor as active
    def set_active(self):
        self.active = True


class VendorBalanceReport(models.TransientModel):
    _name = "idil.vendor.balance.report"
    _description = "Vendor Balance Report"

    vendor_id = fields.Many2one("idil.vendor.registration", string="Vendor Id")
    vendor_name = fields.Char(string="Vendor Name")
    vendor_tel = fields.Char(string="Vendor Phone number")
    account_id = fields.Many2one("idil.chart.account", string="Account", store=True)
    account_name = fields.Char(string="Account Name")
    account_code = fields.Char(string="Account Code")
    balance = fields.Float(
        string="Balance", store=True
    )  # Assuming you want to store and display this field

    @api.model
    def generate_vendor_balances_report(self):
        self.search([]).unlink()  # Clear existing records to avoid stale data
        account_balances = self._get_vendor_balances()
        for balance in account_balances:
            self.create(
                {
                    "vendor_id": balance["vendor_id"],
                    "vendor_name": balance["vendor_name"],
                    "vendor_tel": balance["vendor_tel"],
                    "account_id": balance["account_id"],
                    "account_name": balance["account_name"],
                    "account_code": balance["account_code"],
                    "balance": balance[
                        "balance"
                    ],  # Make sure to store the calculated balance here
                }
            )

        return {
            "type": "ir.actions.act_window",
            "name": "Vendor Balances",
            "view_mode": "tree",
            "res_model": "idil.vendor.balance.report",
            "domain": [
                ("balance", "<>", 0)
            ],  # Ensures only accounts with non-zero balances are shown
            "context": {"group_by": ["vendor_name"]},
            "target": "new",
        }

    def _get_vendor_balances(self):
        vendor_balances = []
        vendor_personnel = self.env["idil.vendor.registration"].search(
            [("active", "=", True)]
        )
        for vendor in vendor_personnel:
            # Initialize balance for each salesperson.
            booking_lines_balance = 0
            purchase_orders = self.env["idil.purchase_order"].search(
                [("vendor_id", "=", vendor.id)]
            )
            for order in purchase_orders:
                bookings = self.env["idil.transaction_booking"].search(
                    [("order_number", "=", order.id)]
                )
                for booking in bookings:
                    # Filter booking lines by account number equal to salesperson's receivable account.
                    booking_lines = self.env["idil.transaction_bookingline"].search(
                        [
                            ("transaction_booking_id", "=", booking.id),
                            ("account_number", "=", vendor.account_payable_id.id),
                        ]
                    )
                    # Calculate debit and credit sums for filtered booking lines.
                    debit = sum(
                        booking_lines.filtered(
                            lambda r: r.transaction_type == "dr"
                        ).mapped("dr_amount")
                    )
                    credit = sum(
                        booking_lines.filtered(
                            lambda r: r.transaction_type == "cr"
                        ).mapped("cr_amount")
                    )
                    booking_lines_balance += debit - credit

            # Debugging: Log the calculated balance for each salesperson.
            _logger.debug(
                f"Vendor Person: {vendor.name}, Balance: {booking_lines_balance}"
            )

            vendor_balances.append(
                {
                    "vendor_id": vendor.id,
                    "vendor_name": vendor.name,
                    "vendor_tel": vendor.phone,
                    "account_id": (
                        vendor.account_payable_id.id
                        if vendor.account_payable_id
                        else ""
                    ),
                    "account_name": (
                        vendor.account_payable_id.name
                        if vendor.account_payable_id
                        else False
                    ),
                    "account_code": (
                        vendor.account_payable_id.code
                        if vendor.account_payable_id
                        else ""
                    ),
                    "balance": booking_lines_balance,
                }
            )

        return vendor_balances


class VendorTransactionReport(models.TransientModel):
    _name = "idil.vendor.transaction.report"
    _description = "Vendor Transaction Report"

    date = fields.Date(string="Date")
    reference = fields.Char(string="Reference")
    vendor_name = fields.Char(string="Vendor Name")
    vendor_tel = fields.Char(string="Vendor Phone Number")
    invoice = fields.Char(string="Invoice")
    description = fields.Char(string="Description")
    account_name = fields.Char(string="Account Name")
    account_code = fields.Char(string="Account Code")
    account_id = fields.Many2one("idil.chart.account", string="Account")
    debit = fields.Float(string="Dr")
    credit = fields.Float(string="Cr")
    balance = fields.Float(string="Balance")

    @api.model
    def generate_vendor_transaction_report(self):
        self.search([]).unlink()  # Clear existing records
        vendors = self.env["idil.vendor.registration"].search([("active", "=", True)])

        for vendor in vendors:
            # Use account IDs for transaction searches
            account_ids = [
                vendor.account_payable_id.id,
                vendor.account_receivable_id.id,
            ]
            transactions = self.env["idil.transaction_bookingline"].search(
                [("account_number", "in", account_ids)],
                order="transaction_booking_id asc, id asc",
            )
            running_balance = 0

            for transaction in transactions:
                if transaction.transaction_type == "dr":
                    running_balance += transaction.dr_amount
                elif transaction.transaction_type == "cr":
                    running_balance -= transaction.cr_amount

                self.create(
                    {
                        "vendor_name": vendor.name,
                        "vendor_tel": vendor.phone,
                        "account_name": transaction.account_number.name,  # Directly using the related field
                        "account_id": transaction.account_number.id,  # Use account ID
                        "date": transaction.transaction_date,
                        "reference": vendor.phone,
                        "description": vendor.phone or "N/A",
                        "debit": (
                            transaction.dr_amount
                            if transaction.transaction_type == "dr"
                            else 0
                        ),
                        "credit": (
                            transaction.cr_amount
                            if transaction.transaction_type == "cr"
                            else 0
                        ),
                        "balance": abs(running_balance),  # Reflecting running balance
                    }
                )

        return {
            "type": "ir.actions.act_window",
            "name": "Vendor Transaction Report",
            "view_mode": "tree",
            "res_model": "idil.vendor.transaction.report",
            "domain": [],
            "context": {"group_by": ["vendor_name"]},
            "target": "new",
        }
