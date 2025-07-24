from venv import logger
from odoo import models, fields, api
from odoo.exceptions import UserError, ValidationError


class ReceiptBulkPayment(models.Model):
    _name = "idil.receipt.bulk.payment"
    _description = "Bulk Sales Receipt Payment"
    _inherit = ["mail.thread", "mail.activity.mixin"]

    name = fields.Char(string="Reference", default="New", readonly=True, copy=False)
    partner_type = fields.Selection(
        [("salesperson", "Salesperson"), ("customer", "Customer")],
        string="Type",
        required=True,
    )
    salesperson_id = fields.Many2one("idil.sales.sales_personnel", string="Salesperson")
    customer_id = fields.Many2one("idil.customer.registration", string="Customer")
    amount_to_pay = fields.Float(
        string="Total Amount to Pay", required=True, store=True
    )

    date = fields.Date(default=fields.Date.context_today, string="Date")
    line_ids = fields.One2many(
        "idil.receipt.bulk.payment.line",
        "bulk_payment_id",
        string="Receipt Lines",
    )
    state = fields.Selection(
        [("draft", "Draft"), ("confirmed", "Confirmed")],
        default="draft",
        string="Status",
    )
    due_receipt_amount = fields.Float(
        string="Total Due Receipt Amount",
        compute="_compute_due_receipt",
        store=False,
    )
    due_receipt_count = fields.Integer(
        string="Number of Due Receipts",
        compute="_compute_due_receipt",
        store=False,
    )
    payment_method_ids = fields.One2many(
        "idil.receipt.bulk.payment.method", "bulk_payment_id", string="Payment Methods"
    )
    payment_methods_total = fields.Float(
        string="Payment Methods Total", compute="_compute_payment_methods_total"
    )

    @api.depends("payment_method_ids.payment_amount")
    def _compute_payment_methods_total(self):
        for rec in self:
            rec.payment_methods_total = sum(
                l.payment_amount for l in rec.payment_method_ids
            )

    @api.constrains("amount_to_pay", "payment_method_ids")
    def _check_payment_method_total(self):
        for rec in self:
            if rec.payment_method_ids:
                total_method = sum(l.payment_amount for l in rec.payment_method_ids)
                if abs(total_method - rec.amount_to_pay) > 0.01:
                    raise ValidationError(
                        "Sum of payment methods must equal Amount to Pay."
                    )

    @api.depends("salesperson_id", "customer_id", "partner_type")
    def _compute_due_receipt(self):
        for rec in self:
            if rec.partner_type == "salesperson" and rec.salesperson_id:
                receipts = rec.env["idil.sales.receipt"].search(
                    [
                        ("salesperson_id", "=", rec.salesperson_id.id),
                        ("payment_status", "=", "pending"),
                    ]
                )
            elif rec.partner_type == "customer" and rec.customer_id:
                receipts = rec.env["idil.sales.receipt"].search(
                    [
                        ("customer_id", "=", rec.customer_id.id),
                        ("payment_status", "=", "pending"),
                    ]
                )
            else:
                receipts = rec.env["idil.sales.receipt"]
            rec.due_receipt_amount = sum(r.due_amount - r.paid_amount for r in receipts)
            rec.due_receipt_count = len(receipts)

    @api.onchange("salesperson_id", "customer_id", "amount_to_pay", "partner_type")
    def _onchange_lines(self):
        self.line_ids = [(5, 0, 0)]
        if self.partner_type == "salesperson" and self.salesperson_id:
            domain = [
                ("salesperson_id", "=", self.salesperson_id.id),
                ("payment_status", "=", "pending"),
            ]
        elif self.partner_type == "customer" and self.customer_id:
            domain = [
                ("customer_id", "=", self.customer_id.id),
                ("payment_status", "=", "pending"),
            ]
        else:
            return
        receipts = self.env["idil.sales.receipt"].search(
            domain, order="receipt_date asc"
        )
        remaining_payment = self.amount_to_pay
        lines = []
        for receipt in receipts:
            if remaining_payment <= 0:
                break
            to_pay = min(receipt.due_amount - receipt.paid_amount, remaining_payment)
            if to_pay > 0:
                lines.append(
                    (
                        0,
                        0,
                        {
                            "receipt_id": receipt.id,
                            "receipt_date": receipt.receipt_date,  # Make sure this field exists and is set in the receipt
                            "due_amount": receipt.due_amount,
                            "paid_amount": receipt.paid_amount,
                            "remaining_amount": receipt.due_amount
                            - receipt.paid_amount,
                            "paid_now": to_pay,
                        },
                    )
                )
                remaining_payment -= to_pay
        self.line_ids = lines

    @api.constrains("amount_to_pay", "salesperson_id", "customer_id", "partner_type")
    def _check_amount(self):
        for rec in self:
            if rec.partner_type == "salesperson" and rec.salesperson_id:
                receipts = rec.env["idil.sales.receipt"].search(
                    [
                        ("salesperson_id", "=", rec.salesperson_id.id),
                        ("payment_status", "=", "pending"),
                    ]
                )
            elif rec.partner_type == "customer" and rec.customer_id:
                receipts = rec.env["idil.sales.receipt"].search(
                    [
                        ("customer_id", "=", rec.customer_id.id),
                        ("payment_status", "=", "pending"),
                    ]
                )
            else:
                continue
            total_due = sum(r.due_amount - r.paid_amount for r in receipts)
            if rec.amount_to_pay > total_due:
                raise ValidationError(
                    f"Total Amount to Pay ({rec.amount_to_pay}) cannot exceed total due ({total_due})."
                )

    @api.constrains("payment_method_ids")
    def _check_at_least_one_payment_method(self):
        for rec in self:
            if not rec.payment_method_ids:
                raise ValidationError("At least one payment method must be added.")

    def action_confirm_payment(self):
        try:
            with self.env.cr.savepoint():
                if self.state != "draft":
                    return

                if not self.payment_method_ids:
                    raise UserError("At least one payment method is required.")

                if self.amount_to_pay <= 0:
                    raise UserError("Payment amount must be greater than zero.")

                if not self.line_ids:
                    raise UserError("No receipt lines to apply payment to.")

                trx_source = self.env["idil.transaction.source"].search(
                    [("name", "=", "Bulk Receipt")], limit=1
                )
                if not trx_source:
                    raise UserError("Transaction source 'Bulk Receipt' not found.")

                remaining_receipts = self.line_ids.filtered(
                    lambda l: l.receipt_id.due_amount > l.receipt_id.paid_amount
                )

                if not remaining_receipts:
                    raise UserError("No valid receipts with remaining due amount.")

                for method in self.payment_method_ids:
                    payment_account = method.payment_account_id
                    if not payment_account:
                        raise UserError(f"Missing payment account.")

                    remaining_amount = method.payment_amount
                    if remaining_amount <= 0:
                        continue

                    for line in remaining_receipts:
                        receipt = line.receipt_id
                        due_balance = receipt.due_amount - receipt.paid_amount

                        if due_balance <= 0 or remaining_amount <= 0:
                            continue

                        to_pay = min(due_balance, remaining_amount)

                        if self.partner_type == "salesperson":
                            ar_account = receipt.salesperson_id.account_receivable_id
                            entity_name = receipt.salesperson_id.name
                            is_salesperson = True
                        elif self.partner_type == "customer":
                            ar_account = receipt.customer_id.account_receivable_id
                            entity_name = receipt.customer_id.name
                            is_salesperson = False
                        else:
                            raise UserError("Invalid partner type.")

                        if ar_account.currency_id.id != payment_account.currency_id.id:
                            raise UserError(
                                f"Currency mismatch between payment account ({payment_account.currency_id.name}) "
                                f"and receivable account ({ar_account.currency_id.name}) for {entity_name}."
                            )

                        # Create Transaction Booking
                        trx_booking = self.env["idil.transaction_booking"].create(
                            {
                                "order_number": (
                                    receipt.sales_order_id.name
                                    if receipt.sales_order_id
                                    else "/"
                                ),
                                "trx_source_id": trx_source.id,
                                "payment_method": "other",
                                "customer_id": (
                                    receipt.customer_id.id
                                    if receipt.customer_id
                                    else False
                                ),
                                "reffno": self.name,
                                "sale_order_id": (
                                    receipt.sales_order_id.id
                                    if receipt.sales_order_id
                                    else False
                                ),
                                "payment_status": (
                                    "paid" if to_pay >= due_balance else "partial_paid"
                                ),
                                "customer_opening_balance_id": receipt.customer_opening_balance_id.id,
                                "trx_date": fields.Datetime.now(),
                                "amount": to_pay,
                            }
                        )

                        # Booking lines (DR from method account, CR to AR)
                        dr_line = self.env["idil.transaction_bookingline"].create(
                            {
                                "transaction_booking_id": trx_booking.id,
                                "transaction_type": "dr",
                                "account_number": payment_account.id,
                                "dr_amount": to_pay,
                                "cr_amount": 0.0,
                                "transaction_date": fields.Datetime.now(),
                                "description": f"Bulk Receipt - {self.name}",
                                "customer_opening_balance_id": receipt.customer_opening_balance_id.id,
                            }
                        )

                        cr_line = self.env["idil.transaction_bookingline"].create(
                            {
                                "transaction_booking_id": trx_booking.id,
                                "transaction_type": "cr",
                                "account_number": ar_account.id,
                                "dr_amount": 0.0,
                                "cr_amount": to_pay,
                                "transaction_date": fields.Datetime.now(),
                                "description": f"Bulk Receipt - {self.name}",
                                "customer_opening_balance_id": receipt.customer_opening_balance_id.id,
                            }
                        )

                        # Sales Payment record (per method)
                        payment = self.env["idil.sales.payment"].create(
                            {
                                "sales_receipt_id": receipt.id,
                                "payment_method_ids": [(4, method.id)],
                                "transaction_booking_ids": [(4, trx_booking.id)],
                                "transaction_bookingline_ids": [
                                    (4, dr_line.id),
                                    (4, cr_line.id),
                                ],
                                "payment_account": payment_account.id,
                                "payment_date": fields.Datetime.now(),
                                "paid_amount": to_pay,
                            }
                        )
                        method.write(
                            {"sales_payment_id": payment.id}
                        )  # If assigning after create

                        # Update receipt
                        receipt.paid_amount += to_pay
                        receipt.remaining_amount = (
                            receipt.due_amount - receipt.paid_amount
                        )
                        receipt.payment_status = (
                            "paid" if receipt.remaining_amount <= 0 else "pending"
                        )
                        line.paid_now += to_pay

                        # Transaction: salesperson or customer
                        if is_salesperson:
                            self.env["idil.salesperson.transaction"].create(
                                {
                                    "sales_person_id": receipt.salesperson_id.id,
                                    "date": fields.Date.today(),
                                    "sales_payment_id": payment.id,
                                    "sales_receipt_id": receipt.id,
                                    "order_id": (
                                        receipt.sales_order_id.id
                                        if receipt.sales_order_id
                                        else False
                                    ),
                                    "transaction_type": "in",
                                    "amount": to_pay,
                                    "description": f"Bulk Payment - Receipt {receipt.id} - Order {receipt.sales_order_id.name if receipt.sales_order_id else ''}",
                                }
                            )
                        else:
                            self.env["idil.customer.sale.payment"].create(
                                {
                                    "order_id": (
                                        receipt.cusotmer_sale_order_id.id
                                        if receipt.cusotmer_sale_order_id
                                        else False
                                    ),
                                    "customer_id": receipt.customer_id.id,
                                    "payment_method": "cash",
                                    "sales_payment_id": payment.id,
                                    "sales_receipt_id": receipt.id,
                                    "account_id": payment_account.id,
                                    "amount": to_pay,
                                }
                            )

                        # Recompute order
                        if receipt.cusotmer_sale_order_id:
                            receipt.cusotmer_sale_order_id._compute_total_paid()
                            receipt.cusotmer_sale_order_id._compute_balance_due()

                        # Deduct from method amount
                        remaining_amount -= to_pay

                    if remaining_amount > 0:
                        raise UserError(
                            f"⚠️ Payment method '{payment_account.name}' has {remaining_amount:.2f} unallocated."
                        )

                self.state = "confirmed"
        except Exception as e:
            logger.error(f"transaction failed: {str(e)}")
            raise ValidationError(f"Transaction failed: {str(e)}")

    @api.model
    def create(self, vals):
        if vals.get("name", "New") == "New":
            vals["name"] = (
                self.env["ir.sequence"].next_by_code("idil.receipt.bulk.payment.seq")
                or "BRP/0001"
            )
        return super().create(vals)

    # def write(self, vals):
    #     for rec in self:
    #         if rec.state == "confirmed":
    #             raise ValidationError(
    #                 "This record is confirmed and cannot be modified.\nIf changes are required, please delete and create a new bulk payment."
    #             )
    #     return super().write(vals)

    def write(self, vals):
        for rec in self:
            if rec.state == "confirmed":
                allowed_fields = {"amount_to_pay"}
                incoming_fields = set(vals.keys())

                # If there's any field being updated that's not allowed
                if not incoming_fields.issubset(allowed_fields):
                    raise ValidationError(
                        "This record is confirmed and cannot be modified.\n"
                        "Only 'amount_to_pay' can be adjusted automatically when a sales payment is deleted."
                    )
        return super().write(vals)

    def unlink(self):
        try:
            with self.env.cr.savepoint():
                for rec in self:
                    if rec.state == "confirmed":
                        for line in rec.line_ids:
                            receipt = line.receipt_id

                            # ✅ Revert paid amount
                            # receipt.paid_amount -= line.paid_now
                            # receipt.remaining_amount = receipt.remaining_amount + line.paid_now
                            # receipt.payment_status = (
                            #     "pending" if receipt.remaining_amount > 0 else "paid"
                            # )

                            # ✅ Delete Sales Payment
                            payments = self.env["idil.sales.payment"].search(
                                [("sales_receipt_id", "=", receipt.id)]
                            )
                            for payment in payments:
                                # Detach transactions
                                trx_bookings = payment.transaction_booking_ids
                                trx_lines = payment.transaction_bookingline_ids

                                # Delete booking lines
                                trx_lines.unlink()

                                # Delete booking
                                trx_bookings.unlink()

                                # Delete customer/salesperson transaction
                                self.env["idil.salesperson.transaction"].search(
                                    [("sales_payment_id", "=", payment.id)]
                                ).unlink()

                                self.env["idil.customer.sale.payment"].search(
                                    [
                                        (
                                            "order_id",
                                            "=",
                                            (
                                                receipt.cusotmer_sale_order_id.id
                                                if receipt.cusotmer_sale_order_id
                                                else False
                                            ),
                                        ),
                                        ("amount", "=", payment.paid_amount),
                                    ]
                                ).unlink()

                                # Delete payment
                                payment.unlink()

                            # ✅ Recompute order totals if needed
                            if receipt.cusotmer_sale_order_id:
                                receipt.cusotmer_sale_order_id._compute_total_paid()
                                receipt.cusotmer_sale_order_id._compute_balance_due()

                        # ✅ Remove bulk payment lines & payment methods
                        rec.line_ids.unlink()
                        rec.payment_method_ids.unlink()

                    super(ReceiptBulkPayment, rec).unlink()
                return True
        except Exception as e:
            logger.error(f"transaction failed: {str(e)}")
            raise ValidationError(f"Transaction failed: {str(e)}")


