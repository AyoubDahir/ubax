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
    _order = "id desc"

    name = fields.Char(string="Sales Reference", tracking=True)

    customer_id = fields.Many2one(
        "idil.customer.registration", string="Customer", required=True
    )
    # Add the field to link to the Customer Place Order

    customer_place_order_id = fields.Many2one(
        "idil.customer.place.order",
        string="Customer Place Order",
        domain="[('customer_id', '=', customer_id), ('state', '=', 'draft')]",
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
            ("bank_transfer", "Bank"),
            ("receivable", "Account Receivable"),
        ],
        string="Payment Method",
    )
    account_number = fields.Many2one(
        "idil.chart.account",
        string="Account Number",
        required=True,
        domain="[('account_type', '=', payment_method)]",
    )

    # One2many field for multiple payment methods
    payment_lines = fields.One2many(
        "idil.customer.sale.payment",
        "order_id",
        string="Payments",
    )

    total_paid = fields.Float(
        string="Total Paid", compute="_compute_total_paid", store=False
    )

    balance_due = fields.Float(
        string="Balance Due", compute="_compute_balance_due", store=False
    )
    customer_opening_balance_id = fields.Many2one(
        "idil.customer.opening.balance.line",
        string="Opening Balance",
        ondelete="cascade",
    )
    total_return_amount = fields.Float(
        string="Total Returned",
        compute="_compute_total_return_amount",
        store=False,
    )
    net_balance = fields.Float(
        string="Net Balance",
        compute="_compute_net_balance",
        store=False,
    )
    total_cost_price = fields.Float(
        string="Total Cost Price",
        compute="_compute_total_cost_price",
        store=False,
        digits=(16, 6),
        readonly=True,
        tracking=True,
    )

    # Automatically populate order lines from the place order when customer_place_order_id is selected
    @api.onchange("customer_place_order_id")
    def _onchange_customer_place_order(self):
        """Automatically populate order lines based on the selected Customer Place Order."""
        if self.customer_place_order_id:
            # Clear existing order lines first
            self.order_lines = [(5, 0, 0)]  # Remove all existing lines

            # Copy order lines from the selected CustomerPlaceOrder
            order_line_vals = []
            for line in self.customer_place_order_id.order_lines:
                order_line_vals.append(
                    (
                        0,
                        0,
                        {
                            "product_id": line.product_id.id,
                            "quantity": line.quantity,
                            "price_unit": line.product_id.sale_price,  # Use product's sale price
                        },
                    )
                )
            # Assign the lines to the current order
            self.order_lines = order_line_vals
            _logger.info(
                f"Order lines populated for Customer {self.customer_id.name} from Place Order {self.customer_place_order_id.name}."
            )

    @api.depends("order_lines", "order_lines.product_id", "order_lines.quantity")
    def _compute_total_cost_price(self):
        for order in self:
            total = 0.0
            for line in order.order_lines:
                product = line.product_id
                qty = line.quantity
                if product and qty:
                    # 1. If product has a BOM
                    if product.bom_id:
                        bom_currency = product.bom_id.currency_id.name
                        if bom_currency == "SL":
                            total += (product.cost * qty) / order.rate
                        else:
                            total += product.cost * qty
                    else:
                        # 2. If no BOM, assume cost is SL and convert
                        total += (product.cost * qty) / order.rate
            order.total_cost_price = total

    @api.depends(
        "balance_due",
        "total_return_amount",
        "payment_method",
        "order_total",
        "total_paid",
    )
    def _compute_net_balance(self):
        for order in self:
            base_balance = order.order_total - order.total_paid

            # If payment is cash or bank, returns don't affect due — full payment already done
            if order.payment_method in ("cash", "bank_transfer"):
                order.net_balance = base_balance
            else:
                order.net_balance = base_balance - order.total_return_amount

    @api.depends("order_lines", "order_lines.product_id")  # triggers on change
    def _compute_total_return_amount(self):
        for order in self:
            return_lines = self.env["idil.customer.sale.return.line"].search(
                [
                    ("sale_order_line_id.order_id", "=", order.id),
                    ("return_id.state", "=", "confirmed"),
                ]
            )
            order.total_return_amount = sum(return_lines.mapped("total_amount"))

    @api.onchange("payment_method", "customer_id")
    def _onchange_payment_method_account(self):
        """Auto-fill account_number based on payment method."""
        for order in self:
            if order.payment_method == "receivable" and order.customer_id:
                order.account_number = order.customer_id.account_receivable_id
            elif order.payment_method in ["cash", "bank_transfer"]:
                order.account_number = False  # Clear it for cash, let user choose

    @api.depends("payment_lines.amount")
    def _compute_total_paid(self):
        for order in self:
            order.total_paid = sum(order.payment_lines.mapped("amount"))

    @api.depends("order_total", "total_paid", "payment_method")
    def _compute_balance_due(self):
        for order in self:
            if order.payment_method in ["cash", "bank_transfer"]:
                order.balance_due = 0.0
            else:
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
        try:
            with self.env.cr.savepoint():
                # Step 1: Check if customer_id is provided in vals
                if "customer_id" in vals:

                    # Set order reference if not provided
                    if "name" not in vals or not vals["name"]:
                        vals["name"] = self._generate_order_reference(vals)

                # Proceed with creating the SaleOrder with the updated vals
                new_order = super(CustomerSaleOrder, self).create(vals)
                # ✅ confirm the linked place order, if any

                # Step 3: Create product movements for each order line
                for line in new_order.order_lines:
                    self.env["idil.product.movement"].create(
                        {
                            "product_id": line.product_id.id,
                            "movement_type": "out",
                            "quantity": line.quantity * -1,
                            "date": new_order.order_date,
                            "source_document": new_order.name,
                            "customer_id": new_order.customer_id.id,
                        }
                    )

                # Step 4: Book accounting entries for the new order
                new_order.book_accounting_entry()
                # after: new_order.book_accounting_entry()

                # ✅ If this sale order came from a Customer Place Order, confirm it and link back
                if new_order.customer_place_order_id:
                    new_order.customer_place_order_id.write(
                        {
                            "state": "confirmed",
                            "sale_order_id": new_order.id,  # if you added the field above
                        }
                    )

                return new_order
        except Exception as e:
            _logger.error(f"Create transaction failed: {str(e)}")
            raise ValidationError(f"Transaction failed: {str(e)}")

    # inside class CustomerSaleOrder(models.Model):

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
        try:
            with self.env.cr.savepoint():
                for order in self:
                    if not order.customer_id.account_receivable_id:
                        raise ValidationError(
                            "The Customer does not have a receivable account."
                        )
                    if order.rate <= 0:
                        raise ValidationError(
                            "Please insert a valid exchange rate greater than 0."
                        )
                    # Only check order lines if not from opening balance
                    if not order.customer_opening_balance_id and not order.order_lines:
                        raise ValidationError(
                            "You must insert at least one product to proceed with the sale."
                        )
                    # If this order is for opening balance, skip accounting booking: opening balance does its own accounting
                    if order.customer_opening_balance_id:
                        return

                    if order.payment_method in ["cash", "bank_transfer"]:
                        account_to_use = self.account_number
                    else:
                        account_to_use = order.customer_id.account_receivable_id

                    # Define the expected currency from the salesperson's account receivable
                    expected_currency = (
                        order.customer_id.account_receivable_id.currency_id
                    )

                    # Search for transaction source ID using "Receipt"
                    trx_source = self.env["idil.transaction.source"].search(
                        [("name", "=", "Customer Sales Order")], limit=1
                    )
                    if not trx_source:
                        raise UserError(
                            "Transaction source 'Customer Sales Order' not found."
                        )

                    # Create a transaction booking
                    transaction_booking = self.env["idil.transaction_booking"].create(
                        {
                            "customer_id": order.customer_id.id,
                            "cusotmer_sale_order_id": order.id,  # Set the sale_order_id to the current SaleOrder's ID
                            "trx_source_id": trx_source.id,
                            "reffno": order.name,  # Use the Sale Order name as reference
                            "Sales_order_number": order.id,
                            "payment_method": "bank_transfer",  # Assuming default payment method; adjust as needed
                            "payment_status": "pending",  # Assuming initial payment status; adjust as needed
                            "trx_date": fields.Date.context_today(self),
                            "amount": order.order_total,
                            # Include other necessary fields
                        }
                    )
                    # ✅ Only create receipt if payment method is NOT cash
                    if order.payment_method not in ["cash", "bank_transfer"]:
                        self.env["idil.sales.receipt"].create(
                            {
                                "cusotmer_sale_order_id": order.id,
                                "due_amount": order.order_total,
                                "paid_amount": 0,
                                "remaining_amount": order.order_total,
                                "customer_id": order.customer_id.id,
                            }
                        )

                    if order.payment_method in ["cash", "bank_transfer"]:
                        self.env["idil.customer.sale.payment"].create(
                            {
                                "order_id": order.id,
                                "customer_id": order.customer_id.id,
                                "payment_method": "cash",  # or use dynamic logic to determine the method
                                "account_id": order.account_number.id,
                                "amount": order.order_total,
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

                        # product_cost_amount = product.cost * line.quantity
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
                        # Validate that the product has a COGS account
                        if not product.account_cogs_id:
                            raise ValidationError(
                                f"No COGS (Cost of Goods Sold) account assigned for the product '{product.name}'.\n"
                                f"Please configure 'COGS Account' in the product settings before continuing."
                            )
                        # === Validate all required accounts ===
                        if not product.asset_account_id:
                            raise ValidationError(
                                f"Product '{product.name}' has no Asset account."
                            )
                        if not product.income_account_id:
                            raise ValidationError(
                                f"Product '{product.name}' has no Income account."
                            )

                        # ✅ Validate currencies if payment is cash
                        if order.payment_method in ["cash", "bank_transfer"]:
                            cash_currency = order.account_number.currency_id
                            involved_accounts = {
                                "COGS": product.account_cogs_id,
                                "Asset": product.asset_account_id,
                                "Income": product.income_account_id,
                            }

                            for acc_name, acc in involved_accounts.items():
                                if acc.currency_id and acc.currency_id != cash_currency:
                                    raise ValidationError(
                                        f"Currency mismatch for product '{product.name}'.\n"
                                        f"{acc_name} account currency is '{acc.currency_id.name}', but Cash account currency is '{cash_currency.name}'.\n"
                                        f"All accounts must match Cash account currency when payment method is Cash."
                                    )

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
                        # After booking the entries, confirm the place order

        except Exception as e:
            _logger.error(f"transaction failed: {str(e)}")
            raise ValidationError(f"Transaction failed: {str(e)}")

    def write(self, vals):
        try:
            # Start transaction
            with self.env.cr.savepoint():
                for order in self:

                    # 1.  Prevent changing payment_method from receivable → cash
                    # ------------------------------------------------------------------
                    if "payment_method" in vals and vals["payment_method"] in [
                        "cash",
                        "bank_transfer",
                    ]:
                        for order in self:
                            if order.payment_method == "receivable":
                                raise ValidationError(
                                    "You cannot switch the payment method from "
                                    "'Account Receivable' to 'Cash or bank'.\n"
                                    "Receivable booking lines already exist for this order."
                                )
                    if (
                        "payment_method" in vals
                        and vals["payment_method"] == "receivable"
                    ):
                        for order in self:
                            if order.payment_method in ["cash", "bank_transfer"]:
                                raise ValidationError(
                                    "You cannot switch the payment method from "
                                    "'Cash or bank' to 'Account Receivable'.\n"
                                    "Cash booking lines already exist for this order."
                                )

                    # Loop through the lines in the database before they are updated
                    for line in order.order_lines:
                        if not line.product_id:
                            continue

                        # Fetch original (pre-update) record from DB
                        original_line = self.env[
                            "idil.customer.sale.order.line"
                        ].browse(line.id)
                        old_qty = original_line.quantity

                        # Get new quantity from vals if being changed, else use current
                        new_qty = line.quantity
                        if "order_lines" in vals:
                            for command in vals["order_lines"]:
                                if command[0] == 1 and command[1] == line.id:
                                    if "quantity" in command[2]:
                                        new_qty = command[2]["quantity"]

                        product = line.product_id

                        # Check if increase
                        if new_qty > old_qty:
                            diff = new_qty - old_qty
                            if product.stock_quantity < diff:
                                raise ValidationError(
                                    f"Not enough stock for product '{product.name}'.\n"
                                    f"Available: {product.stock_quantity}, Required additional: {diff}"
                                )
                            # product.stock_quantity -= diff

                        # If decrease
                        elif new_qty < old_qty:
                            diff = old_qty - new_qty
                            # product.stock_quantity += diff

                # === Perform the write ===
                res = super(CustomerSaleOrder, self).write(vals)

                # === Update related records ===
                for order in self:
                    # -- Update Product Movements --
                    movements = self.env["idil.product.movement"].search(
                        [("source_document", "=", order.name)]
                    )
                    for movement in movements:
                        matching_line = order.order_lines.filtered(
                            lambda l: l.product_id.id == movement.product_id.id
                        )
                        if matching_line:
                            movement.write(
                                {
                                    "quantity": matching_line[0].quantity * -1,
                                    "date": order.order_date,
                                    "customer_id": order.customer_id.id,
                                }
                            )

                    # -- Update Sales Receipt --
                    receipt = self.env["idil.sales.receipt"].search(
                        [("cusotmer_sale_order_id", "=", order.id)], limit=1
                    )
                    if receipt:
                        receipt.write(
                            {
                                "due_amount": order.order_total,
                                "paid_amount": order.total_paid,
                                "remaining_amount": order.balance_due,
                                "customer_id": order.customer_id.id,
                            }
                        )

                    # -- Update Transaction Booking & Booking Lines --
                    booking = self.env["idil.transaction_booking"].search(
                        [("cusotmer_sale_order_id", "=", order.id)], limit=1
                    )
                    if booking:
                        booking.write(
                            {
                                "trx_date": fields.Date.context_today(self),
                                "amount": order.order_total,
                                "customer_id": order.customer_id.id,
                                "payment_method": order.payment_method
                                or "bank_transfer",
                                "payment_status": "pending",
                            }
                        )

                        lines = self.env["idil.transaction_bookingline"].search(
                            [("transaction_booking_id", "=", booking.id)]
                        )
                        for line in lines:
                            matching_order_line = order.order_lines.filtered(
                                lambda l: l.product_id.id == line.product_id.id
                            )
                            if not matching_order_line:
                                continue

                            order_line = matching_order_line[0]
                            product = order_line.product_id

                            bom_currency = (
                                product.bom_id.currency_id
                                if product.bom_id
                                else product.currency_id
                            )

                            amount_in_bom_currency = product.cost * order_line.quantity

                            if bom_currency.name == "USD":
                                product_cost_amount = amount_in_bom_currency * self.rate
                            else:
                                product_cost_amount = amount_in_bom_currency

                            _logger.info(
                                f"Product Cost Amount: {product_cost_amount} for product {product.name}"
                            )
                            updated_values = {}

                            # COGS (DR)
                            if (
                                line.transaction_type == "dr"
                                and line.account_number.id == product.account_cogs_id.id
                            ):
                                updated_values["dr_amount"] = product_cost_amount
                                updated_values["cr_amount"] = 0

                            # Asset Inventory (CR)
                            elif (
                                line.transaction_type == "cr"
                                and line.account_number.id
                                == product.asset_account_id.id
                            ):
                                updated_values["cr_amount"] = product_cost_amount
                                updated_values["dr_amount"] = 0

                            # Receivable or Cash (DR)
                            elif (
                                line.transaction_type == "dr"
                                and line.account_number.id
                                == (
                                    order.customer_id.account_receivable_id.id
                                    if order.payment_method
                                    not in ["cash", "bank_transfer"]
                                    else order.account_number.id
                                )
                            ):
                                updated_values["dr_amount"] = order_line.subtotal
                                updated_values["cr_amount"] = 0

                            # Income (CR)
                            elif (
                                line.transaction_type == "cr"
                                and line.account_number.id
                                == product.income_account_id.id
                            ):
                                updated_values["cr_amount"] = order_line.subtotal
                                updated_values["dr_amount"] = 0

                            line.write(updated_values)

                return res
        except Exception as e:
            _logger.error("Error in create: %s", e)
            raise ValidationError(models._("Creation failed: %s") % str(e))

    def unlink(self):
        try:
            with self.env.cr.savepoint():
                for order in self:
                    for line in order.order_lines:
                        # 🔒 Prevent delete if any payment has been made
                        receipt = self.env["idil.sales.receipt"].search(
                            [("cusotmer_sale_order_id", "=", order.id)], limit=1
                        )
                        if receipt and receipt.paid_amount > 0:
                            raise ValidationError(
                                f"❌ Cannot delete order '{order.name}' because it has a paid amount of {receipt.paid_amount:.2f}."
                            )
                        product = line.product_id
                        if product:
                            # 1. Restore the stock
                            # product.stock_quantity += line.quantity

                            # 2. Delete related product movement
                            self.env["idil.product.movement"].search(
                                [
                                    ("product_id", "=", product.id),
                                    ("source_document", "=", order.name),
                                ]
                            ).unlink()

                            # 3. Delete related booking lines
                            booking_lines = self.env[
                                "idil.transaction_bookingline"
                            ].search(
                                [
                                    ("product_id", "=", product.id),
                                    (
                                        "transaction_booking_id.cusotmer_sale_order_id",
                                        "=",
                                        order.id,
                                    ),
                                ]
                            )
                            booking_lines.unlink()

                    # 5. Delete transaction booking if it exists
                    booking = self.env["idil.transaction_booking"].search(
                        [("cusotmer_sale_order_id", "=", order.id)], limit=1
                    )
                    if booking:
                        booking.unlink()

                    res = super(CustomerSaleOrder, self).unlink()

                    # 4. Delete sales receipt
                    self.env["idil.sales.receipt"].search(
                        [("cusotmer_sale_order_id", "=", order.id)]
                    ).unlink()

                    return res
        except Exception as e:
            _logger.error("Error in create: %s", e)
            raise ValidationError(models._("Creation failed: %s") % str(e))


class CustomerSaleOrderLine(models.Model):
    _name = "idil.customer.sale.order.line"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _description = "CustomerSale Order Line"
    _order = "id desc"

    order_id = fields.Many2one("idil.customer.sale.order", string="Sale Order")
    product_id = fields.Many2one("my_product.product", string="Product")
    quantity_Demand = fields.Float(string="Demand", default=1.0)
    available_stock = fields.Float(
        string="Available Stock",
        related="product_id.stock_quantity",
        readonly=True,
        store=False,
    )

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
    customer_opening_balance_line_id = fields.Many2one(
        "idil.customer.opening.balance.line",
        string="Customer Opening Balance Line",
        ondelete="cascade",
    )

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
        try:
            with self.env.cr.savepoint():
                # If linked to opening balance, skip product_id and stock check!
                if vals.get("customer_opening_balance_line_id"):
                    vals["product_id"] = False  # Explicitly make sure it's empty
                    return super(CustomerSaleOrderLine, self).create(vals)
                # Else: normal process, require product and update stock
                if not vals.get("product_id"):
                    raise ValidationError(
                        "You must select a product for this order line (unless it's for opening balance)."
                    )
                record = super(CustomerSaleOrderLine, self).create(vals)
                self.update_product_stock(record.product_id, record.quantity)
                return record
        except Exception as e:
            _logger.error(f"Create transaction failed: {str(e)}")
            raise ValidationError(f"Transaction failed: {str(e)}")

    @staticmethod
    def update_product_stock(product, quantity):
        """Static Method: Update product stock quantity based on the sale order line quantity change."""
        # If this order is for opening balance, skip accounting booking: opening balance does its own accounting

        new_stock_quantity = product.stock_quantity - quantity
        if new_stock_quantity < 0:
            raise ValidationError(
                "Insufficient stock for product '{}'. The available stock quantity is {:.2f}, "
                "but the required quantity is {:.2f}.".format(
                    product.name, product.stock_quantity, abs(quantity)
                )
            )
        # product.stock_quantity = new_stock_quantity

    @api.constrains("quantity", "price_unit")
    def _check_quantity_and_price(self):
        """Ensure that quantity and unit price are greater than zero."""
        for line in self:

            # If this order is for opening balance, skip accounting booking: opening balance does its own accounting
            if line.customer_opening_balance_line_id:
                return

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

    order_id = fields.Many2one("idil.customer.sale.order", string="Customer Sale Order")
    sales_payment_id = fields.Many2one(
        "idil.sales.payment", string="Sales Payment", ondelete="cascade"
    )
    sales_receipt_id = fields.Many2one("idil.sales.receipt", string="Sales Receipt")

    customer_id = fields.Many2one(
        "idil.customer.registration", string="Customer", required=True
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

    payment_method = fields.Selection(
        [("cash", "Cash"), ("ar", "A/R")],
        string="Payment Method",
        required=True,
    )

    account_id = fields.Many2one("idil.chart.account", string="Account", required=True)
    amount = fields.Float(string="Amount", required=True)
