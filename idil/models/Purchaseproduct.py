from asyncio.log import logger
from odoo import models, fields, api
from odoo.exceptions import UserError, ValidationError


class ProductPurchaseOrder(models.Model):
    _name = "idil.product.purchase.order"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _description = "Product Purchase Order"
    _order = "id desc"

    name = fields.Char(string="Reference", readonly=True, default="New")
    vendor_id = fields.Many2one(
        "idil.vendor.registration", string="Vendor", required=True
    )
    order_lines = fields.One2many(
        "idil.product.purchase.order.line", "order_id", string="Order Lines"
    )
    payment_method = fields.Selection(
        [("cash", "Cash"), ("ap", "A/P"), ("bank_transfer", "Bank")],
        string="Payment Method",
        required=True,
    )

    invoice_number = fields.Char(
        string="Invoice Number",
        required=True,
        tracking=True,
    )

    purchase_date = fields.Date(
        string="Purchase Date", default=fields.Date.today, required=True
    )

    amount = fields.Float(
        string="Total Amount", compute="_compute_total_amount", store=True
    )

    account_number = fields.Many2one(
        "idil.chart.account",
        string="Account Number",
        domain="[('account_type', '=', payment_method)]",
    )

    @api.depends("order_lines.amount")
    def _compute_total_amount(self):
        for rec in self:
            rec.amount = sum(line.amount for line in rec.order_lines)

    @api.model
    def create(self, vals):
        if vals.get("name", "New") == "New":
            vals["name"] = (
                self.env["ir.sequence"].next_by_code("product.purchase.order.seq")
                or "New"
            )
        return super().create(vals)

    def unlink(self):
        try:
            with self.env.cr.savepoint():
                order_ids = []

                # Step 1: Validate all orders first
                for order in self:
                    for line in order.order_lines:
                        product = line.product_id
                        if product and line.quantity:
                            if product.stock_quantity < line.quantity:
                                raise ValidationError(
                                    f"Cannot delete Purchase Order '{order.name}' because product '{product.name}' "
                                    f"has only {product.stock_quantity} in stock, but {line.quantity} is needed to reverse."
                                )
                    order_ids.append(order.id)

                # Step 2: Adjust stock before deletion
                for order in self:
                    for line in order.order_lines:
                        product = line.product_id
                        if product and line.quantity:
                            product.stock_quantity -= line.quantity

                # Step 3: Delete main purchase orders
                res = super(ProductPurchaseOrder, self).unlink()

                # Step 4: Cleanup related records after deletion
                for order_id in order_ids:
                    # Delete related transaction lines
                    self.env["idil.transaction_bookingline"].search(
                        [("product_purchase_order_id", "=", order_id)]
                    ).unlink()

                    # Delete product movements
                    self.env["idil.product.movement"].search(
                        [("product_purchase_order_id", "=", order_id)]
                    ).unlink()

                    # Delete vendor transactions
                    self.env["idil.vendor_transaction"].search(
                        [("product_purchase_order_id", "=", order_id)]
                    ).unlink()

                    # Delete transaction bookings
                    self.env["idil.transaction_booking"].search(
                        [("product_purchase_order_id", "=", order_id)]
                    ).unlink()

                return res
        except Exception as e:
            logger.error(f"transaction failed: {str(e)}")
            raise ValidationError(f"Transaction failed: {str(e)}")

    def write(self, vals):
        # Check if order_lines are being updated
        if "order_lines" in vals:
            for command in vals["order_lines"]:
                if command[0] in [1, 0]:  # update or create
                    line_vals = command[2] or {}
                    line_id = command[1]
                    if line_id and "quantity" in line_vals:
                        # Fetch current line record
                        line = self.env["idil.product.purchase.order.line"].browse(
                            line_id
                        )
                        old_qty = line.quantity
                        new_qty = line_vals["quantity"]
                        qty_diff = new_qty - old_qty

                        if qty_diff < 0:
                            product = line.product_id
                            if product.stock_quantity < abs(qty_diff):
                                raise ValidationError(
                                    f"Cannot reduce quantity of product '{product.name}'. "
                                    f"Available stock is {product.stock_quantity}, but trying to reduce by {abs(qty_diff)}."
                                )

        return super(ProductPurchaseOrder, self).write(vals)


