from email.utils import format_datetime
import re

from odoo import models, fields, api
from odoo.exceptions import UserError, ValidationError
import logging
from odoo.tools import datetime, format_datetime

_logger = logging.getLogger(__name__)


class SaleOrder(models.Model):
    _name = "idil.sale.order"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _description = "Sale Order"
    _order = "id desc"

    name = fields.Char(string="Sales Reference", tracking=True)

    sales_person_id = fields.Many2one(
        "idil.sales.sales_personnel", string="Salesperson", required=True
    )
    # Add a reference to the salesperson's order
    salesperson_order_id = fields.Many2one(
        "idil.salesperson.place.order",
        string="Related Salesperson Order",
        help="This field links to the salesperson order "
        "that this actual order is based on.",
    )

    order_date = fields.Datetime(string="Order Date", default=fields.Datetime.now)
    order_lines = fields.One2many(
        "idil.sale.order.line", "order_id", string="Order Lines"
    )
    order_total = fields.Float(
        string="Order Total", compute="_compute_order_total", store=True
    )
    state = fields.Selection(
        [("draft", "Draft"), ("confirmed", "Confirmed"), ("cancel", "Cancelled")],
        default="confirmed",
    )

    commission_amount = fields.Float(
        string="Commission Amount", compute="_compute_total_commission", store=True
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
    total_due_usd = fields.Float(
        string="Total Due (USD)", compute="_compute_totals_in_usd", store=True
    )
    total_commission_usd = fields.Float(
        string="Commission (USD)", compute="_compute_totals_in_usd", store=True
    )
    total_discount_usd = fields.Float(
        string="Discount (USD)", compute="_compute_totals_in_usd", store=True
    )
    total_returned_qty = fields.Float(
        string="Total Returned Quantity",
        compute="_compute_total_returned_qty",
        store=False,
        readonly=True,
    )

    @api.depends("order_lines", "order_lines.product_id")
    def _compute_total_returned_qty(self):
        for order in self:
            total_returned = 0.0

            # Find all confirmed returns linked to this order
            return_lines = self.env["idil.sale.return.line"].search(
                [
                    ("return_id.sale_order_id", "=", order.id),
                    ("return_id.state", "=", "confirmed"),
                ]
            )

            # Sum all returned quantities
            total_returned = sum(return_lines.mapped("returned_quantity"))

            order.total_returned_qty = total_returned

    @api.depends(
        "order_lines.subtotal",
        "order_lines.commission_amount",
        "order_lines.discount_amount",
        "rate",
    )
    def _compute_totals_in_usd(self):
        for order in self:
            subtotal = sum(order.order_lines.mapped("subtotal"))
            commission = sum(order.order_lines.mapped("commission_amount"))
            discount = sum(order.order_lines.mapped("discount_amount"))

            rate = order.rate or 0.0
            order.total_due_usd = subtotal / rate if rate else 0.0
            order.total_commission_usd = commission / rate if rate else 0.0
            order.total_discount_usd = discount / rate if rate else 0.0

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

    @api.depends("order_lines.quantity", "order_lines.product_id.commission")
    def _compute_total_commission(self):
        for order in self:
            total_commission = 0.0
            for line in order.order_lines:
                product = line.product_id
                if product.is_sales_commissionable:
                    if not product.sales_account_id:
                        raise ValidationError(
                            f"Product '{product.name}' does not have a Sales Commission Account set."
                        )
                    if product.commission <= 0:
                        raise ValidationError(
                            f"Product '{product.name}' does not have a valid Commission Rate set."
                        )

                    # Calculate commission only if validations pass
                    total_commission += line.commission_amount

            order.commission_amount = total_commission

    @api.model
    def create(self, vals):
        # Step 1: Check if sales_person_id is provided in vals
        if "sales_person_id" in vals:
            salesperson_id = vals["sales_person_id"]

            # Step 2: Find the most recent draft SalespersonOrder for this salesperson
            salesperson_order = self.env["idil.salesperson.place.order"].search(
                [("salesperson_id", "=", salesperson_id), ("state", "=", "draft")],
                order="order_date desc",
                limit=1,
            )

            if salesperson_order:
                # Link the found SalespersonOrder to the SaleOrder being created
                vals["salesperson_order_id"] = salesperson_order.id
                salesperson_order.write({"state": "confirmed"})
            else:
                # Optionally handle the case where no draft SalespersonOrder is found
                raise UserError(
                    "No draft Sales person order found for the given sales person."
                )

            # Set order reference if not provided
            if "name" not in vals or not vals["name"]:
                vals["name"] = self._generate_order_reference(vals)

        # Proceed with creating the SaleOrder with the updated vals
        new_order = super(SaleOrder, self).create(vals)
        # Create a corresponding SalesReceipt
        self.env["idil.sales.receipt"].create(
            {
                "sales_order_id": new_order.id,
                "due_amount": new_order.order_total,
                "receipt_date": new_order.order_date,
                "paid_amount": 0,
                "remaining_amount": new_order.order_total,
                "salesperson_id": new_order.sales_person_id.id,
            }
        )

        for line in new_order.order_lines:
            self.env["idil.product.movement"].create(
                {
                    "product_id": line.product_id.id,
                    "sale_order_id": new_order.id,
                    "movement_type": "out",
                    "quantity": line.quantity * -1,
                    "date": new_order.order_date,
                    "source_document": new_order.name,
                    "sales_person_id": new_order.sales_person_id.id,
                }
            )

        new_order.book_accounting_entry()

        return new_order

    def _generate_order_reference(self, vals):
        bom_id = vals.get("bom_id", False)
        if bom_id:
            bom = self.env["idil.bom"].browse(bom_id)
            bom_name = (
                re.sub("[^A-Za-z0-9]+", "", bom.name[:2]).upper()
                if bom and bom.name
                else "XX"
            )
            date_str = "/" + datetime.now().strftime("%d%m%Y")
            day_night = "/DAY/" if datetime.now().hour < 12 else "/NIGHT/"
            sequence = self.env["ir.sequence"].next_by_code("idil.sale.order.sequence")
            sequence = sequence[-3:] if sequence else "000"
            return f"{bom_name}{date_str}{day_night}{sequence}"
        else:
            # Fallback if no BOM is provided
            return self.env["ir.sequence"].next_by_code("idil.sale.order.sequence")

    @api.depends("order_lines.subtotal")
    def _compute_order_total(self):
        for order in self:
            order.order_total = sum(order.order_lines.mapped("subtotal"))

    @api.onchange("sales_person_id")
    def _onchange_sales_person_id(self):
        if not self.sales_person_id:
            return
        # Assuming 'order_date' is the field name for the order's date in both models
        current_order_date = (
            fields.Date.today()
        )  # Adjust if the order date is not today

        last_order = self.env["idil.salesperson.place.order"].search(
            [("salesperson_id", "=", self.sales_person_id.id), ("state", "=", "draft")],
            order="order_date desc",
            limit=1,
        )

        if last_order:
            # Check if the last order's date is the same as the current order's date
            last_order_date = fields.Date.to_date(last_order.order_date)

            # Prepare a list of commands to update 'order_lines' one2many field
            order_lines_cmds = [
                (5, 0, 0)
            ]  # Command to delete all existing records in the set
            for line in last_order.order_lines:
                discount_quantity = (
                    (line.product_id.discount / 100) * (line.quantity)
                    if line.product_id.is_quantity_discount
                    else 0.0
                )

                order_lines_cmds.append(
                    (
                        0,
                        0,
                        {
                            "product_id": line.product_id.id,
                            "quantity_Demand": line.quantity,
                            "discount_quantity": discount_quantity,
                            "quantity": line.quantity,
                            # Set initial 'quantity' the same as 'quantity_Demand'
                            # Add other necessary fields here
                        },
                    )
                )

            # Apply the commands to the 'order_lines' field
            self.order_lines = order_lines_cmds

        else:
            raise UserError(
                "This salesperson does not have any draft orders to reference."
            )

    def book_accounting_entry(self):
        """
        Create a transaction booking for the given SaleOrder, with entries for:

        1. Debiting the Asset Inventory account for each order line's product
        2. Crediting the COGS account for each order line's product
        3. Debiting the Sales Account Receivable for each order line's amount
        4. Crediting the product's income account for each order line's amount
        5. Debiting the Sales Commission account for each order line's commission amount (if applicable)
        6. Debiting the Sales Discount account for each order line's discount amount (if applicable)
        """
        for order in self:
            if not order.sales_person_id.account_receivable_id:
                raise ValidationError(
                    "The salesperson does not have a receivable account set."
                )

            # Define the expected currency from the salesperson's account receivable
            expected_currency = order.sales_person_id.account_receivable_id.currency_id

            trx_source_id = self.env["idil.transaction.source"].search(
                [("name", "=", "Sales Order")], limit=1
            )
            if not trx_source_id:
                raise ValidationError(
                    _('Transaction source "Purchase Order" not found.')
                )
            # Create a transaction booking
            transaction_booking = self.env["idil.transaction_booking"].create(
                {
                    "sales_person_id": order.sales_person_id.id,
                    "sale_order_id": order.id,  # Set the sale_order_id to the current SaleOrder's ID
                    "trx_source_id": trx_source_id.id,
                    "Sales_order_number": order.id,
                    "payment_method": "bank_transfer",  # Assuming default payment method; adjust as needed
                    "payment_status": "pending",  # Assuming initial payment status; adjust as needed
                    "trx_date": order.order_date,
                    "amount": order.order_total,
                    # Include other necessary fields
                }
            )

            total_debit = 0
            # For each order line, create a booking line entry for debit
            for line in order.order_lines:
                product = line.product_id

                bom_currency = (
                    product.bom_id.currency_id
                    if product.bom_id
                    else product.currency_id
                )

                amount_in_bom_currency = product.cost * line.quantity

                if bom_currency.name == "USD":
                    product_cost_amount = amount_in_bom_currency * self.rate
                else:
                    product_cost_amount = amount_in_bom_currency

                # product_cost_amount = product.cost * line.quantity * self.rate

                _logger.info(
                    f"Product Cost Amount: {product_cost_amount} for product {product.name}"
                )

                # Validate required accounts and currency consistency
                if line.commission_amount > 0:
                    if not product.sales_account_id:
                        raise ValidationError(
                            f"Product '{product.name}' has a commission amount but no Sales Commission Account set."
                        )
                    if product.sales_account_id.currency_id != expected_currency:
                        raise ValidationError(
                            f"Sales Commission Account for product '{product.name}' has a different currency.\n"
                            f"Expected currency: {expected_currency.name}, "
                            f"Actual currency: {product.sales_account_id.currency_id.name}."
                        )

                if line.discount_amount > 0:
                    if not product.sales_discount_id:
                        raise ValidationError(
                            f"Product '{product.name}' has a discount amount but no Sales Discount Account set."
                        )
                    if product.sales_discount_id.currency_id != expected_currency:
                        raise ValidationError(
                            f"Sales Discount Account for product '{product.name}' has a different currency.\n"
                            f"Expected currency: {expected_currency.name}, "
                            f"Actual currency: {product.sales_discount_id.currency_id.name}."
                        )

                if not product.asset_account_id:
                    raise ValidationError(
                        f"Product '{product.name}' does not have an Asset Account set."
                    )
                if product.asset_account_id.currency_id != expected_currency:
                    raise ValidationError(
                        f"Asset Account for product '{product.name}' has a different currency.\n"
                        f"Expected currency: {expected_currency.name}, "
                        f"Actual currency: {product.asset_account_id.currency_id.name}."
                    )

                if not product.income_account_id:
                    raise ValidationError(
                        f"Product '{product.name}' does not have an Income Account set."
                    )
                if product.income_account_id.currency_id != expected_currency:
                    raise ValidationError(
                        f"Income Account for product '{product.name}' has a different currency.\n"
                        f"Expected currency: {expected_currency.name}, "
                        f"Actual currency: {product.income_account_id.currency_id.name}."
                    )
                # ------------------------------------------------------------------------------------------------------
                # Credit entry Expanses inventory of COGS account for the product
                self.env["idil.transaction_bookingline"].create(
                    {
                        "transaction_booking_id": transaction_booking.id,
                        "description": f"Sales Order -- Expanses COGS account for - {product.name}",
                        "product_id": product.id,
                        "account_number": product.account_cogs_id.id,
                        "transaction_type": "dr",
                        "dr_amount": product_cost_amount,
                        "cr_amount": 0,
                        "transaction_date": order.order_date,
                        # Include other necessary fields
                    }
                )
                # Credit entry asset inventory account of the product
                self.env["idil.transaction_bookingline"].create(
                    {
                        "transaction_booking_id": transaction_booking.id,
                        "description": f"Sales Inventory account for - {product.name}",
                        "product_id": product.id,
                        "account_number": product.asset_account_id.id,
                        "transaction_type": "cr",
                        "dr_amount": 0,
                        "cr_amount": product_cost_amount,
                        "transaction_date": order.order_date,
                        # Include other necessary fields
                    }
                )
                # ------------------------------------------------------------------------------------------------------
                # Debit entry for the order line amount Sales Account Receivable
                self.env["idil.transaction_bookingline"].create(
                    {
                        "transaction_booking_id": transaction_booking.id,
                        "description": f"Sale of {product.name}",
                        "product_id": product.id,
                        "account_number": order.sales_person_id.account_receivable_id.id,
                        "transaction_type": "dr",  # Debit transaction
                        "dr_amount": line.subtotal,
                        "cr_amount": 0,
                        "transaction_date": order.order_date,
                        # Include other necessary fields
                    }
                )
                total_debit += line.subtotal

                # Credit entry using the product's income account
                self.env["idil.transaction_bookingline"].create(
                    {
                        "transaction_booking_id": transaction_booking.id,
                        "description": f"Sales Revenue - {product.name}",
                        "product_id": product.id,
                        "account_number": product.income_account_id.id,
                        "transaction_type": "cr",
                        "dr_amount": 0,
                        "cr_amount": (
                            line.subtotal
                            + line.commission_amount
                            + line.discount_amount
                        ),
                        "transaction_date": order.order_date,
                        # Include other necessary fields
                    }
                )

                # Debit entry for commission expenses
                if product.is_sales_commissionable and line.commission_amount > 0:
                    self.env["idil.transaction_bookingline"].create(
                        {
                            "transaction_booking_id": transaction_booking.id,
                            "description": f"Commission Expense - {product.name}",
                            "product_id": product.id,
                            "account_number": product.sales_account_id.id,
                            "transaction_type": "dr",  # Debit transaction for commission expense
                            "dr_amount": line.commission_amount,
                            "cr_amount": 0,
                            "transaction_date": order.order_date,
                            # Include other necessary fields
                        }
                    )

                # Debit entry for discount expenses
                if line.discount_amount > 0:
                    self.env["idil.transaction_bookingline"].create(
                        {
                            "transaction_booking_id": transaction_booking.id,
                            "description": f"Discount Expense - {product.name}",
                            "product_id": product.id,
                            "account_number": product.sales_discount_id.id,
                            "transaction_type": "dr",  # Debit transaction for discount expense
                            "dr_amount": line.discount_amount,
                            "cr_amount": 0,
                            "transaction_date": order.order_date,
                            # Include other necessary fields
                        }
                    )

    def write(self, vals):
        for order in self:
            # Check for Sales Receipt
            receipts = self.env["idil.sales.receipt"].search(
                [("sales_order_id", "=", order.id), ("paid_amount", ">", 0)]
            )
            if receipts:
                receipt_details = "\n".join(
                    [
                        f"- Receipt Date: {format_datetime(self.env, r.receipt_date)}, "
                        f"Amount Paid: {r.paid_amount:.2f}, Due: {r.due_amount:.2f}, Remaining: {r.remaining_amount:.2f}"
                        for r in receipts
                    ]
                )
                raise UserError(
                    f"Cannot edit this Sales Order because it has linked Receipts:\n{receipt_details}"
                )

            # Check for Sale Return
            returns = self.env["idil.sale.return"].search(
                [("sale_order_id", "=", order.id)]
            )
            if returns:
                return_details = "\n".join(
                    [
                        f"- Return Date: {format_datetime(self.env, r.return_date)}, State: {r.state}"
                        for r in returns
                    ]
                )
                raise UserError(
                    f"Cannot edit this Sales Order because it has linked Sale Returns:\n{return_details}"
                )

        for order in self:
            # Capture old quantities for comparison
            old_quantities = {line.id: line.quantity for line in order.order_lines}

        # Proceed with the standard write
        for order in self:

            for line in order.order_lines:
                product = line.product_id
                old_qty = old_quantities.get(line.id, 0.0)
                new_qty = line.quantity
                qty_diff = new_qty - old_qty
                # 🔒 Step 3: Validate return quantity before updating

                # Handle stock adjustment
                if qty_diff > 0:
                    # Increase in quantity, check availability
                    if product.stock_quantity < qty_diff:
                        raise ValidationError(
                            f"Insufficient stock for product '{product.name}'. "
                            f"Available: {product.stock_quantity}, Needed: {qty_diff}"
                        )
                    product.stock_quantity -= qty_diff
                elif qty_diff < 0:
                    # Decrease in quantity, return stock
                    product.stock_quantity += abs(qty_diff)

            res = super(SaleOrder, self).write(vals)
            # Remove old movements
            movements = self.env["idil.product.movement"].search(
                [
                    ("sale_order_id", "=", order.id),
                ]
            )

            movements.unlink()

            # Recreate product movements
            for line in order.order_lines:
                self.env["idil.product.movement"].create(
                    {
                        "sale_order_id": order.id,
                        "product_id": line.product_id.id,
                        "movement_type": "out",
                        "quantity": line.quantity * -1,
                        "date": order.order_date,
                        "source_document": order.name,
                        "sales_person_id": order.sales_person_id.id,
                    }
                )

            # Remove and recreate booking and booking lines
            bookings = self.env["idil.transaction_booking"].search(
                [("sale_order_id", "=", order.id)]
            )
            for booking in bookings:
                booking.booking_lines.unlink()
                booking.unlink()

            order.book_accounting_entry()
            # ✅ Adjust corresponding receipt if exists
            receipt = self.env["idil.sales.receipt"].search(
                [("sales_order_id", "=", order.id)], limit=1
            )
            if receipt:
                paid_amount = receipt.paid_amount or 0.0
                new_due = order.order_total
                receipt.write(
                    {
                        "due_amount": new_due,
                        "remaining_amount": new_due - paid_amount,
                    }
                )

        return res

    def unlink(self):
        # Gather the sale order IDs before deleting
        order_ids = self.ids
        for order in self:
            # Check for Sales Receipt
            receipts = self.env["idil.sales.receipt"].search(
                [("sales_order_id", "=", order.id)]
            )

            # Filter only receipts with non-zero paid amount
            receipts_with_payment = receipts.filtered(lambda r: r.paid_amount > 0)

            if receipts_with_payment:
                receipt_details = "\n".join(
                    [
                        f"- Receipt Date: {format_datetime(self.env, r.receipt_date)}, "
                        f"Amount Paid: {r.paid_amount:.2f}, Due: {r.due_amount:.2f}, Remaining: {r.remaining_amount:.2f}"
                        for r in receipts_with_payment
                    ]
                )
                raise UserError(
                    f"Cannot edit this Sales Order because it has Receipts with payment:\n{receipt_details}"
                )

            # Check for Sale Return
            returns = self.env["idil.sale.return"].search(
                [("sale_order_id", "=", order.id)]
            )
            if returns:
                return_details = "\n".join(
                    [
                        f"- Return Date: {format_datetime(self.env, r.return_date)}, State: {r.state}"
                        for r in returns
                    ]
                )
                raise UserError(
                    f"Cannot edit this Sales Order because it has linked Sale Returns:\n{return_details}"
                )

        for order in self:
            # Revert stock, delete related product movements, bookings, etc.
            for line in order.order_lines:
                product = line.product_id
                product.stock_quantity += line.quantity

            movements = self.env["idil.product.movement"].search(
                [("sale_order_id", "=", order.id)]
            )
            movements.unlink()

            bookings = self.env["idil.transaction_booking"].search(
                [("sale_order_id", "=", order.id)]
            )
            for booking in bookings:
                booking.booking_lines.unlink()
                booking.unlink()

            self.env["idil.salesperson.transaction"].search(
                [("order_id", "=", order.id)]
            ).unlink()
            # Do NOT delete receipt here!

        # Delete the sale order(s) and all their direct dependencies
        res = super(SaleOrder, self).unlink()

        # Now delete related sales receipts for these orders (if any)
        self.env["idil.sales.receipt"].search(
            [("sales_order_id", "=", order.id)]
        ).unlink()

        # order.salesperson_order_id.state = "draft"

        return res


class SaleOrderLine(models.Model):
    _name = "idil.sale.order.line"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _description = "Sale Order Line"
    _order = "id desc"

    order_id = fields.Many2one("idil.sale.order", string="Sale Order")
    product_id = fields.Many2one("my_product.product", string="Product")
    quantity_Demand = fields.Float(string="Demand", default=1.0)
    quantity = fields.Float(string="Quantity Used", required=True, tracking=True)
    # New computed field for the difference between Demand and Quantity Used
    quantity_diff = fields.Float(
        string="Quantity Difference", compute="_compute_quantity_diff", store=True
    )

    # Editable price unit with dynamic default
    price_unit = fields.Float(
        string="Unit Price",
        default=lambda self: self.product_id.sale_price if self.product_id else 0.0,
    )
    discount_amount = fields.Float(
        string="Discount Amount", compute="_compute_discount_amount", store=True
    )

    subtotal = fields.Float(string="Due Amount", compute="_compute_subtotal")

    # Editable and computed field for commission amount
    commission_amount = fields.Float(
        string="Commission Amount",
        compute="_compute_commission_amount",
        inverse="_set_commission_amount",
        store=True,
    )

    # New computed field for Discount amount
    discount_quantity = fields.Float(
        string="Discount Quantity", compute="_compute_discount_quantity", store=True
    )
    returned_quantity = fields.Float(
        string="Returned Quantity",
        compute="_compute_returned_quantity",
        store=False,
        readonly=True,
    )

    @api.depends("order_id", "product_id")
    def _compute_returned_quantity(self):
        for line in self:
            if line.order_id and line.product_id:
                # Get all confirmed return lines for this product and order
                return_lines = self.env["idil.sale.return.line"].search(
                    [
                        ("return_id.sale_order_id", "=", line.order_id.id),
                        ("product_id", "=", line.product_id.id),
                        ("return_id.state", "=", "confirmed"),
                    ]
                )
                line.returned_quantity = sum(return_lines.mapped("returned_quantity"))
            else:
                line.returned_quantity = 0.0

    @api.depends("quantity", "product_id.commission", "price_unit")
    def _compute_commission_amount(self):
        for line in self:
            product = line.product_id
            if product.is_sales_commissionable:
                if not product.sales_account_id:
                    raise ValidationError(
                        f"Product '{product.name}' does not have a Sales Commission Account set."
                    )
                if product.commission <= 0:
                    raise ValidationError(
                        f"Product '{product.name}' does not have a valid Commission Rate set."
                    )

                # Calculate commission amount
                line.commission_amount = (
                    (line.quantity - line.discount_quantity)
                    * product.commission
                    * line.price_unit
                )
            else:
                line.commission_amount = 0.0

    def _set_commission_amount(self):
        """Allow manual updates to commission_amount."""
        for line in self:
            # Just store the manually set value; no computation here
            pass

    @api.depends("quantity", "price_unit", "commission_amount")
    def _compute_subtotal(self):
        for line in self:
            line.subtotal = (
                (line.quantity * line.price_unit)
                - (line.discount_quantity * line.price_unit)
                - line.commission_amount
            )

    @api.depends("quantity")
    def _compute_discount_quantity(self):
        for line in self:
            line.discount_quantity = (
                (line.product_id.discount / 100) * (line.quantity)
                if line.product_id.is_quantity_discount
                else 0.0
            )

    @api.depends("discount_quantity", "price_unit")
    def _compute_discount_amount(self):
        for line in self:
            line.discount_amount = line.discount_quantity * line.price_unit

    @api.depends("quantity_Demand", "quantity")
    def _compute_quantity_diff(self):
        for record in self:
            record.quantity_diff = record.quantity_Demand - record.quantity

    @api.model
    def create(self, vals):
        record = super(SaleOrderLine, self).create(vals)

        # Create a Salesperson Transaction
        if record.order_id.sales_person_id:
            self.env["idil.salesperson.transaction"].create(
                {
                    "sales_person_id": record.order_id.sales_person_id.id,
                    "date": fields.Date.today(),
                    "order_id": record.order_id.id,
                    "transaction_type": "out",  # Assuming 'out' for sales
                    "amount": record.subtotal
                    + record.discount_amount
                    + record.commission_amount,
                    "description": f"Sales Amount of - Order Line for {record.product_id.name} (Qty: {record.quantity})",
                }
            )
            self.env["idil.salesperson.transaction"].create(
                {
                    "sales_person_id": record.order_id.sales_person_id.id,
                    "date": fields.Date.today(),
                    "order_id": record.order_id.id,
                    "transaction_type": "out",  # Assuming 'out' for sales
                    "amount": record.commission_amount * -1,
                    "description": f"Sales Commission Amount of - Order Line for  {record.product_id.name} (Qty: {record.quantity})",
                }
            )
            self.env["idil.salesperson.transaction"].create(
                {
                    "sales_person_id": record.order_id.sales_person_id.id,
                    "date": fields.Date.today(),
                    "order_id": record.order_id.id,
                    "transaction_type": "out",  # Assuming 'out' for sales
                    "amount": record.discount_amount * -1,
                    "description": f"Sales Discount Amount of - Order Line for  {record.product_id.name} (Qty: {record.quantity})",
                }
            )

        self.update_product_stock(record.product_id, record.quantity)
        return record

    def write(self, vals):
        for line in self:
            order = line.order_id
            product = line.product_id
            old_qty = line.quantity
            new_qty = vals.get("quantity", old_qty)

            # ✅ Step 1: Validate that the new quantity is not less than returned quantity
            if new_qty < old_qty:
                confirmed_returns = self.env["idil.sale.return.line"].search(
                    [
                        ("return_id.sale_order_id", "=", order.id),
                        ("product_id", "=", product.id),
                        ("return_id.state", "=", "confirmed"),
                    ]
                )
                total_returned = sum(confirmed_returns.mapped("returned_quantity"))

                if new_qty < total_returned:
                    raise ValidationError(
                        f"You cannot reduce quantity of '{product.name}' to {new_qty:.2f} "
                        f"because {total_returned:.2f} has already been returned."
                    )

            # Step 1: Update stock if quantity is changing
            if "quantity" in vals:
                quantity_diff = vals["quantity"] - line.quantity
                self.update_product_stock(line.product_id, quantity_diff)

        # Step 2: Proceed with the actual write
        res = super(SaleOrderLine, self).write(vals)

        for line in self:
            order = line.order_id

            # Step 3: Delete old salesperson transactions for this order

            self.env["idil.salesperson.transaction"].search(
                [("order_id", "=", order.id), ("sale_return_id", "=", False)]
            ).unlink()

            # Step 4: Recreate transactions for all lines in the order
            for updated_line in order.order_lines:
                self.env["idil.salesperson.transaction"].create(
                    {
                        "sales_person_id": order.sales_person_id.id,
                        "date": fields.Date.today(),
                        "order_id": order.id,
                        "transaction_type": "out",
                        "amount": updated_line.subtotal
                        + updated_line.discount_amount
                        + updated_line.commission_amount,
                        "description": f"Sales Amount of - Order Line for {updated_line.product_id.name} (Qty: {updated_line.quantity})",
                    }
                )

                self.env["idil.salesperson.transaction"].create(
                    {
                        "sales_person_id": order.sales_person_id.id,
                        "date": fields.Date.today(),
                        "order_id": order.id,
                        "transaction_type": "in",
                        "amount": updated_line.commission_amount,
                        "description": f"Sales Commission Amount of - Order Line for {updated_line.product_id.name} (Qty: {updated_line.quantity})",
                    }
                )

                self.env["idil.salesperson.transaction"].create(
                    {
                        "sales_person_id": order.sales_person_id.id,
                        "date": fields.Date.today(),
                        "order_id": order.id,
                        "transaction_type": "in",
                        "amount": updated_line.discount_amount,
                        "description": f"Sales Discount Amount of - Order Line for {updated_line.product_id.name} (Qty: {updated_line.quantity})",
                    }
                )

        return res

    @staticmethod
    def update_product_stock(product, quantity_diff):
        """Static Method: Update product stock quantity based on the sale order line quantity change."""
        new_stock_quantity = product.stock_quantity - quantity_diff
        if new_stock_quantity < 0:
            raise ValidationError(
                "Insufficient stock for product '{}'. The available stock quantity is {:.2f}, "
                "but the required quantity is {:.2f}.".format(
                    product.name, product.stock_quantity, abs(quantity_diff)
                )
            )
        product.stock_quantity = new_stock_quantity
