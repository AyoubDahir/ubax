import re
from datetime import datetime
import logging
from odoo import models, fields, exceptions, api, _
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


class PurchaseOrderLine(models.Model):
    _name = "idil.purchase_order.line"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _description = "Purchase Order"

    order_id = fields.Many2one(
        "idil.purchase_order", string="Order", ondelete="cascade"
    )

    item_id = fields.Many2one("idil.item", string="Item", required=True)
    quantity = fields.Integer(string="Quantity", required=True)
    cost_price = fields.Float(
        string="Cost per Unit", digits=(16, 3), required=True, tracking=True
    )

    amount = fields.Float(
        string="Total Price", compute="_compute_total_price", store=True
    )
    expiration_date = fields.Date(
        string="Expiration Date", required=True
    )  # Add expiration date field

    transaction_ids = fields.One2many(
        "idil.transaction_bookingline", "order_line", string="Transactions"
    )

    def _create_item_movement(self, values):
        """Create an item movement entry."""
        if self.item_id:
            self.env["idil.item.movement"].create(
                {
                    "item_id": self.item_id.id,
                    "date": fields.Date.today(),
                    "quantity": self.quantity,
                    "source": "Vendor",
                    "destination": "Inventory",
                    "movement_type": "in",
                    "related_document": f"idil.purchase_order.line,{self.id}",
                }
            )
            _logger.info(
                f"Created item movement for item {self.item_id.name} with quantity {self.quantity}"
            )

    @api.model
    def create(self, values):
        existing_line = self.search(
            [
                ("order_id", "=", values.get("order_id")),
                ("item_id", "=", values.get("item_id")),
            ]
        )
        if existing_line:
            existing_line.write(
                {"quantity": existing_line.quantity + values.get("quantity", 0)}
            )
            return existing_line
        else:

            new_line = super(PurchaseOrderLine, self).create(values)
            new_line._update_item_stock(
                values.get("quantity", 0),
                values.get("cost_price"),
            )
            new_line._create_stock_transaction(values)
            new_line._create_item_movement(values)

            return new_line

    @api.model
    def _create_stock_transaction(self, values):
        try:
            purchase_account_number = self._validate_purchase_account()
            self._check_account_balance(purchase_account_number)
            new_transaction_number = self._get_next_transaction_number()

            stock_account_number = self._get_stock_account_number()

            # Retrieve accounts to check their currencies
            purchase_account = self.env["idil.chart.account"].browse(
                purchase_account_number
            )
            stock_account = self.env["idil.chart.account"].browse(stock_account_number)

            if purchase_account.currency_id != stock_account.currency_id:
                raise ValidationError(
                    _("Credit and Debit accounts must have the same currency type.")
                )

            transaction_values = self._prepare_transaction_values(
                new_transaction_number, values
            )
            new_transaction = self._create_transaction_record(transaction_values)

            self._create_transaction_line(
                new_transaction.id, new_transaction_number, stock_account_number, "dr"
            )
            self._create_transaction_line(
                new_transaction.id,
                new_transaction_number,
                purchase_account_number,
                "cr",
            )

            # Create Vendor Transaction after creating the transaction booking and lines
            self._create_vendor_transaction(new_transaction, values)

        except Exception as e:
            _logger.error(f"Error creating transaction: {e}")
            raise

    def _create_transaction_line(
        self, transaction_id, transaction_number, account_number, transaction_type
    ):
        line_values = {
            # 'transaction_number': transaction_number,
            "order_line": self.id,
            "item_id": self.item_id.id,
            "account_number": account_number,
            "transaction_type": transaction_type,
            "dr_amount": self.amount if transaction_type == "dr" else 0,
            "cr_amount": 0 if transaction_type == "dr" else self.amount,
            "transaction_date": fields.Date.today(),
            "transaction_booking_id": transaction_id,
        }
        self.env["idil.transaction_bookingline"].create(line_values)

    def _create_transaction_record(self, transaction_values):
        # Check the payment method and set payment status accordingly
        if transaction_values.get("payment_method") == "cash":
            transaction_values["payment_status"] = "paid"
        else:
            transaction_values["payment_status"] = "pending"

        # Check if a transaction for the order already exists
        existing_transaction = self.env["idil.transaction_booking"].search(
            [
                ("order_number", "=", transaction_values.get("order_number")),
            ],
            limit=1,
        )

        if existing_transaction:
            # If it exists, update the amount and payment status
            existing_transaction.write(
                {
                    "amount": transaction_values["amount"],
                    "payment_status": transaction_values["payment_status"],
                    # Update any other relevant fields as necessary
                }
            )
            return existing_transaction
        else:

            # If no existing transaction, create a new one
            return self.env["idil.transaction_booking"].create(transaction_values)

    def _prepare_transaction_values(self, transaction_number, values):
        trx_source_id = self.get_manual_transaction_source_id()
        # Calculate the total amount of all order lines for this order
        total_amount = sum(line.amount for line in self.order_id.order_lines)

        return {
            "reffno": self.order_id.reffno,
            "transaction_number": transaction_number,
            "vendor_id": self.order_id.vendor_id.id,
            "order_number": self.order_id.id,
            "payment_method": self.order_id.payment_method,
            "trx_source_id": trx_source_id,
            "purchase_order_id": self.order_id.id,
            "payment_status": (
                "paid" if self.order_id.payment_method == "cash" else "pending"
            ),
            "trx_date": fields.Date.today(),
            "amount": total_amount,  # Use the total amount of all lines here
            # 'remaining_amount': total_amount,
            "remaining_amount": (
                0 if self.order_id.payment_method == "cash" else total_amount
            ),
            "amount_paid": (
                total_amount if self.order_id.payment_method == "cash" else 0
            ),
        }

    def get_manual_transaction_source_id(self):
        trx_source = self.env["idil.transaction.source"].search(
            [("name", "=", "Purchase Order")], limit=1
        )
        if not trx_source:
            raise ValidationError(_('Transaction source "Purchase Order" not found.'))
        return trx_source.id

    def _create_vendor_transaction(self, transaction, values):
        vendor_transaction_values = {
            "order_number": transaction.order_number,
            "transaction_number": transaction.transaction_number,
            "transaction_date": transaction.trx_date,
            "vendor_id": transaction.vendor_id.id,
            "amount": transaction.amount,
            # 'remaining_amount': transaction.amount,
            # 'paid_amount': 0,
            "remaining_amount": (
                0 if transaction.payment_method == "cash" else transaction.amount
            ),
            "paid_amount": (
                transaction.amount if transaction.payment_method == "cash" else 0
            ),
            "payment_method": transaction.payment_method,
            "reffno": transaction.reffno,
            "transaction_booking_id": transaction.id,
            "payment_status": (
                "paid" if transaction.payment_method == "cash" else "pending"
            ),
        }
        self.env["idil.vendor_transaction"].create(vendor_transaction_values)

    def _sum_order_line_amounts(self):
        # Corrected to use the proper field name 'order_lines'
        return sum(line.amount for line in self.order_id.order_lines)

    def _get_next_transaction_number(self):
        max_transaction_number = (
            self.env["idil.transaction_booking"]
            .search([], order="transaction_number desc", limit=1)
            .transaction_number
            or 0
        )
        return max_transaction_number + 1

    def _get_stock_account_number(self):
        return self.item_id.asset_account_id.id

        # return self.env['idil.transaction_booking'].create(transaction_values)

    def _calculate_account_balance(self, account_number):
        """
        Calculate the balance for a given account number.
        """
        transactions = self.env["idil.transaction_bookingline"].search(
            [("account_number", "=", account_number)]
        )
        debit_sum = sum(transaction.dr_amount for transaction in transactions)
        credit_sum = sum(transaction.cr_amount for transaction in transactions)
        return debit_sum - credit_sum

    def _determine_purchase_account_number(self):
        """Determine purchase account number based on payment method."""
        _logger.debug(
            f"Determining account number for payment method: {self.order_id.payment_method}"
        )
        if self.order_id.payment_method == "cash":
            account = self.env["idil.chart.account"].search(
                [("account_type", "=", "cash")], limit=1
            )
            _logger.debug(f"Cash account found: {account.code if account else 'None'}")
            return account.id if account else False
        elif self.order_id.payment_method == "ap":
            account = self.order_id.vendor_id.account_payable_id
            _logger.debug(f"AP account found: {account.code if account else 'None'}")
            return account.id if account else False
        # Implement logic for other payment methods
        _logger.error("No account found for the specified payment method")
        return False

    def _validate_purchase_account(self):
        purchase_account_number = self._determine_purchase_account_number()
        if not purchase_account_number:
            _logger.error(
                f"No purchase account number found for payment method {self.order_id.payment_method}"
            )
            raise exceptions.UserError(
                "Purchase account number is required but was not found."
            )
        return purchase_account_number

    def _check_account_balance(self, purchase_account_number):
        # Check if the payment method is 'cash' or 'bank_transfer'
        if self.order_id.payment_method not in ["cash", "bank_transfer"]:
            return  # Skip balance check for other payment methods

        account_balance = self._calculate_account_balance(purchase_account_number)
        if account_balance < self.amount:
            raise exceptions.UserError(
                f"Insufficient balance in account {purchase_account_number} for this transaction. "
                f"Account balance is {account_balance}, but the transaction amount is {self.amount}."
            )

    def write(self, values):
        if "quantity" in values:
            # Calculate the difference between the new and old quantities
            quantity_diff = values["quantity"] - self.quantity

            if quantity_diff != 0:
                # Update item movement entry for the quantity change
                item_movement = self.env["idil.item.movement"].search(
                    [("related_document", "=", f"idil.purchase_order.line,{self.id}")],
                    limit=1,
                )
                if item_movement:
                    item_movement.quantity += quantity_diff
                else:
                    # Create new item movement if it doesn't exist
                    self._create_item_movement({"quantity": quantity_diff})

                # Update item stock based on the quantity difference
                cost_price = values.get("cost_price", self.cost_price)
                self._update_item_stock(quantity_diff, cost_price)

                # Adjust stock transactions and vendor transactions
                self._adjust_stock_transaction(values)
                self._adjust_vendor_transaction(values)

        return super(PurchaseOrderLine, self).write(values)

    def _adjust_stock_transaction(self, values):
        """Adjust stock transactions based on the quantity change."""
        try:
            new_quantity = values["quantity"]
            new_amount = new_quantity * self.item_id.cost_price

            transaction_lines = self.env["idil.transaction_bookingline"].search(
                [("order_line", "=", self.id)]
            )

            for line in transaction_lines:
                if line.transaction_type == "dr":
                    line.write({"dr_amount": new_amount})
                elif line.transaction_type == "cr":
                    line.write({"cr_amount": new_amount})

            # Adjust the total transaction amount
            transaction = self.env["idil.transaction_booking"].search(
                [("id", "=", transaction_lines[0].transaction_booking_id.id)]
            )
            if transaction:
                transaction.write(
                    {
                        "amount": new_amount,
                        "remaining_amount": (
                            new_amount if transaction.payment_method != "cash" else 0
                        ),
                        "amount_paid": (
                            new_amount if transaction.payment_method == "cash" else 0
                        ),
                    }
                )

        except Exception as e:
            _logger.error(f"Error adjusting stock transaction: {e}")
            raise

    def _adjust_vendor_transaction(self, values):
        """Adjust vendor transactions based on the quantity change."""
        try:
            # Calculate the new amount based on the new quantity and cost price
            new_quantity = values["quantity"]
            new_amount = new_quantity * self.item_id.cost_price

            vendor_transaction = self.env["idil.vendor_transaction"].search(
                [("order_number", "=", self.order_id.id)], limit=1
            )

            if vendor_transaction:
                vendor_transaction.write(
                    {
                        "amount": new_amount,
                        "remaining_amount": (
                            new_amount
                            if vendor_transaction.payment_method != "cash"
                            else 0
                        ),
                        "paid_amount": (
                            new_amount
                            if vendor_transaction.payment_method == "cash"
                            else 0
                        ),
                    }
                )

        except Exception as e:
            _logger.error(f"Error adjusting vendor transaction: {e}")
            raise

    def unlink(self):
        for line in self:
            # Check and delete all related transaction_bookingline records
            transaction_lines = self.env["idil.transaction_bookingline"].search(
                [("order_line", "=", line.id)]
            )
            if transaction_lines:
                transaction_lines.unlink()

            # Check and delete related transaction_booking records if there are no more lines linked to them
            transactions = transaction_lines.mapped("transaction_booking_id")
            for transaction in transactions:
                if not self.env["idil.transaction_bookingline"].search_count(
                    [("transaction_booking_id", "=", transaction.id)]
                ):
                    transaction.unlink()

            # Check and delete related vendor_transaction records
            vendor_transactions = self.env["idil.vendor_transaction"].search(
                [("order_number", "=", line.order_id.id)]
            )
            if vendor_transactions:
                vendor_transactions.unlink()

            # Check and delete related item_movement records
            item_movements = self.env["idil.item.movement"].search(
                [("related_document", "=", f"idil.purchase_order.line,{line.id}")]
            )
            if item_movements:
                item_movements.unlink()

        return super(PurchaseOrderLine, self).unlink()

    @api.depends("item_id", "quantity", "cost_price")
    def _compute_total_price(self):
        for line in self:
            if line.item_id:
                if line.cost_price > 0:
                    line.amount = line.cost_price * line.quantity
                else:
                    line.amount = line.item_id.cost_price * line.quantity
            else:
                line.amount = 0.0

    def _update_item_stock(self, quantity, cost_price):
        """
        Updates the stock quantity and cost price for an item.

        - Adjusts the item's quantity.
        - Recalculates and updates the item's cost price using weighted average logic.
        """
        if self.item_id:
            try:
                if quantity > 0:
                    # Calculate the weighted average cost price
                    current_stock = self.item_id.quantity
                    current_cost_price = self.item_id.cost_price

                    total_current_value = current_stock * current_cost_price
                    total_new_value = quantity * cost_price

                    # New stock after adjustment
                    new_quantity = current_stock + quantity

                    if new_quantity > 0:
                        # Calculate new weighted average cost price
                        new_cost_price = (
                            total_current_value + total_new_value
                        ) / new_quantity
                    else:
                        new_cost_price = cost_price  # Use the cost price directly if new_quantity becomes 0

                    # Update the item's stock and cost price
                    update_values = {"quantity": new_quantity}
                    if cost_price != 0:  # Only update cost_price if it's not zero
                        update_values["cost_price"] = new_cost_price

                    self.item_id.with_context(update_transaction_booking=False).write(
                        update_values
                    )

                elif quantity < 0:
                    # Decrease stock
                    if self.item_id.quantity >= abs(quantity):
                        new_quantity = self.item_id.quantity - abs(quantity)
                        self.item_id.with_context(
                            update_transaction_booking=False
                        ).write(
                            {
                                "quantity": new_quantity,
                            }
                        )
                    else:
                        raise exceptions.ValidationError(
                            f"Insufficient stock for item '{self.item_id.name}'. "
                            f"Current stock: {self.item_id.quantity}, Requested decrease: {abs(quantity)}"
                        )
            except exceptions.ValidationError as e:
                raise exceptions.ValidationError(e.args[0])

    def add_item(self):
        if self.order_id.vendor_id and self.order_id.vendor_id.stock_supplier:
            new_line = self.env["idil.purchase_order.line"].create(
                {
                    "order_id": self.order_id.id,
                    "expiration_date": fields.Date.today(),
                    # Initialize other fields here (if needed)
                }
            )
            return {
                "type": "ir.actions.act_window",
                "res_model": "idil.purchase_order.line",
                "view_mode": "form",
                "res_id": new_line.id,
                "target": "current",
            }
        else:
            raise exceptions.ValidationError("Vendor stock information not available!")


