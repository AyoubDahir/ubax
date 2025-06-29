from odoo import models, fields, api
from odoo.exceptions import ValidationError


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

    # @api.onchange("salesperson_id", "customer_id", "amount_to_pay", "partner_type")
    # def _onchange_lines(self):
    #     # Clear lines first
    #     self.line_ids = [(5, 0, 0)]
    #     if self.partner_type == "salesperson" and self.salesperson_id:
    #         domain = [
    #             ("salesperson_id", "=", self.salesperson_id.id),
    #             ("payment_status", "=", "pending"),
    #         ]
    #     elif self.partner_type == "customer" and self.customer_id:
    #         domain = [
    #             ("customer_id", "=", self.customer_id.id),
    #             ("payment_status", "=", "pending"),
    #         ]
    #     else:
    #         return
    #     receipts = self.env["idil.sales.receipt"].search(
    #         domain, order="receipt_date asc"
    #     )
    #     remaining_payment = self.amount_to_pay
    #     lines = []
    #     for receipt in receipts:
    #         if remaining_payment <= 0:
    #             break
    #         to_pay = min(receipt.due_amount - receipt.paid_amount, remaining_payment)
    #         if to_pay > 0:
    #             lines.append(
    #                 (
    #                     0,
    #                     0,
    #                     {
    #                         "receipt_id": receipt.id,
    #                         "receipt_date": receipt.receipt_date,
    #                         "due_amount": receipt.due_amount,
    #                         "paid_amount": receipt.paid_amount,
    #                         "remaining_amount": receipt.due_amount
    #                         - receipt.paid_amount,
    #                         "paid_now": to_pay,
    #                     },
    #                 )
    #             )
    #             remaining_payment -= to_pay
    #     self.line_ids = lines

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

    def action_confirm_payment(self):
        if self.state != "draft":
            return

        # Optional: check account balance here if required

        remaining_payment = self.amount_to_pay

        for line in self.line_ids:
            if remaining_payment <= 0:
                break
            receipt = line.receipt_id
            receipt_remaining = receipt.due_amount - receipt.paid_amount
            to_pay = min(remaining_payment, receipt_remaining)
            if to_pay > 0:
                # Write the paying amount, then process receipt (your existing logic)
                receipt.amount_paying = to_pay

                receipt.action_process_receipt()
                # Update line with actual paid
                line.write({"paid_now": to_pay})
                remaining_payment -= to_pay
        self.state = "confirmed"

    @api.model
    def create(self, vals):
        if vals.get("name", "New") == "New":
            vals["name"] = (
                self.env["ir.sequence"].next_by_code("idil.receipt.bulk.payment.seq")
                or "BRP/0001"
            )
        return super().create(vals)

    def write(self, vals):
        for rec in self:
            if rec.state == "confirmed":
                raise ValidationError(
                    "This record is confirmed and cannot be modified.\nIf changes are required, please delete and create a new bulk payment."
                )
        return super().write(vals)

    def unlink(self):
        for bulk in self:
            if bulk.state == "confirmed":
                raise ValidationError("Cannot delete a confirmed bulk payment.")
            for line in bulk.line_ids:
                # Optionally revert payment if needed
                line.unlink()
        return super().unlink()


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
        domain=[("account_type", "in", ["cash", "bank_transfer"])],
    )
    payment_amount = fields.Float(string="Amount", required=True)
    note = fields.Char(string="Memo/Reference")
