from odoo import models, fields
from odoo.exceptions import UserError


class SalesReceipt(models.Model):
    _name = "idil.sales.receipt"
    _description = "Sales Receipt"

    sales_order_id = fields.Many2one(
        "idil.sale.order", string="Sale Order", required=True
    )
    salesperson_id = fields.Many2one(
        "idil.sales.sales_personnel",
        string="Salesperson",
        related="sales_order_id.sales_person_id",
        store=True,
        readonly=True,
    )
    receipt_date = fields.Datetime(
        string="Receipt Date", default=fields.Datetime.now, required=True
    )
    due_amount = fields.Float(string="Due Amount", required=True)
    payment_status = fields.Selection(
        [("pending", "Pending"), ("paid", "Paid")], default="pending", required=True
    )
    paid_amount = fields.Float(string="Paid Amount", default=0.0, store=True)
    remaining_amount = fields.Float(string="Due Amount", store=True)
    amount_paying = fields.Float(string="Amount Paying", store=True)
    payment_ids = fields.One2many(
        "idil.sales.payment", "sales_receipt_id", string="Payments"
    )
    payment_account_currency_id = fields.Many2one(
        "res.currency",
        string="Currency",
        default=lambda self: self.env.company.currency_id,
    )

    payment_account = fields.Many2one(
        "idil.chart.account",
        string="Receipt Asset Account",
        help="Payment Account to be used for the receipt -- asset accounts.",
        domain="[('code', 'like', '1'), ('currency_id', '=', payment_account_currency_id)]",
        # Domain to filter accounts starting with '1' and in USD
    )

    def _compute_remaining_amount(self):
        for record in self:
            if record.amount_paying > record.due_amount - record.paid_amount:
                raise UserError(
                    "The amount paying cannot exceed the remaining due amount."
                )
            record.remaining_amount = (
                record.due_amount - record.paid_amount - record.amount_paying
            )

    def action_process_receipt(self):
        for record in self:
            if record.amount_paying <= 0:
                raise UserError("Please enter a valid amount to pay.")
            if record.amount_paying > record.remaining_amount:
                raise UserError("You cannot pay more than the remaining due amount.")

            # Check currency consistency
            if (
                record.payment_account_currency_id
                != record.sales_order_id.sales_person_id.account_receivable_id.currency_id
            ):
                raise UserError(
                    "The payment currency does not match the receivable account currency."
                )

            record.paid_amount += record.amount_paying
            record.remaining_amount -= record.amount_paying

            # Search for transaction source ID using "Receipt"
            trx_source = self.env["idil.transaction.source"].search(
                [("name", "=", "Receipt")], limit=1
            )
            if not trx_source:
                raise UserError("Transaction source 'Receipt' not found.")

            # Create a transaction booking
            transaction_booking = self.env["idil.transaction_booking"].create(
                {
                    "order_number": record.sales_order_id.name,
                    "trx_source_id": trx_source.id,
                    "payment_method": "other",
                    "pos_payment_method": False,  # Update if necessary
                    "payment_status": (
                        "paid" if record.remaining_amount <= 0 else "partial_paid"
                    ),
                    "trx_date": fields.Datetime.now(),
                    "amount": record.paid_amount,
                }
            )

            # Create transaction booking lines
            self.env["idil.transaction_bookingline"].create(
                {
                    "transaction_booking_id": transaction_booking.id,
                    "transaction_type": "dr",
                    "account_number": record.payment_account.id,
                    "dr_amount": record.amount_paying,
                    "cr_amount": 0,
                    "transaction_date": fields.Datetime.now(),
                }
            )

            self.env["idil.transaction_bookingline"].create(
                {
                    "transaction_booking_id": transaction_booking.id,
                    "transaction_type": "cr",
                    "account_number": record.sales_order_id.sales_person_id.account_receivable_id.id,
                    "dr_amount": 0,
                    "cr_amount": record.amount_paying,
                    "transaction_date": fields.Datetime.now(),
                }
            )
            # Create a sales payment
            self.env["idil.sales.payment"].create(
                {
                    "sales_receipt_id": record.id,
                    "transaction_booking_ids": [(4, transaction_booking.id)],
                    "transaction_bookingline_ids": [
                        (4, line.id) for line in transaction_booking.booking_lines
                    ],
                    "payment_account": record.payment_account.id,
                    "payment_date": fields.Datetime.now(),
                    "paid_amount": record.amount_paying,
                }
            )

            record.amount_paying = 0.0  # Reset the amount paying

            if record.remaining_amount <= 0:
                record.payment_status = "paid"
            else:
                record.payment_status = "pending"


class IdilSalesPayment(models.Model):
    _name = "idil.sales.payment"
    _description = "Sales Payment"

    sales_receipt_id = fields.Many2one("idil.sales.receipt", string="Sales Receipt")
    payment_account = fields.Many2one("idil.chart.account", string="Payment Account")
    payment_date = fields.Datetime(string="Payment Date", default=fields.Datetime.now)
    paid_amount = fields.Float(string="Paid Amount")
    transaction_booking_ids = fields.One2many(
        "idil.transaction_booking",
        "sales_payment_id",
        string="Transaction Bookings",
        ondelete="cascade",
    )
    transaction_bookingline_ids = fields.One2many(
        "idil.transaction_bookingline",
        "sales_payment_id",
        string="Transaction Bookings Lines",
        ondelete="cascade",
    )

    def unlink(self):
        for payment in self:
            payment.sales_receipt_id.remaining_amount += payment.paid_amount
            payment.sales_receipt_id.paid_amount -= payment.paid_amount
        return super(IdilSalesPayment, self).unlink()
