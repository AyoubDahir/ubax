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
    _order = "id desc"

    order_id = fields.Many2one(
        "idil.purchase_order", string="Order", ondelete="cascade"
    )

    item_id = fields.Many2one("idil.item", string="Item", required=True)
    quantity = fields.Integer(string="Quantity", required=True)
    cost_price = fields.Float(
        string="Cost per Unit", digits=(16, 5), required=True, tracking=True
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
    item_movement_ids = fields.One2many(
        "idil.item.movement",
        "purchase_order_line_id",
        string="Item Movements",
        auto_join=True,
        ondelete="cascade",
    )

    @api.onchange("item_id")
    def _onchange_item_id(self):
        if self.item_id:
            self.cost_price = self.item_id.cost_price

    @api.model
    def create(self, values):
        # If cost_price is 0 or not provided, get it from the item
        if not values.get("cost_price"):
            item = self.env["idil.item"].browse(values.get("item_id"))
            values["cost_price"] = item.cost_price

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
            # new_line._create_stock_transaction(values)

            return new_line

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

    @api.depends("item_id", "quantity", "cost_price")
    def _compute_total_price(self):
        for line in self.filtered(lambda l: l.exists()):
            if line.item_id:
                if line.cost_price > 0:
                    line.amount = line.cost_price * line.quantity
                else:
                    line.amount = line.item_id.cost_price * line.quantity
            else:
                line.amount = 0.0

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
    _order = "id desc"

    reffno = fields.Char(string="Reference Number")  # Consider renaming for clarity
    vendor_id = fields.Many2one(
        "idil.vendor.registration", string="Vendor", required=True
    )
    invoice_number = fields.Char(
        string="Invoice Number",
        required=True,
        tracking=True,
    )
    purchase_date = fields.Date(
        string="Purchase Date", default=fields.Date.today, required=True
    )

    order_lines = fields.One2many(
        "idil.purchase_order.line", "order_id", string="Order Lines"
    )

    description = fields.Text(string="Description")
    payment_method = fields.Selection(
        [("cash", "Cash"), ("ap", "A/P"), ("bank_transfer", "Bank")],
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

    def _create_item_movements(self):
        for order in self:
            for line in order.order_lines:
                self.env["idil.item.movement"].create(
                    {
                        "item_id": line.item_id.id,
                        "purchase_order_line_id": line.id,
                        "date": fields.Date.today(),
                        "quantity": line.quantity,
                        "source": "Vendor",
                        "destination": "Inventory",
                        "movement_type": "in",
                        "related_document": f"idil.purchase_order.line,{line.id}",
                    }
                )
                _logger.info(
                    f"[ITEM MOVEMENT] Created for item {line.item_id.name} | Qty: {line.quantity}"
                )

    def _update_item_stock(self):
        for order in self:
            for line in order.order_lines:
                item = line.item_id
                quantity = line.quantity
                cost_price = line.cost_price

                if not item:
                    continue

                if quantity > 0:
                    current_stock = item.quantity
                    current_cost_price = item.cost_price

                    total_current_value = current_stock * current_cost_price
                    total_new_value = quantity * cost_price
                    new_quantity = current_stock + quantity

                    if new_quantity > 0:
                        new_cost_price = (
                            total_current_value + total_new_value
                        ) / new_quantity
                    else:
                        new_cost_price = cost_price

                    update_vals = {"quantity": new_quantity}
                    if cost_price != 0:
                        update_vals["cost_price"] = new_cost_price

                    item.with_context(update_transaction_booking=False).write(
                        update_vals
                    )

                elif quantity < 0:
                    if item.quantity >= abs(quantity):
                        item.with_context(update_transaction_booking=False).write(
                            {"quantity": item.quantity - abs(quantity)}
                        )
                    else:
                        raise exceptions.ValidationError(
                            f"Insufficient stock for item '{item.name}'. "
                            f"Available: {item.quantity}, trying to remove: {abs(quantity)}"
                        )

    def create_vendor_transaction(self):
        transaction = self.env["idil.transaction_booking"].search(
            [("order_number", "=", self.id)], limit=1
        )
        if not transaction:
            return

        existing_vendor_transaction = self.env["idil.vendor_transaction"].search(
            [("order_number", "=", self.id)], limit=1
        )
        if existing_vendor_transaction:
            return  # Avoid duplicates

        self.env["idil.vendor_transaction"].create(
            {
                "order_number": self.id,
                "transaction_number": transaction.transaction_number,
                "transaction_date": self.purchase_date,
                "vendor_id": self.vendor_id.id,
                "amount": transaction.amount,
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

    def create_transaction_booking_with_lines(self):
        if not self.order_lines:
            return

        # Check if already exists
        if self.env["idil.transaction_booking"].search(
            [("order_number", "=", self.id)]
        ):
            return

        transaction_number = (
            self.env["idil.transaction_booking"]
            .search([], order="transaction_number desc", limit=1)
            .transaction_number
            or 0
        ) + 1

        trx_source_id = self.env["idil.transaction.source"].search(
            [("name", "=", "Purchase Order")], limit=1
        )
        if not trx_source_id:
            raise ValidationError(_('Transaction source "Purchase Order" not found.'))

        total_amount = sum(line.amount for line in self.order_lines)

        transaction = self.env["idil.transaction_booking"].create(
            {
                "reffno": self.reffno,
                "transaction_number": transaction_number,
                "vendor_id": self.vendor_id.id,
                "order_number": self.id,
                "payment_method": self.payment_method,
                "trx_source_id": trx_source_id.id,
                "purchase_order_id": self.id,
                "payment_status": (
                    "paid" if self.payment_method == "cash" else "pending"
                ),
                "trx_date": self.purchase_date,
                "amount": total_amount,
                "remaining_amount": (
                    0 if self.payment_method == "cash" else total_amount
                ),
                "amount_paid": total_amount if self.payment_method == "cash" else 0,
            }
        )

        # Now create booking lines
        for line in self.order_lines:
            # Fallback to company currency if not explicitly set
            # Validate currency consistency
            stock_acc = line.item_id.asset_account_id
            payment_acc = (
                self.account_number
                if self.payment_method == "cash"
                else self.vendor_id.account_payable_id
            )

            # Fallback to company currency if currency not explicitly set
            stock_currency = stock_acc.currency_id or stock_acc.company_id.currency_id
            payment_currency = (
                payment_acc.currency_id or payment_acc.company_id.currency_id
            )

            if stock_currency.id != payment_currency.id:
                raise ValidationError(
                    f"Currency mismatch detected:\n"
                    f"Debit Account '{stock_acc.name}' uses '{stock_currency.name}',\n"
                    f"Credit Account '{payment_acc.name}' uses '{payment_currency.name}'.\n"
                    f"Both must use the same currency."
                )

            stock_account = line.item_id.asset_account_id.id
            if self.payment_method == "cash":

                purchase_account = self.account_number.id
                account_id = self.account_number.id
                total_amount = sum(line.amount for line in self.order_lines)
                validate_account_balance(self.env, account_id, total_amount)
            else:

                purchase_account = self.vendor_id.account_payable_id.id

            # DR line (stock)
            self.env["idil.transaction_bookingline"].create(
                {
                    "order_line": line.id,
                    "item_id": line.item_id.id,
                    "description": f"{line.item_id.name} - Purchase Ref No. #{self.reffno}",
                    "account_number": stock_account,
                    "transaction_type": "dr",
                    "dr_amount": line.amount,
                    "cr_amount": 0,
                    "transaction_date": self.purchase_date,
                    "transaction_booking_id": transaction.id,
                }
            )

            # CR line (payment or AP)
            self.env["idil.transaction_bookingline"].create(
                {
                    "order_line": line.id,
                    "item_id": line.item_id.id,
                    "description": f"{line.item_id.name} - Purchase Ref No. #{self.reffno}",
                    "account_number": purchase_account,
                    "transaction_type": "cr",
                    "dr_amount": 0,
                    "cr_amount": line.amount,
                    "transaction_date": self.purchase_date,
                    "transaction_booking_id": transaction.id,
                }
            )

    @api.model
    def create(self, vals):
        """
        Override the default create method to customize the reference number.
        """
        # Generate the reference number
        vals["reffno"] = self._generate_purchase_order_reference(vals)
        # Call the super method to create the record with updated values
        order = super(PurchaseOrder, self).create(vals)
        order.create_transaction_booking_with_lines()
        order.create_vendor_transaction()
        order._update_item_stock()  # 🔁 Shift stock update here
        order._create_item_movements()  # 👈 Call movement creation here

        return order

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

    @api.depends("order_lines.amount")
    def _compute_total_amount(self):
        for order in self:
            order.amount = sum(line.amount for line in order.order_lines.exists())

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

    def write(self, vals):
        for order in self:
            # ✅ Check if this line is referenced in any purchase return (that is not cancelled)
            related_returns = self.env["idil.purchase_return"].search(
                [
                    ("original_order_id", "=", self.id),
                    ("state", "!=", "cancel"),
                ]
            )

            if related_returns:
                return_info = "\n".join(
                    f"- Return: {related_returns.name}, Date: {related_returns.return_date}"
                    for ret in related_returns
                )
                raise ValidationError(
                    f"Cannot update this purchase line because it is referenced in the following Purchase Return(s):\n\n"
                    f"{return_info}\n\n"
                    "To update this line, please delete the related purchase return(s) first."
                )

            # ❌ Block update if payments exist
            vendor_transactions = self.env["idil.vendor_transaction"].search(
                [("order_number", "=", order.id)]
            )
            if vendor_transactions:
                vendor_payments = self.env["idil.vendor_payment"].search(
                    [("vendor_transaction_id", "in", vendor_transactions.ids)]
                )
                if vendor_payments:
                    payment_info = "\n".join(
                        f"- Reference: {payment.reffno or 'N/A'}, Amount Paid: {payment.amount_paid:.2f}"
                        for payment in vendor_payments
                    )
                    raise ValidationError(
                        _(
                            f"Cannot update this Purchase Order because the following payment(s) are linked to it:\n\n"
                            f"{payment_info}\n\n"
                            "Please unlink or delete these payments before updating."
                        )
                    )

            # Prevent payment method change
            if "payment_method" in vals:
                old_method = order.payment_method
                new_method = vals["payment_method"]
                if old_method and new_method and old_method != new_method:
                    raise ValidationError(
                        _(
                            "Changing the payment method is not allowed once it has been set."
                        )
                    )

            # --- 1. Reverse Stock Quantities ---
            for line in order.order_lines:
                item = line.item_id
                if item:
                    reverse_qty = -line.quantity  # Reverse addition
                    item.with_context(update_transaction_booking=False).write(
                        {"quantity": item.quantity + reverse_qty}
                    )

            # --- 2. Remove Old Movements ---
            movements = self.env["idil.item.movement"].search(
                [("purchase_order_line_id", "in", order.order_lines.ids)]
            )
            if movements:
                movements.unlink()

            # --- 3. Remove Old Booking Lines and Bookings ---
            bookings = self.env["idil.transaction_booking"].search(
                [("order_number", "=", order.id)]
            )
            for booking in bookings:
                lines = self.env["idil.transaction_bookingline"].search(
                    [("transaction_booking_id", "=", booking.id)]
                )
                lines.unlink()
            bookings.unlink()

            # --- 4. Remove Old Vendor Transactions ---
            vendors = self.env["idil.vendor_transaction"].search(
                [("order_number", "=", order.id)]
            )
            if vendors:
                vendors.unlink()

            # --- 5. Apply Super Write ---
            result = super(PurchaseOrder, order).write(vals)

            # --- 6. Rebuild Booking, Stock, Vendor Txn, Movement ---
            order._update_item_stock()
            order.create_transaction_booking_with_lines()
            order.create_vendor_transaction()
            order._create_item_movements()

            return result

    def unlink(self):
        for order in self:
            # Check if any vendor payments are linked to the vendor transaction of this purchase order
            vendor_transactions = self.env["idil.vendor_transaction"].search(
                [("order_number", "=", self.id)]
            )
            if vendor_transactions:
                vendor_payments = self.env["idil.vendor_payment"].search(
                    [("vendor_transaction_id", "in", vendor_transactions.ids)]
                )
                if vendor_payments:
                    payment_info = "\n".join(
                        f"- Reference: {payment.reffno or 'N/A'}, Amount Paid: {payment.amount_paid:.2f}"
                        for payment in vendor_payments
                    )
                    raise ValidationError(
                        _(
                            f"Cannot delete or modify this Purchase Order because the following payment(s) are linked to it:\n\n"
                            f"{payment_info}\n\n"
                            "Please unlink or delete these payments before proceeding."
                        )
                    )

            # --- 1. Adjust Stock Quantities ---
            for line in order.order_lines:
                item = line.item_id
                if item:
                    new_qty = item.quantity - line.quantity
                    if new_qty < 0:
                        raise ValidationError(
                            f"Cannot delete order: Item '{item.name}' would have negative stock.\n"
                            f"Current: {item.quantity}, Removing: {line.quantity}"
                        )
                    item.write({"quantity": new_qty})

            # --- 2. Delete Item Movements ---
            self.env["idil.item.movement"].search(
                [("purchase_order_line_id", "in", order.order_lines.ids)]
            ).unlink()

            # --- 3. Delete Transaction Booking Lines + Bookings ---
            bookings = self.env["idil.transaction_booking"].search(
                [("order_number", "=", order.id)]
            )
            for booking in bookings:
                self.env["idil.transaction_bookingline"].search(
                    [("transaction_booking_id", "=", booking.id)]
                ).unlink()
            bookings.unlink()

            # --- 4. Delete Vendor Transactions ---
            self.env["idil.vendor_transaction"].search(
                [("order_number", "=", order.id)]
            ).unlink()

            # --- 5. Delete Order Lines (if needed) ---
            order.order_lines.unlink()

        # --- 6. Delete the Purchase Order ---
        return super(PurchaseOrder, self).unlink()

    # Utility: Validate account balance centrally


def validate_account_balance(env, account_id, required_amount):
    """
    Validates that the account has sufficient balance for a transaction.

    :param env: The Odoo environment, e.g. self.env
    :param account_id: The ID of the account to check
    :param required_amount: The amount needed for the transaction
    :raises: ValidationError if balance is insufficient
    """
    account = env["idil.chart.account"].browse(account_id)
    if not account.exists():
        raise ValidationError(f"Account ID {account_id} not found.")

    lines = env["idil.transaction_bookingline"].search(
        [("account_number", "=", account_id)]
    )
    debit = sum(line.dr_amount for line in lines)
    credit = sum(line.cr_amount for line in lines)
    balance = debit - credit

    if balance < required_amount:
        raise ValidationError(
            f"Insufficient balance in account '{account.name}' (Code: {account.code}).\n"
            f"Available: {balance:.2f}, Required: {required_amount:.2f}"
        )