class PurchaseOrder(models.Model):
    _name = "idil.purchase_order"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _description = "Purchase Order Lines"

    reffno = fields.Char(string="Reference Number")  # Consider renaming for clarity
    vendor_id = fields.Many2one(
        "idil.vendor.registration", string="Vendor", required=True
    )
    order_lines = fields.One2many(
        "idil.purchase_order.line", "order_id", string="Order Lines"
    )

    description = fields.Text(string="Description")
    payment_method = fields.Selection(
        [("cash", "Cash"), ("ap", "A/P"), ("bank_transfer", "Bank Transfer")],
        string="Payment Method",
        required=True,
    )
    account_number = fields.Many2one(
        "idil.chart.account",
        string="Account Number",
        required=True,
        domain="[('account_type', '=', payment_method)]",
    )

    amount = fields.Float(
        string="Total Price", compute="_compute_total_amount", store=True, readonly=True
    )

    @api.onchange("payment_method", "vendor_id")
    def _onchange_payment_method(self):
        self.account_number = (
            False  # Reset account number with any change to ensure correctness
        )
        if not self.payment_method:
            return {"domain": {"account_number": []}}

        if self.payment_method == "ap" and self.vendor_id:
            # Assuming 'vendor_account_number' is a field on the vendor pointing to 'idil.chart.account'
            self.account_number = self.vendor_id.account_payable_id.id
            return {
                "domain": {
                    "account_number": [
                        ("id", "=", self.vendor_id.account_payable_id.id)
                    ]
                }
            }
        elif self.payment_method == "cash":
            # Adjust the domain to suit how you distinguish cash accounts in 'idil.chart.account'
            return {"domain": {"account_number": [("account_type", "=", "cash")]}}

        # For bank_transfer or any other case, adjust the domain as needed
        domain = {"account_number": [("account_type", "=", self.payment_method)]}
        return {"domain": domain}

    @api.model
    def create(self, vals):
        """
        Override the default create method to customize the reference number.
        """
        # Generate the reference number
        vals["reffno"] = self._generate_purchase_order_reference(vals)
        # Call the super method to create the record with updated values
        return super(PurchaseOrder, self).create(vals)

    def _generate_purchase_order_reference(self, values):
        vendor_id = values.get("vendor_id", False)
        if vendor_id:
            vendor_id = self.env["idil.vendor.registration"].browse(vendor_id)
            vendor_name = (
                "PO/" + re.sub("[^A-Za-z0-9]+", "", vendor_id.name[:2]).upper()
                if vendor_id and vendor_id.name
                else "XX"
            )
            date_str = "/" + datetime.now().strftime("%d%m%Y")
            day_night = "/DAY/" if datetime.now().hour < 12 else "/NIGHT/"
            sequence = self.env["ir.sequence"].next_by_code(
                "idil.purchase_order.sequence"
            )
            sequence = sequence[-3:] if sequence else "000"
            return f"{vendor_name}{date_str}{day_night}{sequence}"
        else:
            # Fallback if no BOM is provided
            return self.env["ir.sequence"].next_by_code("idil.purchase_order.sequence")

    @api.depends("order_lines.amount")  # Corrected from 'order_lines.price_total'
    def _compute_total_amount(self):
        for order in self:
            order.amount = sum(line.amount for line in order.order_lines)

    def unlink(self):
        for order in self:
            # Check and delete all related order lines and their related records
            if order.order_lines:
                order.order_lines.unlink()

            # Check and delete related transaction_booking records
            transactions = self.env["idil.transaction_booking"].search(
                [("order_number", "=", order.id)]
            )
            if transactions:
                transactions.unlink()

            # Check and delete related vendor_transaction records
            vendor_transactions = self.env["idil.vendor_transaction"].search(
                [("order_number", "=", order.id)]
            )
            if vendor_transactions:
                vendor_transactions.unlink()

        return super(PurchaseOrder, self).unlink()
