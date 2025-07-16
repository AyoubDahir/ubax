from odoo import models, fields, api, exceptions
from datetime import datetime
from datetime import date
import re
from odoo.exceptions import ValidationError, UserError
import logging

_logger = logging.getLogger(__name__)


class CustomerSaleReturn(models.Model):
    _name = "idil.customer.sale.return"
    _description = "Customer Sale Return"

    name = fields.Char(string="Return Reference", default="New", readonly=True)
    customer_id = fields.Many2one(
        "idil.customer.registration", string="Customer", required=True
    )
    sale_order_id = fields.Many2one(
        "idil.customer.sale.order",
        string="Sale Order",
        domain="[('customer_id', '=', customer_id)]",
    )
    return_date = fields.Date(default=fields.Date.context_today, string="Return Date")
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("confirmed", "Confirmed"),
            ("cancel", "Cancelled"),
        ],
        default="draft",
        string="Status",
    )

    return_lines = fields.One2many(
        "idil.customer.sale.return.line", "return_id", string="Return Lines"
    )

    @api.model
    def create(self, vals):
        if vals.get("name", "New") == "New":
            vals["name"] = (
                self.env["ir.sequence"].next_by_code("idil.customer.sale.return")
                or "New"
            )
        return super().create(vals)

    @api.onchange("sale_order_id")
    def _onchange_sale_order_id(self):
        if self.sale_order_id:
            self.return_lines = [(5, 0, 0)]  # Clear lines
            lines = []
            for order_line in self.sale_order_id.order_lines:
                # Fetch previously confirmed return qty for this line
                prev_return_lines = self.env["idil.customer.sale.return.line"].search(
                    [
                        ("sale_order_line_id", "=", order_line.id),
                        ("return_id.state", "=", "confirmed"),
                    ]
                )
                total_prev_returned = sum(r.return_quantity for r in prev_return_lines)
                returnable_qty = max(order_line.quantity - total_prev_returned, 0.0)

                lines.append(
                    (
                        0,
                        0,
                        {
                            "sale_order_line_id": order_line.id,
                            "product_id": order_line.product_id.id,
                            "original_quantity": order_line.quantity,
                            "price_unit": order_line.price_unit,
                            "returnable_quantity": returnable_qty,
                        },
                    )
                )
            self.return_lines = lines

    def action_process(self):
        for rec in self:
            if rec.state != "draft":
                raise ValidationError("Only draft returns can be processed.")

            trx_source = self.env["idil.transaction.source"].search(
                [("name", "=", "Sale Return")], limit=1
            )
            if not trx_source:
                raise ValidationError("Transaction source 'Sale Return' not found.")
            total_return_amount = 0
            for line in rec.return_lines:
                if line.return_quantity <= 0:
                    continue
                if line.return_quantity > line.original_quantity:
                    raise ValidationError(
                        f"Return quantity for '{line.product_id.name}' exceeds the original sold quantity."
                    )

                # 1. Stock update
                product = line.product_id
                product.stock_quantity += line.return_quantity

                # 2. Stock movement (in)
                self.env["idil.product.movement"].create(
                    {
                        "product_id": product.id,
                        "movement_type": "in",
                        "quantity": line.return_quantity,
                        "date": fields.Datetime.now(),
                        "source_document": "Customer Sale Return: " + rec.name,
                        "customer_id": rec.customer_id.id,
                    }
                )

                # 3. Reverse accounting
                original_line = line.sale_order_line_id
                original_booking = self.env["idil.transaction_booking"].search(
                    [("cusotmer_sale_order_id", "=", rec.sale_order_id.id)], limit=1
                )

                if not original_booking:
                    raise ValidationError("Original transaction not found.")

                bom_currency = (
                    product.bom_id.currency_id
                    if product.bom_id
                    else product.currency_id
                )

                amount_in_bom_currency = original_line.price_unit * line.return_quantity

                if bom_currency.name == "USD":
                    reverse_amount = amount_in_bom_currency * self.rate
                else:
                    reverse_amount = amount_in_bom_currency

                _logger.info(
                    f"Product Cost Amount: {reverse_amount} for product {product.name}"
                )

                total_return_amount += reverse_amount  # Accumulate return total

                # Create new reversed booking
                reversed_booking = self.env["idil.transaction_booking"].create(
                    {
                        "transaction_number": self.env[
                            "idil.transaction_booking"
                        ]._get_next_transaction_number(),
                        "trx_source_id": trx_source.id,
                        "customer_id": rec.customer_id.id,
                        "reffno": rec.name,
                        "trx_date": rec.return_date,
                        "amount": reverse_amount,
                        "amount_paid": 0,
                        "remaining_amount": 0,
                        "payment_status": "paid",
                        "customer_sales_return_id": line.id,
                    }
                )

                # Reverse each line from original
                original_lines = self.env["idil.transaction_bookingline"].search(
                    [
                        ("transaction_booking_id", "=", original_booking.id),
                        ("product_id", "=", product.id),
                    ]
                )
                for orig in original_lines:
                    self.env["idil.transaction_bookingline"].create(
                        {
                            "transaction_booking_id": reversed_booking.id,
                            "account_number": orig.account_number.id,
                            "product_id": orig.product_id.id,
                            "transaction_type": (
                                "cr" if orig.transaction_type == "dr" else "dr"
                            ),
                            "dr_amount": (
                                reverse_amount if orig.transaction_type == "cr" else 0.0
                            ),
                            "cr_amount": (
                                reverse_amount if orig.transaction_type == "dr" else 0.0
                            ),
                            "transaction_date": rec.return_date,
                            "company_id": self.env.company.id,
                            "description": "Reversal of " + orig.description,
                            "customer_sales_return_id": line.id,
                        }
                    )

                    # ✅ Total return amount (sum of reverse amounts for all lines in this return)
                    # ✅ Now update the sales receipt once based on total return
                    # ✅ Collect total reverse amount for all lines
                # ✅ Update receipt only once, outside the loop
                if total_return_amount > 0:
                    receipt = self.env["idil.sales.receipt"].search(
                        [("cusotmer_sale_order_id", "=", rec.sale_order_id.id)], limit=1
                    )
                    # 3. Validate against paid amount
                    if receipt and total_return_amount > receipt.remaining_amount:
                        raise ValidationError(
                            f"Return amount ({total_return_amount}) exceeds remaining amount ({receipt.remaining_amount}) on the receipt.\n"
                            f"The customer has already paid too much to allow this return without refund.\n"
                            f"Please verify payment first."
                        )
                    if receipt:
                        new_due = max(receipt.due_amount - total_return_amount, 0.0)
                        new_remaining = max(new_due - receipt.paid_amount, 0.0)

                        receipt.write(
                            {
                                "due_amount": new_due,
                                "remaining_amount": new_remaining,
                                "payment_status": (
                                    "paid" if new_remaining <= 0 else "pending"
                                ),
                            }
                        )

            rec.state = "confirmed"

    @api.model
    def create(self, vals):
        if vals.get("name", "New") == "New":
            vals["name"] = (
                self.env["ir.sequence"].next_by_code("idil.customer.sale.return")
                or "New"
            )

        return_obj = super().create(vals)

        return return_obj


