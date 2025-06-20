from odoo import models, fields, api
from odoo.exceptions import UserError, ValidationError


class SaleReturn(models.Model):
    _name = "idil.sale.return"
    _description = "Sale Return"
    _inherit = ["mail.thread", "mail.activity.mixin"]

    salesperson_id = fields.Many2one(
        "idil.sales.sales_personnel", string="Salesperson", required=True
    )
    sale_order_id = fields.Many2one(
        "idil.sale.order",
        string="Sale Order",
        required=True,
        domain="[('sales_person_id', '=', salesperson_id)]",
        help="Select a sales order related to the chosen salesperson.",
    )
    return_date = fields.Datetime(
        string="Return Date", default=fields.Datetime.now, required=True
    )
    return_lines = fields.One2many(
        "idil.sale.return.line", "return_id", string="Return Lines", required=True
    )
    state = fields.Selection(
        [("draft", "Draft"), ("confirmed", "Confirmed"), ("cancelled", "Cancelled")],
        default="draft",
        string="Status",
        track_visibility="onchange",
    )
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

    @api.onchange("sale_order_id")
    def _onchange_sale_order_id(self):
        if not self.sale_order_id:
            return
        sale_order = self.sale_order_id
        return_lines = [(5, 0, 0)]  # Clear existing lines

        for line in sale_order.order_lines:
            return_lines.append(
                (
                    0,
                    0,
                    {
                        "product_id": line.product_id.id,
                        "quantity": line.quantity,  # Ensure this is being set
                        "returned_quantity": 0.0,
                        "price_unit": line.price_unit,
                        "subtotal": line.subtotal,
                    },
                )
            )

        self.return_lines = return_lines

    def action_confirm(self):
        for return_order in self:
            if return_order.state != "draft":
                raise UserError("Only draft return orders can be confirmed.")

            for return_line in return_order.return_lines:
                corresponding_sale_line = self.env["idil.sale.order.line"].search(
                    [
                        ("order_id", "=", return_order.sale_order_id.id),
                        ("product_id", "=", return_line.product_id.id),
                    ],
                    limit=1,
                )

                if not corresponding_sale_line:
                    raise ValidationError(
                        f"Sale line not found for product {return_line.product_id.name}."
                    )

                # ✅ Calculate total previously returned qty for this product in this order
                previous_returns = self.env["idil.sale.return.line"].search(
                    [
                        ("return_id.sale_order_id", "=", return_order.sale_order_id.id),
                        ("product_id", "=", return_line.product_id.id),
                        ("return_id", "!=", return_order.id),  # Exclude current draft
                        ("return_id.state", "=", "confirmed"),
                    ]
                )

                total_prev_returned = sum(r.returned_quantity for r in previous_returns)
                new_total = total_prev_returned + return_line.returned_quantity

                if new_total > corresponding_sale_line.quantity:
                    available_to_return = (
                        corresponding_sale_line.quantity - total_prev_returned
                    )
                    raise ValidationError(
                        f"Cannot return {return_line.returned_quantity:.2f} of {return_line.product_id.name}.\n\n"
                        f"✅ Already Returned: {total_prev_returned:.2f}\n"
                        f"✅ Available for Return: {available_to_return:.2f}\n"
                        f"🧾 Original Sold Quantity: {corresponding_sale_line.quantity:.2f}"
                    )

            # Confirm valid return
            self.book_sales_return_entry()
        return_order.state = "confirmed"

    def book_sales_return_entry(self):
        for return_order in self:
            if not return_order.salesperson_id.account_receivable_id:
                raise ValidationError(
                    "The salesperson does not have a receivable account set."
                )

            # Define the expected currency from the salesperson's account receivable
            expected_currency = (
                return_order.salesperson_id.account_receivable_id.currency_id
            )

            # Create a transaction booking for the return
            transaction_booking = self.env["idil.transaction_booking"].create(
                {
                    "sales_person_id": return_order.salesperson_id.id,
                    "sale_return_id": return_order.id,
                    "sale_order_id": return_order.sale_order_id.id,  # Link to the original SaleOrder's ID
                    "trx_source_id": 3,
                    "Sales_order_number": return_order.sale_order_id.id,
                    "payment_method": "bank_transfer",  # Assuming default payment method; adjust as needed
                    "payment_status": "pending",  # Assuming initial payment status; adjust as needed
                    "trx_date": fields.Date.context_today(self),
                    "amount": sum(line.subtotal for line in return_order.return_lines),
                    # Include other necessary fields
                }
            )

            for return_line in return_order.return_lines:
                product = return_line.product_id
                discount_quantity = (
                    (return_line.product_id.discount / 100)
                    * (return_line.returned_quantity)
                    if return_line.product_id.is_quantity_discount
                    else 0.0
                )
                discount_amount = discount_quantity * return_line.price_unit
                commission_amount = (
                    (return_line.returned_quantity - discount_quantity)
                    * return_line.product_id.commission
                    * return_line.price_unit
                )

                subtotal = (
                    (return_line.returned_quantity * return_line.price_unit)
                    - (discount_quantity * return_line.price_unit)
                    - commission_amount
                )
                product_cost_amount = (
                    product.cost * return_line.returned_quantity * self.rate
                )
                # product.stock_quantity = (
                #     product.stock_quantity + return_line.returned_quantity
                # )
                # ------------------------------------------------------------------------------------------------------
                # Reversed Credit entry Expanses inventory of COGS account for the product
                self.env["idil.transaction_bookingline"].create(
                    {
                        "transaction_booking_id": transaction_booking.id,
                        "sale_return_id": return_order.id,
                        "description": f"Sales Return for -- Expanses COGS Account ( {product.name} ) ",
                        "product_id": product.id,
                        "account_number": product.account_cogs_id.id,
                        "transaction_type": "cr",
                        "dr_amount": 0,
                        "cr_amount": product_cost_amount,
                        "transaction_date": fields.Date.context_today(self),
                        # Include other necessary fields
                    }
                )
                # Reversed Credit entry (now as Debit) for asset inventory account of the product
                self.env["idil.transaction_bookingline"].create(
                    {
                        "transaction_booking_id": transaction_booking.id,
                        "sale_return_id": return_order.id,
                        "description": f"Sales Return for -- Product Inventory Account ( {product.name} ) ",
                        "product_id": product.id,
                        "account_number": product.asset_account_id.id,
                        "transaction_type": "dr",  # Reversed transaction (Debit)
                        "dr_amount": product_cost_amount,
                        "cr_amount": 0,
                        "transaction_date": fields.Date.context_today(self),
                        # Include other necessary fields
                    }
                )
                # ------------------------------------------------------------------------------------------------------
                # Reversed Debit entry (now as Credit) for the return line amount
                self.env["idil.transaction_bookingline"].create(
                    {
                        "transaction_booking_id": transaction_booking.id,
                        "sale_return_id": return_order.id,
                        "description": f"Sales Return for -- Account Receivable Account ( {product.name} ) ",
                        "product_id": product.id,
                        "account_number": return_order.salesperson_id.account_receivable_id.id,
                        "transaction_type": "cr",  # Reversed transaction (Credit)
                        "dr_amount": 0,
                        "cr_amount": subtotal,
                        "transaction_date": fields.Date.context_today(self),
                        # Include other necessary fields
                    }
                )

                # Reversed Credit entry (now as Debit) using the product's income account
                self.env["idil.transaction_bookingline"].create(
                    {
                        "transaction_booking_id": transaction_booking.id,
                        "sale_return_id": return_order.id,
                        "description": f"Sales Return for -- Revenue Account Account ( {product.name} ) ",
                        "product_id": product.id,
                        "account_number": product.income_account_id.id,
                        "transaction_type": "dr",  # Reversed transaction (Debit)
                        "dr_amount": subtotal + discount_amount + commission_amount,
                        "cr_amount": 0,
                        "transaction_date": fields.Date.context_today(self),
                        # Include other necessary fields
                    }
                )

                # Reversed Debit entry (now as Credit) for commission expenses

                if product.is_sales_commissionable and commission_amount > 0:
                    self.env["idil.transaction_bookingline"].create(
                        {
                            "transaction_booking_id": transaction_booking.id,
                            "sale_return_id": return_order.id,
                            "description": f"Sales Return for -- Commission Expense Account ( {product.name} ) ",
                            "product_id": product.id,
                            "account_number": product.sales_account_id.id,
                            "transaction_type": "cr",  # Reversed transaction (Credit)
                            "dr_amount": 0,
                            "cr_amount": commission_amount,
                            "transaction_date": fields.Date.context_today(self),
                            # Include other necessary fields
                        }
                    )

                # Reversed Debit entry (now as Credit) for discount expenses

                if discount_amount > 0:
                    self.env["idil.transaction_bookingline"].create(
                        {
                            "transaction_booking_id": transaction_booking.id,
                            "sale_return_id": return_order.id,
                            "description": f"Sales Return for -- Discount Expense Account ( {product.name} ) ",
                            "product_id": product.id,
                            "account_number": product.sales_discount_id.id,
                            "transaction_type": "cr",  # Reversed transaction (Credit)
                            "dr_amount": 0,
                            "cr_amount": discount_amount,
                            "transaction_date": fields.Date.context_today(self),
                            # Include other necessary fields
                        }
                    )
                # Create a Salesperson Transaction

                self.env["idil.salesperson.transaction"].create(
                    {
                        "sales_person_id": return_order.salesperson_id.id,
                        "date": fields.Date.today(),
                        "order_id": return_order.sale_order_id.id,
                        "transaction_type": "in",  # Assuming 'out' for sales
                        "amount": subtotal + discount_amount + commission_amount,
                        "description": f"Sales Retund of - Order Line for {product.name} (Qty: {return_line.returned_quantity})",
                    }
                )
                self.env["idil.salesperson.transaction"].create(
                    {
                        "sales_person_id": return_order.salesperson_id.id,
                        "date": fields.Date.today(),
                        "order_id": return_order.sale_order_id.id,
                        "transaction_type": "in",  # Assuming 'out' for sales
                        "amount": commission_amount * -1,  # Negative for refund
                        "description": f"Sales Retund of - Commission Amount of - Order Line for  {product.name} (Qty: {return_line.returned_quantity})",
                    }
                )
                self.env["idil.salesperson.transaction"].create(
                    {
                        "sales_person_id": return_order.salesperson_id.id,
                        "date": fields.Date.today(),
                        "order_id": return_order.sale_order_id.id,
                        "transaction_type": "in",  # Assuming 'out' for sales --
                        "amount": discount_amount * -1,  # Negative for refund
                        "description": f"Sales Retund of - Discount Amount of - Order Line for  {product.name} (Qty: {return_line.returned_quantity})",
                    }
                )
                self.env["idil.product.movement"].create(
                    {
                        "product_id": product.id,
                        "movement_type": "in",
                        "quantity": return_line.returned_quantity,
                        "date": fields.Datetime.now(),
                        "source_document": return_order.id,
                        "sales_person_id": return_order.salesperson_id.id,
                    }
                )
                # Find the related sales receipt
            sales_receipt = self.env["idil.sales.receipt"].search(
                [("sales_order_id", "=", return_order.sale_order_id.id)], limit=1
            )

            if sales_receipt:
                # Calculate the total return amount (subtotal + discounts + commission)
                total_return_amount = (
                    sum(
                        return_line.subtotal
                        for return_line in return_order.return_lines
                    )
                    - discount_amount
                    - commission_amount
                )

                # Adjust due_amount, paid_amount, and remaining_amount
                sales_receipt.due_amount -= total_return_amount
                sales_receipt.paid_amount = min(
                    sales_receipt.paid_amount, sales_receipt.due_amount
                )  # Ensure paid_amount doesn't exceed due_amount
                sales_receipt.remaining_amount = (
                    sales_receipt.due_amount - sales_receipt.paid_amount
                )  # Remaining is what is still due

                # If due_amount is 0 or less, mark the payment as "paid"
                if sales_receipt.due_amount <= 0:
                    sales_receipt.payment_status = "paid"
                else:
                    sales_receipt.payment_status = "pending"