class ProductPurchaseOrderLine(models.Model):
    _name = "idil.product.purchase.order.line"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _description = "Product Purchase Order Line"
    _order = "id desc"

    order_id = fields.Many2one(
        "idil.product.purchase.order", string="Order", ondelete="cascade"
    )
    product_id = fields.Many2one(
        "my_product.product",
        string="Product",
        required=True,
        domain=[("is_cost_manual_purchase", "=", True)],
    )
    quantity = fields.Float(string="Quantity", required=True)
    cost_price = fields.Float(string="Cost Price", digits=(16, 3), required=True)
    amount = fields.Float(string="Total Amount", compute="_compute_amount", store=True)

    @api.depends("quantity", "cost_price")
    def _compute_amount(self):
        for rec in self:
            rec.amount = rec.quantity * rec.cost_price

    @api.onchange("product_id")
    def _onchange_product_id_set_cost(self):
        if self.product_id:
            self.cost_price = self.product_id.cost

    @api.model
    def create(self, values):
        record = super().create(values)
        record.book_product_purchase_transaction()
        return record

    def book_product_purchase_transaction(self):

        try:
            with self.env.cr.savepoint():
                for line in self:
                    product = line.product_id
                    order = line.order_id

                    # Validate
                    if not product.asset_account_id:
                        raise ValidationError("Inventory account not set for product.")

                    # Generate transaction number
                    transaction_number = self.env[
                        "idil.transaction_booking"
                    ]._get_next_transaction_number()

                    trx_source = self.env["idil.transaction.source"].search(
                        [("name", "=", "Purchase Products")], limit=1
                    )
                    if not trx_source:
                        raise ValidationError(
                            'Transaction source "Purchase Prodcuts" not found.'
                        )

                    # Determine which account to use for the credit line
                    if order.payment_method == "cash":
                        cash_account = order.account_number

                        if not cash_account:
                            raise ValidationError("No cash account selected.")

                        # Calculate current balance for this cash account
                        transaction_lines = self.env[
                            "idil.transaction_bookingline"
                        ].search([("account_number", "=", cash_account.id)])

                        debit_total = sum(line.dr_amount for line in transaction_lines)
                        credit_total = sum(line.cr_amount for line in transaction_lines)
                        balance = debit_total - credit_total

                        if balance < line.amount:
                            raise ValidationError(
                                f"Insufficient balance in cash account '{cash_account.name}'.\n"
                                f"Current balance: {balance}, Required: {line.amount}"
                            )

                        credit_account_id = cash_account
                    else:
                        if not order.vendor_id.account_payable_id:
                            raise ValidationError(
                                "Vendor does not have an A/P account configured."
                            )
                        credit_account_id = order.vendor_id.account_payable_id

                    # Get debit and credit accounts
                    debit_account = product.asset_account_id
                    credit_account = (
                        credit_account_id  # already determined based on payment method
                    )

                    # Ensure both accounts have the same currency
                    if debit_account.currency_id.id != credit_account.currency_id.id:
                        raise ValidationError(
                            f"Currency mismatch:\n"
                            f"Debit Account '{debit_account.name}' is in {debit_account.currency_id.name},\n"
                            f"Credit Account '{credit_account.name}' is in {credit_account.currency_id.name}.\n"
                            f"Both must be the same to proceed."
                        )

                    # Create transaction booking
                    booking = self.env["idil.transaction_booking"].create(
                        {
                            "transaction_number": transaction_number,
                            "reffno": order.name,
                            "product_purchase_order_id": order.id,
                            "trx_source_id": trx_source.id,
                            "vendor_id": order.vendor_id.id,
                            "payment_method": order.payment_method,
                            "trx_date": order.purchase_date,
                            "amount": line.amount,
                            "amount_paid": (
                                line.amount if order.payment_method == "cash" else 0
                            ),
                            "remaining_amount": (
                                0 if order.payment_method == "cash" else line.amount
                            ),
                            "payment_status": (
                                "paid" if order.payment_method == "cash" else "pending"
                            ),
                        }
                    )

                    # Create debit line (Inventory asset)
                    self.env["idil.transaction_bookingline"].create(
                        {
                            "transaction_booking_id": booking.id,
                            "product_purchase_order_id": order.id,
                            "transaction_type": "dr",
                            "dr_amount": line.amount,
                            "cr_amount": 0,
                            "account_number": product.asset_account_id.id,
                            "product_id": product.id,
                            "transaction_date": order.purchase_date,
                            "company_id": self.env.company.id,
                        }
                    )

                    # Create credit line (Cash or A/P)
                    self.env["idil.transaction_bookingline"].create(
                        {
                            "transaction_booking_id": booking.id,
                            "product_purchase_order_id": order.id,
                            "transaction_type": "cr",
                            "dr_amount": 0,
                            "cr_amount": line.amount,
                            "account_number": credit_account_id.id,
                            "product_id": product.id,
                            "transaction_date": order.purchase_date,
                            "company_id": self.env.company.id,
                        }
                    )

                    # Book vendor transaction if payment is A/P
                    if order.payment_method == "ap":
                        self.env["idil.vendor_transaction"].create(
                            {
                                "product_purchase_order_id": order.id,
                                "transaction_number": transaction_number,
                                "transaction_date": order.purchase_date,
                                "vendor_id": order.vendor_id.id,
                                "amount": line.amount,
                                "remaining_amount": line.amount,
                                "paid_amount": 0,
                                "payment_method": "ap",
                                "reffno": order.name,
                                "transaction_booking_id": booking.id,
                                "payment_status": "pending",
                            }
                        )
                    # Create product movement record
                    self.env["idil.product.movement"].create(
                        {
                            "product_id": line.product_id.id,
                            "movement_type": "in",
                            "product_purchase_order_id": order.id,
                            "quantity": line.quantity,
                            "source_document": "vendor",
                            "destination": "Inventory",
                            "vendor_id": order.vendor_id.id,
                            "related_document": f"idil.product.purchase.order.line,{line.id}",
                            "transaction_number": transaction_number,
                            "date": order.purchase_date,
                            "source_document": order.name,
                        }
                    )

                    product.stock_quantity += (
                        line.quantity
                    )  # ✅ Increase stock quantity
        except Exception as e:
            logger.error(f"transaction failed: {str(e)}")
            raise ValidationError(f"Transaction failed: {str(e)}")

    def write(self, vals):
        try:
            with self.env.cr.savepoint():
                for record in self:
                    # ✅   # Check if this line is referenced in any purchase return (that is not cancelled)
                    related_returns = self.env["idil.product.purchase_return"].search(
                        [
                            ("return_lines.order_line_id", "=", record.id),
                            ("state", "!=", "cancel"),
                        ]
                    )

                    if related_returns:
                        return_info = "\n".join(
                            f"- Return: {ret.name}, Date: {ret.return_date}"
                            for ret in related_returns
                        )
                        raise ValidationError(
                            f"Cannot update this purchase line because it is referenced in the following Purchase Return(s):\n\n"
                            f"{return_info}\n\n"
                            "To update this line, please delete the related purchase return(s) first."
                        )

                    old_quantity = record.quantity
                    old_cost = record.cost_price
                    # Get new quantity (if changed)
                    new_quantity = vals.get("quantity", old_quantity)
                    quantity_diff = new_quantity - old_quantity

                    # Validate against negative stock
                    if quantity_diff < 0:
                        available_stock = record.product_id.stock_quantity
                        if available_stock < abs(quantity_diff):
                            raise ValidationError(
                                f"Cannot reduce quantity. Available stock for product '{record.product_id.name}' is "
                                f"{available_stock}, but the change requires removing {abs(quantity_diff)} units."
                            )

                    res = super(ProductPurchaseOrderLine, record).write(vals)

                    # Update stock quantity
                    new_quantity = vals.get("quantity", old_quantity)
                    quantity_diff = new_quantity - old_quantity
                    if quantity_diff != 0:
                        record.product_id.stock_quantity += quantity_diff

                    # Update related transaction_bookingline(s)
                    related_lines = self.env["idil.transaction_bookingline"].search(
                        [
                            ("product_purchase_order_id", "=", record.order_id.id),
                            ("product_id", "=", record.product_id.id),
                        ]
                    )
                    for line in related_lines:
                        if line.transaction_type == "dr":
                            line.dr_amount = record.amount
                        elif line.transaction_type == "cr":
                            line.cr_amount = record.amount

                    # Update main booking amount
                    transaction = self.env["idil.transaction_booking"].search(
                        [("product_purchase_order_id", "=", record.order_id.id)],
                        limit=1,
                    )
                    # Update vendor_transaction (if exists)
                    vendor_transaction = self.env["idil.vendor_transaction"].search(
                        [("product_purchase_order_id", "=", record.order_id.id)],
                        limit=1,
                    )

                    prev_paid = vendor_transaction.paid_amount or 0.0
                    if transaction:
                        if self.order_id.payment_method == "ap":
                            transaction.write(
                                {
                                    "amount": record.amount,
                                    "remaining_amount": record.amount - prev_paid,
                                    "amount_paid": prev_paid,
                                }
                            )
                        elif self.order_id.payment_method == "cash":
                            transaction.write(
                                {
                                    "amount": record.amount,
                                    "paid_amount": record.amount,
                                    "remaining_amount": 0,
                                }
                            )

                    if vendor_transaction:
                        if self.order_id.payment_method == "ap":
                            vendor_transaction.amount = record.amount
                            vendor_transaction.remaining_amount = (
                                record.amount - prev_paid
                            )
                            vendor_transaction.paid_amount = prev_paid
                        elif self.order_id.payment_method == "cash":
                            vendor_transaction.amount = record.amount
                            vendor_transaction.paid_amount = record.amount
                            vendor_transaction.remaining_amount = 0

                    # Update product movement
                    movement = self.env["idil.product.movement"].search(
                        [
                            ("product_purchase_order_id", "=", record.order_id.id),
                            ("product_id", "=", record.product_id.id),
                            ("movement_type", "=", "in"),
                        ],
                        limit=1,
                    )
                    if movement:
                        movement.quantity = new_quantity
                        movement.date = fields.Datetime.now()
                    else:
                        self.env["idil.product.movement"].create(
                            {
                                "product_id": record.product_id.id,
                                "movement_type": "in",
                                "product_purchase_order_id": record.order_id.id,
                                "quantity": new_quantity,
                                "date": fields.Datetime.now(),
                                "source_document": record.order_id.name,
                            }
                        )

                return res
        except Exception as e:
            logger.error(f"transaction failed: {str(e)}")
            raise ValidationError(f"Transaction failed: {str(e)}")

    def unlink(self):
        try:
            with self.env.cr.savepoint():
                for record in self:
                    # Check if product has enough stock to reverse
                    available_qty = record.product_id.stock_quantity
                    if available_qty < record.quantity:
                        raise ValidationError(
                            f"Cannot delete line for product '{record.product_id.name}' because "
                            f"only {available_qty} units are in stock, but {record.quantity} are required to reverse."
                        )

                    # 1. Decrease stock
                    record.product_id.stock_quantity -= record.quantity

                    # 2. Delete related transaction_bookingline(s)
                    related_lines = self.env["idil.transaction_bookingline"].search(
                        [
                            ("product_purchase_order_id", "=", record.order_id.id),
                            ("product_id", "=", record.product_id.id),
                        ]
                    )
                    related_lines.unlink()

                    # 3. Check and delete transaction_booking if no other lines exist
                    other_lines = self.env["idil.product.purchase.order.line"].search(
                        [("order_id", "=", record.order_id.id), ("id", "!=", record.id)]
                    )
                    if not other_lines:
                        # Delete transaction_booking
                        transaction = self.env["idil.transaction_booking"].search(
                            [("product_purchase_order_id", "=", record.order_id.id)]
                        )
                        transaction.unlink()

                        # Delete vendor_transaction
                        vendor_transaction = self.env["idil.vendor_transaction"].search(
                            [("product_purchase_order_id", "=", record.order_id.id)]
                        )
                        vendor_transaction.unlink()

                    # 4. Delete product movement
                    movement = self.env["idil.product.movement"].search(
                        [
                            ("product_purchase_order_id", "=", record.order_id.id),
                            ("product_id", "=", record.product_id.id),
                            ("movement_type", "=", "in"),
                        ]
                    )
                    movement.unlink()

                return super(ProductPurchaseOrderLine, self).unlink()
        except Exception as e:
            logger.error(f"transaction failed: {str(e)}")
            raise ValidationError(f"Transaction failed: {str(e)}")