class CustomerSaleReturnLine(models.Model):
    _name = "idil.customer.sale.return.line"
    _description = "Customer Sale Return Line"

    return_id = fields.Many2one(
        "idil.customer.sale.return",
        string="Sale Return",
        required=True,
        ondelete="cascade",
    )
    sale_order_line_id = fields.Many2one(
        "idil.customer.sale.order.line", string="Original Order Line", store=True
    )
    product_id = fields.Many2one("my_product.product", string="Product", required=True)

    original_quantity = fields.Float(string="Original Quantity", store=True)
    price_unit = fields.Float(string="Unit Price", store=True)
    returnable_quantity = fields.Float(
        string="Returnable Quantity",
        compute="_compute_returned_and_returnable",
        store=True,
    )
    return_quantity = fields.Float(string="Return Quantity")
    previously_returned_quantity = fields.Float(
        string="Previously Returned",
        compute="_compute_returned_and_returnable",
        store=False,
    )
    total_amount = fields.Float(
        string="Total Amount",
        compute="_compute_total_amount",
        store=True,
        readonly=True,
    )

    @api.depends("return_quantity", "price_unit")
    def _compute_total_amount(self):
        for line in self:
            line.total_amount = line.return_quantity * line.price_unit

    @api.depends("sale_order_line_id")
    def _compute_returned_and_returnable(self):
        for line in self:
            if not line.sale_order_line_id:
                line.previously_returned_quantity = 0.0
                line.returnable_quantity = 0.0
                continue

            domain = [
                ("sale_order_line_id", "=", line.sale_order_line_id.id),
                ("return_id.state", "=", "confirmed"),
            ]

            # ✅ Only add exclusion if this line has a real ID (not a virtual/new one)
            if line.id and isinstance(line.id, int):
                domain.append(("id", "!=", line.id))

            prev_returns = self.env["idil.customer.sale.return.line"].search(domain)
            total_prev = sum(prev.return_quantity for prev in prev_returns)

            line.previously_returned_quantity = total_prev
            line.returnable_quantity = max(line.original_quantity - total_prev, 0.0)

    @api.constrains("return_quantity", "returnable_quantity")
    def _check_return_quantity(self):
        for line in self:
            if line.return_quantity <= 0:
                raise ValidationError(
                    f"Return quantity for product '{line.product_id.name}' must be greater than 0."
                )

            if line.return_quantity > line.returnable_quantity:
                raise ValidationError(
                    f"Return quantity for product '{line.product_id.name}' cannot exceed available returnable quantity ({line.returnable_quantity})."
                )