class ReceiptBulkPaymentLine(models.Model):
    _name = "idil.receipt.bulk.payment.line"
    _description = "Bulk Receipt Payment Line"

    bulk_payment_id = fields.Many2one(
        "idil.receipt.bulk.payment", string="Bulk Payment"
    )
    receipt_id = fields.Many2one("idil.sales.receipt", string="Receipt", required=True)
    receipt_date = fields.Datetime(related="receipt_id.receipt_date", store=True)
    due_amount = fields.Float(related="receipt_id.due_amount", store=True)
    paid_amount = fields.Float(related="receipt_id.paid_amount", store=True)
    remaining_amount = fields.Float(compute="_compute_remaining_amount", store=True)
    paid_now = fields.Float(string="Paid Now", store=True)

    customer_id = fields.Many2one(
        related="receipt_id.customer_id",
        string="Customer",
        readonly=True,
    )
    salesperson_id = fields.Many2one(
        related="receipt_id.salesperson_id",
        string="Salesperson",
        readonly=True,
    )
    receipt_status = fields.Selection(
        related="receipt_id.payment_status",
        string="Status",
        readonly=True,
    )

    @api.depends("due_amount", "paid_amount")
    def _compute_remaining_amount(self):
        for rec in self:
            rec.remaining_amount = (rec.due_amount or 0) - (rec.paid_amount or 0)


class ReceiptBulkPaymentMethod(models.Model):
    _name = "idil.receipt.bulk.payment.method"
    _description = "Bulk Receipt Payment Method"

    bulk_payment_id = fields.Many2one(
        "idil.receipt.bulk.payment", string="Bulk Payment"
    )
    payment_account_id = fields.Many2one(
        "idil.chart.account",
        string="Payment Account",
        required=True,
        domain=[("account_type", "in", ["cash", "bank_transfer", "sales_expense"])],
    )
    payment_amount = fields.Float(string="Amount", required=True)
    note = fields.Char(string="Memo/Reference")
    sales_payment_id = fields.Many2one(
        "idil.sales.payment",
        string="Linked Sales Payment",
        ondelete="cascade",  # This makes it auto-delete if sales payment is deleted
    )
