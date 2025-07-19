from odoo import models, fields, api
from odoo.exceptions import UserError


class SalesReceipt(models.Model):
    _name = "idil.sales.receipt"
    _description = "Sales Receipt"
    _order = "id desc"

    sales_order_id = fields.Many2one(
        "idil.sale.order",
        string="Sale Order",
        ondelete="cascade",  # <--- THIS IS THE KEY!
    )

    salesperson_id = fields.Many2one(
        "idil.sales.sales_personnel",
        string="Salesperson",
        required=False,
    )
    customer_id = fields.Many2one("idil.customer.registration", string="Customer")
    cusotmer_sale_order_id = fields.Many2one(
        "idil.customer.sale.order",
        string="Customer Sale Order",
        ondelete="cascade",
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
        domain="[('account_type', 'in', ['cash', 'bank_transfer', 'sales_expense']), ('currency_id', '=', payment_account_currency_id)]",
    )

    sales_opening_balance_id = fields.Many2one(
        "idil.sales.opening.balance",
        string="Opening Balance",
        ondelete="cascade",
    )
    customer_opening_balance_id = fields.Many2one(
        "idil.customer.opening.balance.line",
        string="Opening Balance",
        ondelete="cascade",
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

            # Validate payment currency consistency
            if record.sales_order_id:
                expected_currency = (
                    record.sales_order_id.sales_person_id.account_receivable_id.currency_id
                )
                entity = f"Salesperson {record.sales_order_id.sales_person_id.name}"

            elif record.cusotmer_sale_order_id:
                expected_currency = record.customer_id.account_receivable_id.currency_id
                entity = f"Customer {record.customer_id.name}"

            elif record.sales_opening_balance_id and record.salesperson_id:
                # Receipt created from opening balance line
                expected_currency = (
                    record.salesperson_id.account_receivable_id.currency_id
                )
                entity = f"Salesperson {record.salesperson_id.name}"

            elif record.customer_opening_balance_id and record.customer_id:
                # Receipt created from opening balance line
                expected_currency = record.customer_id.account_receivable_id.currency_id
                entity = f"Customer {record.customer_id.name}"

            else:
                raise UserError(
                    "Missing sales order reference. Cannot determine related entity."
                )

            if record.payment_account_currency_id != expected_currency:
                raise UserError(
                    f"The payment currency does not match the receivable account currency for {entity}."
                )

            record.paid_amount += record.amount_paying
            record.remaining_amount -= record.amount_paying

            # Determine the correct A/R account based on the type of order or opening balance
            if record.sales_order_id:
                ar_account_id = (
                    record.sales_order_id.sales_person_id.account_receivable_id
                )
                order_name = record.sales_order_id.name
            elif record.cusotmer_sale_order_id:
                ar_account_id = record.customer_id.account_receivable_id
                order_name = record.cusotmer_sale_order_id.name
            elif record.sales_opening_balance_id and record.salesperson_id:
                ar_account_id = record.salesperson_id.account_receivable_id
                order_name = record.sales_opening_balance_id.name

            elif record.customer_opening_balance_id and record.customer_id:
                ar_account_id = record.customer_id.account_receivable_id
                order_name = record.customer_opening_balance_id.opening_balance_id.name

            else:
                raise UserError(
                    "Missing sale order reference to determine A/R account."
                )

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
                    "customer_id": record.customer_id.id,
                    "reffno": order_name,  # Use the Sale Order name as reference
                    "payment_method": "other",
                    "sale_order_id": record.sales_order_id.id,
                    "pos_payment_method": False,  # Update if necessary
                    "payment_status": (
                        "paid" if record.remaining_amount <= 0 else "partial_paid"
                    ),
                    "customer_opening_balance_id": record.customer_opening_balance_id.id,
                    "trx_date": fields.Datetime.now(),
                    "amount": record.paid_amount,
                }
            )
            # Fetch the currencies for both accounts
            payment_currency = record.payment_account.currency_id
            ar_account_currency = ar_account_id.currency_id

            if payment_currency.id != ar_account_currency.id:
                raise UserError(
                    f"The currency of the selected Payment Account ({payment_currency.name or 'N/A'}) "
                    f"does not match the Receivable Account ({ar_account_currency.name or 'N/A'}). "
                    "Please make sure both accounts use the same currency."
                )

            # Create transaction booking lines
            self.env["idil.transaction_bookingline"].create(
                {
                    "transaction_booking_id": transaction_booking.id,
                    "transaction_type": "dr",
                    "description": f"Receipt -- {record.cusotmer_sale_order_id.name if record.cusotmer_sale_order_id else record.sales_order_id.name}",
                    "account_number": record.payment_account.id,
                    "dr_amount": record.amount_paying,
                    "cr_amount": 0,
                    "transaction_date": fields.Datetime.now(),
                    "description": f"Receipt for {order_name}",
                    "customer_opening_balance_id": record.customer_opening_balance_id.id,
                }
            )

            self.env["idil.transaction_bookingline"].create(
                {
                    "transaction_booking_id": transaction_booking.id,
                    "transaction_type": "cr",
                    "description": f"Receipt -- {record.cusotmer_sale_order_id.name if record.cusotmer_sale_order_id else record.sales_order_id.name}",
                    "account_number": ar_account_id.id,
                    "dr_amount": 0,
                    "cr_amount": record.amount_paying,
                    "transaction_date": fields.Datetime.now(),
                    "description": f"Receipt for {order_name}",
                    "customer_opening_balance_id": record.customer_opening_balance_id.id,
                }
            )

            payment = self.env["idil.sales.payment"].create(
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

            # Only create salesperson transaction if the record is for a salesperson (from sale order OR opening balance)
            if record.sales_order_id:
                self.env["idil.salesperson.transaction"].create(
                    {
                        "sales_person_id": record.sales_order_id.sales_person_id.id,
                        "date": fields.Date.today(),
                        "sales_payment_id": payment.id,
                        "order_id": record.sales_order_id.id,
                        "transaction_type": "in",
                        "amount": record.amount_paying,
                        "description": f"Sales Payment Amount for - Receipt ID ({record.id}) - with Order name -- {record.sales_order_id.name}",
                    }
                )
            elif record.sales_opening_balance_id and record.salesperson_id:
                self.env["idil.salesperson.transaction"].create(
                    {
                        "sales_person_id": record.salesperson_id.id,
                        "date": fields.Date.today(),
                        "sales_payment_id": payment.id,
                        "order_id": False,  # No order_id for opening balance
                        "transaction_type": "in",
                        "amount": record.amount_paying,
                        "description": f"Opening Balance Payment Amount for --  ({record.salesperson_id.name}) - Receipt ID ({record.id})  - with ReffNo# -- {record.sales_opening_balance_id.name}",
                    }
                )

            # If the receipt is for a customer, create a payment record and update sale order payment tracking
            if record.cusotmer_sale_order_id:
                # Create customer sale payment entry
                self.env["idil.customer.sale.payment"].create(
                    {
                        "order_id": record.cusotmer_sale_order_id.id,
                        "sales_payment_id": payment.id,
                        "sales_receipt_id": record.id,
                        "customer_id": record.cusotmer_sale_order_id.customer_id.id,
                        "payment_method": "cash",  # or use dynamic logic to determine the method
                        "account_id": record.payment_account.id,
                        "amount": record.amount_paying,
                    }
                )

            if record.customer_opening_balance_id and record.customer_id:
                # Create customer sale payment entry
                self.env["idil.customer.sale.payment"].create(
                    {
                        "order_id": record.customer_opening_balance_id.opening_balance_id.customer_sale_order_id.id,  # No order_id for opening balance
                        "sales_payment_id": payment.id,
                        "sales_receipt_id": record.id,
                        "customer_id": record.customer_opening_balance_id.customer_id.id,
                        "payment_method": "cash",  # or use dynamic logic to determine the method
                        "account_id": record.payment_account.id,
                        "amount": record.amount_paying,
                    }
                )

            # Force recompute on customer sale order
            # record.cusotmer_sale_order_id._compute_total_paid()
            # record.cusotmer_sale_order_id._compute_balance_due()

            record.amount_paying = 0.0  # Reset the amount paying

            if record.remaining_amount <= 0:
                record.payment_status = "paid"
            else:
                record.payment_status = "pending"

    def unlink(self):
        messages = []
        for receipt in self:
            # Block deletion if linked to sales order
            if receipt.sales_order_id and receipt.sales_order_id.exists():
                order_name = (
                    receipt.sales_order_id.display_name
                    or receipt.sales_order_id.name
                    or "Unknown"
                )
                messages.append(f"- Sales Order: {order_name}")

            # Block deletion if linked to customer sale order
            if (
                receipt.cusotmer_sale_order_id
                and receipt.cusotmer_sale_order_id.exists()
            ):
                order_name = (
                    receipt.cusotmer_sale_order_id.display_name
                    or receipt.cusotmer_sale_order_id.name
                    or "Unknown"
                )
                messages.append(f"- Customer Sale Order: {order_name}")

            # Block deletion if linked to any opening balance
            if receipt.sales_opening_balance_id or receipt.customer_opening_balance_id:
                raise UserError(
                    "⚠️ You cannot delete a sales receipt that was created from an opening balance."
                )

        if messages:
            detail = "\n".join(messages)
            raise UserError(
                f"""⚠️ Deletion Not Allowed!

                This sales receipt is linked to the following source(s):
                {detail}

                To maintain proper financial and audit records, you cannot delete a sales receipt directly.
                If you wish to remove this transaction, please delete the parent record instead.

                Do not attempt to delete sales receipts directly.
                Thank you for your understanding and cooperation."""
            )

        return super(SalesReceipt, self).unlink()


class IdilSalesPayment(models.Model):
    _name = "idil.sales.payment"
    _description = "Sales Payment"
    _order = "id desc"

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
            # Delete linked salesperson transactions
            self.env["idil.salesperson.transaction"].search(
                [("sales_payment_id", "=", payment.id)]
            ).unlink()
            self.env["idil.customer.sale.payment"].search(
                [("sales_payment_id", "=", payment.id)]
            ).unlink()
        return super(IdilSalesPayment, self).unlink()
