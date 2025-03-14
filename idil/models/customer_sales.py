import re

from odoo import models, fields, api
from odoo.exceptions import UserError, ValidationError
from odoo.tools.safe_eval import datetime
import logging

_logger = logging.getLogger(__name__)


class CustomerSaleOrder(models.Model):
    _name = "idil.customer.sale.order"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _description = "CustomerSale Order"

    name = fields.Char(string="Sales Reference", tracking=True)

    customer_id = fields.Many2one(
        "idil.customer.registration", string="Customer", required=True
    )

    order_date = fields.Datetime(string="Order Date", default=fields.Datetime.now)
    order_lines = fields.One2many(
        "idil.customer.sale.order.line", "order_id", string="Order Lines"
    )
    order_total = fields.Float(
        string="Order Total", compute="_compute_order_total", store=True
    )
    state = fields.Selection(
        [("draft", "Draft"), ("confirmed", "Confirmed"), ("cancel", "Cancelled")],
        default="confirmed",
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
    payment_method = fields.Selection(
        [
            ("cash", "Cash"),
            ("ar", "A/R"),
        ],
        string="Payment Method",
    )
    # One2many field for multiple payment methods
    payment_lines = fields.One2many(
        "idil.customer.sale.payment",
        "order_id",
        string="Payments",
    )

    total_paid = fields.Float(
        string="Total Paid", compute="_compute_total_paid", store=True
    )

    balance_due = fields.Float(
        string="Balance Due", compute="_compute_balance_due", store=True
    )

    @api.depends("payment_lines.amount")
    def _compute_total_paid(self):
        for order in self:
            order.total_paid = sum(order.payment_lines.mapped("amount"))

    @api.depends("order_total", "total_paid")
    def _compute_balance_due(self):
        for order in self:
            order.balance_due = order.order_total - order.total_paid

    @api.constrains("total_paid", "order_total")
    def _check_payment_balance(self):
        for order in self:
            if order.total_paid > order.order_total:
                raise ValidationError(
                    "The total paid amount cannot exceed the order total."
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

    @api.model
    def create(self, vals):
        # Step 1: Check if customer_id is provided in vals
        if "customer_id" in vals:

            # Set order reference if not provided
            if "name" not in vals or not vals["name"]:
                vals["name"] = self._generate_order_reference(vals)

        # Proceed with creating the SaleOrder with the updated vals
        new_order = super(CustomerSaleOrder, self).create(vals)
        # Create a corresponding SalesReceipt
        # self.env["idil.sales.receipt"].create(
        #     {
        #         "sales_order_id": new_order.id,
        #         "due_amount": new_order.order_total,
        #         "paid_amount": 0,
        #         "remaining_amount": new_order.order_total,
        #         "salesperson_id": new_order.customer_id.id,
        #     }
        # )

        for line in new_order.order_lines:
            self.env["idil.product.movement"].create(
                {
                    "product_id": line.product_id.id,
                    "movement_type": "out",
                    "quantity": line.quantity * -1,
                    "date": fields.Datetime.now(),
                    "source_document": new_order.name,
                    "customer_id": new_order.customer_id.id,
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

    def book_accounting_entry(self):
        """
        Create a transaction booking for the given SaleOrder, with entries for:

        1. Debiting the Asset Inventory account for each order line's product
        2. Crediting the COGS account for each order line's product
        3. Debiting the Sales Account Receivable for each order line's amount
        4. Crediting the product's income account for each order line's amount
        """
        for order in self:
            if not order.customer_id.account_receivable_id:
                raise ValidationError(
                    "The Customer does not have a receivable account."
                )
            if order.rate <= 0:
                raise ValidationError(
                    "Please insert a valid exchange rate greater than 0."
                )
            if not order.order_lines:
                raise ValidationError(
                    "You must insert at least one product to proceed with the sale."
                )
            if order.payment_method == "cash":
                if not order.customer_id.account_cash_id:
                    raise ValidationError("The Customer does not have a cash account.")
                account_to_use = order.customer_id.account_cash_id
            else:
                account_to_use = order.customer_id.account_receivable_id

            # Define the expected currency from the salesperson's account receivable
            expected_currency = order.customer_id.account_receivable_id.currency_id

            # Create a transaction booking
            transaction_booking = self.env["idil.transaction_booking"].create(
                {
                    "customer_id": order.customer_id.id,
                    "cusotmer_sale_order_id": order.id,  # Set the sale_order_id to the current SaleOrder's ID
                    "trx_source_id": 3,
                    "Sales_order_number": order.id,
                    "payment_method": "bank_transfer",  # Assuming default payment method; adjust as needed
                    "payment_status": "pending",  # Assuming initial payment status; adjust as needed
                    "trx_date": fields.Date.context_today(self),
                    "amount": order.order_total,
                    # Include other necessary fields
                }
            )

            total_debit = 0
            # For each order line, create a booking line entry for debit
            for line in order.order_lines:
                product = line.product_id
                product_cost_amount = product.cost * line.quantity * self.rate
                _logger.info(
                    f"Product Cost Amount: {product_cost_amount} for product {product.name}"
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
                        # Use the COGS Account_number
                        "transaction_type": "dr",
                        "dr_amount": product_cost_amount,
                        "cr_amount": 0,
                        "transaction_date": fields.Date.context_today(self),
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
                        "transaction_date": fields.Date.context_today(self),
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
                        "account_number": account_to_use.id,
                        "transaction_type": "dr",  # Debit transaction
                        "dr_amount": line.subtotal,
                        "cr_amount": 0,
                        "transaction_date": fields.Date.context_today(self),
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
                        "cr_amount": (line.subtotal),
                        "transaction_date": fields.Date.context_today(self),
                        # Include other necessary fields
                    }
                )

    def write(self, vals):
        # Call the original write method
        res = super(CustomerSaleOrder, self).write(vals)
        # After the write operation, update booking entries if necessary
        self.update_booking_entry()
        return res

    def update_booking_entry(self):
        # Find the related TransactionBooking record
        booking = self.env["idil.transaction_booking"].search(
            [("sale_order_id", "=", self.id)], limit=1
        )
        if booking:
            booking.amount = self.order_total
            booking.update_related_booking_lines()

    def unlink(self):

        for order in self:
            # Adjust product stock quantities for each order line before deletion
            for line in order.order_lines:
                if line.product_id:
                    # Assuming SaleOrderLine has a method 'update_product_stock' to adjust stock quantities
                    CustomerSaleOrder.update_product_stock(
                        line.product_id, -line.quantity
                    )

        # Revert the state of related SalespersonOrder(s) back to 'draft'

        # Proceed to delete the SaleOrder(s) after adjustments
        return super(CustomerSaleOrder, self).unlink()


class CustomerSaleOrderLine(models.Model):
    _name = "idil.customer.sale.order.line"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _description = "CustomerSale Order Line"

    order_id = fields.Many2one("idil.customer.sale.order", string="Sale Order")
    product_id = fields.Many2one("my_product.product", string="Product")
    quantity_Demand = fields.Float(string="Demand", default=1.0)
    quantity = fields.Float(string="Quantity Used", required=True, tracking=True)
    cost_price = fields.Float(
        string="Cost Price", store=True, tracking=True
    )  # Save cost to DB

    # Editable price unit with dynamic default
    price_unit = fields.Float(
        string="Unit Price",
        default=lambda self: self.product_id.sale_price if self.product_id else 0.0,
    )
    cogs = fields.Float(string="COGS", compute="_compute_cogs")

    subtotal = fields.Float(string="Due Amount", compute="_compute_subtotal")
    profit = fields.Float(string="Profit Amount", compute="_compute_profit")

    @api.depends("quantity", "price_unit")
    def _compute_subtotal(self):
        for line in self:
            line.subtotal = line.quantity * line.price_unit

    @api.depends("cogs", "subtotal")
    def _compute_profit(self):
        for line in self:
            line.profit = line.subtotal - line.cogs

    @api.depends("quantity", "cost_price", "order_id.rate")
    def _compute_cogs(self):
        """Computes the Cost of Goods Sold (COGS) considering the exchange rate"""
        for line in self:
            if line.order_id:
                line.cogs = line.quantity * line.cost_price
            else:
                line.cogs = (
                    line.quantity * line.cost_price
                )  # Fallback if no rate is found

    @api.model
    def create(self, vals):
        record = super(CustomerSaleOrderLine, self).create(vals)

        # Create a Salesperson Transaction
        # if record.order_id.customer_id:
        #     self.env["idil.salesperson.transaction"].create(
        #         {
        #             "customer_id": record.order_id.customer_id.id,
        #             "date": fields.Date.today(),
        #             "order_id": record.order_id.id,
        #             "transaction_type": "out",  # Assuming 'out' for sales
        #             "amount": record.subtotal,
        #             "description": f"Sales Amount of - Order Line for {record.product_id.name} (Qty: {record.quantity})",
        #         }
        #     )

        self.update_product_stock(record.product_id, record.quantity)
        return record

    @staticmethod
    def update_product_stock(product, quantity):
        """Static Method: Update product stock quantity based on the sale order line quantity change."""
        new_stock_quantity = product.stock_quantity - quantity
        if new_stock_quantity < 0:
            raise ValidationError(
                "Insufficient stock for product '{}'. The available stock quantity is {:.2f}, "
                "but the required quantity is {:.2f}.".format(
                    product.name, product.stock_quantity, abs(quantity)
                )
            )
        product.stock_quantity = new_stock_quantity

    @api.constrains("quantity", "price_unit")
    def _check_quantity_and_price(self):
        """Ensure that quantity and unit price are greater than zero."""
        for line in self:
            if line.quantity <= 0:
                raise ValidationError(
                    f"Product '{line.product_id.name}' must have a quantity greater than zero."
                )
            if line.price_unit <= 0:
                raise ValidationError(
                    f"Product '{line.product_id.name}' must have a unit price greater than zero."
                )

    @api.onchange("product_id", "order_id.rate")
    def _onchange_product_id(self):
        """When product_id changes, update the cost price"""
        if self.product_id:
            self.cost_price = (
                self.product_id.cost * self.order_id.rate
            )  # Fetch cost price from product
            self.price_unit = (
                self.product_id.sale_price
            )  # Set sale price as default unit price
        else:
            self.cost_price = 0.0
            self.price_unit = 0.0


class CustomerSalePayment(models.Model):
    _name = "idil.customer.sale.payment"
    _description = "Sale Order Payment"

    order_id = fields.Many2one(
        "idil.customer.sale.order", string="Sale Order", required=True
    )

    payment_method = fields.Selection(
        [("cash", "Cash"), ("ar", "A/R")],
        string="Payment Method",
        required=True,
    )

    account_id = fields.Many2one("account.account", string="Account", required=True)

    amount = fields.Float(string="Amount", required=True)