class SaleReturnLine(models.Model):
    _name = "idil.sale.return.line"
    _description = "Sale Return Line"

    return_id = fields.Many2one(
        "idil.sale.return", string="Sale Return", required=True, ondelete="cascade"
    )
    product_id = fields.Many2one("my_product.product", string="Product", required=True)
    quantity = fields.Float(string="Original Quantity", required=True)
    returned_quantity = fields.Float(string="Returned Quantity", required=True)
    price_unit = fields.Float(string="Unit Price", required=True)
    subtotal = fields.Float(string="Subtotal", compute="_compute_subtotal", store=True)
    previously_returned_qty = fields.Float(
        string="Previously Returned Qty",
        compute="_compute_previously_returned_qty",
        store=False,
        readonly=True,
    )
    available_return_qty = fields.Float(
        string="Available to Return",
        compute="_compute_available_return_qty",
        store=False,
        readonly=True,
    )

    @api.depends("product_id", "return_id.sale_order_id")
    def _compute_previously_returned_qty(self):
        for line in self:
            if (
                not line.product_id
                or not line.return_id
                or not line.return_id.sale_order_id
            ):
                line.previously_returned_qty = 0.0
                continue

            domain = [
                ("product_id", "=", line.product_id.id),
                ("return_id.sale_order_id", "=", line.return_id.sale_order_id.id),
                ("return_id.state", "=", "confirmed"),
            ]

            # Avoid filtering by ID if the line is not saved (has no numeric ID)
            if isinstance(line.id, int):
                domain.append(("id", "!=", line.id))

            previous_lines = self.env["idil.sale.return.line"].search(domain)
            line.previously_returned_qty = sum(
                r.returned_quantity for r in previous_lines
            )

    @api.depends("returned_quantity", "price_unit")
    def _compute_subtotal(self):
        for line in self:
            line.subtotal = line.returned_quantity * line.price_unit

    @api.depends("product_id", "return_id.sale_order_id")
    def _compute_available_return_qty(self):
        for line in self:
            line.available_return_qty = 0.0
            if (
                not line.product_id
                or not line.return_id
                or not line.return_id.sale_order_id
            ):
                continue

            sale_line = self.env["idil.sale.order.line"].search(
                [
                    ("order_id", "=", line.return_id.sale_order_id.id),
                    ("product_id", "=", line.product_id.id),
                ],
                limit=1,
            )

            if not sale_line:
                continue

            domain = [
                ("product_id", "=", line.product_id.id),
                ("return_id.sale_order_id", "=", line.return_id.sale_order_id.id),
                ("return_id.state", "=", "confirmed"),
            ]
            if isinstance(line.id, int):
                domain.append(("id", "!=", line.id))

            previous_lines = self.env["idil.sale.return.line"].search(domain)
            total_prev_returned = sum(r.returned_quantity for r in previous_lines)
            line.available_return_qty = max(
                sale_line.quantity - total_prev_returned, 0.0
            )
