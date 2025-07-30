from asyncio.log import logger
from odoo import models, fields, api
from odoo.exceptions import ValidationError


class ProductPurchaseReturn(models.Model):
    _name = "idil.product.purchase_return"
    _description = "Product Purchase Return"
    _inherit = ["mail.thread", "mail.activity.mixin"]

    name = fields.Char(
        string="Return Reference",
        required=True,
        readonly=True,
        copy=False,
        default="New",
    )
    vendor_id = fields.Many2one(
        "idil.vendor.registration",
        string="Vendor",
        required=True,
        tracking=True,
    )

    original_order_id = fields.Many2one(
        "idil.product.purchase.order",
        string="Original Order",
        domain="[('vendor_id', '=', vendor_id)]",
        required=True,
    )
    return_date = fields.Date(default=fields.Date.today, string="Return Date")

    return_lines = fields.One2many(
        "idil.product.purchase_return.line", "return_id", string="Return Lines"
    )
    state = fields.Selection(
        [("draft", "Draft"), ("confirmed", "Confirmed"), ("cancel", "Cancelled")],
        default="draft",
    )

    @api.model
    def create(self, vals):
        if vals.get("name", "New") == "New":
            vals["name"] = (
                self.env["ir.sequence"].next_by_code("idil.purchase.return.seq")
                or "RET/0001"
            )
        return super(ProductPurchaseReturn, self).create(vals)

    def action_process_return(self):
        try:
            with self.env.cr.savepoint():
                for return_obj in self:
                    if return_obj.state == "confirmed":
                        raise ValidationError(
                            f"Return '{return_obj.name}' has already been confirmed."
                        )
                    for line in return_obj.return_lines:
                        product = line.product_id
                        if product.stock_quantity < line.return_qty:
                            raise ValidationError(
                                f"Cannot return {line.return_qty} of {product.name}. Only {product.stock_quantity} in stock."
                            )

                        # product.write(
                        #     {"stock_quantity": product.stock_quantity - line.return_qty}
                        # )

                        self.env["idil.product.movement"].create(
                            {
                                "product_id": product.id,
                                "product_purchase_order_id": line.return_id.original_order_id.id,
                                "vendor_id": return_obj.vendor_id.id,
                                "transaction_number": return_obj.name,
                                "date": fields.Date.today(),
                                "quantity": -line.return_qty,
                                "source_document": "Inventory",
                                "destination": "Vendor",
                                "movement_type": "out",
                                "related_document": f"idil.product.purchase_return.line,{line.id}",
                                "purchase_return_id": return_obj.id,
                            }
                        )

                        self._create_return_transaction(line)
                return_obj.write({"state": "confirmed"})
        except Exception as e:
            logger.error(f"transaction failed: {str(e)}")
            raise ValidationError(f"Transaction failed: {str(e)}")

    def _create_return_transaction(self, line):
        try:
            with self.env.cr.savepoint():
                trx_number = self.env[
                    "idil.transaction_booking"
                ]._get_next_transaction_number()
                amount = line.amount
                trx_source = self.env["idil.transaction.source"].search(
                    [("name", "=", "Product Purchase Return")], limit=1
                )
                if not trx_source:
                    raise ValidationError(
                        ('Transaction source "Purchase Return" not found.')
                    )

                trx = self.env["idil.transaction_booking"].create(
                    {
                        "transaction_number": trx_number,
                        "product_return_id": self.id,
                        "reffno": f"{self.original_order_id.name}/RET",
                        "vendor_id": self.vendor_id.id,
                        "trx_source_id": trx_source.id,
                        "product_purchase_order_id": self.original_order_id.id,
                        "trx_date": fields.Date.today(),
                        "amount": amount,
                        "remaining_amount": 0,
                        "amount_paid": 0,
                        "payment_method": "ap",
                        "payment_status": "posted",
                        "order_number": self.original_order_id.id,
                    }
                )
                # Find all original booking lines for this product/order_line
                original_booking_lines = self.env[
                    "idil.transaction_bookingline"
                ].search(
                    [
                        ("product_purchase_order_id", "=", self.original_order_id.id),
                        ("transaction_type", "in", ["dr", "cr"]),
                        # Optionally: ("transaction_booking_id.purchase_order_id", "=", line.return_id.original_order_id.id),
                    ]
                )

                # For each original booking line, create a reversed one for return
                return_booking_lines = []
                for orig in original_booking_lines:
                    reversed_type = "cr" if orig.transaction_type == "dr" else "dr"
                    reversed_vals = {
                        "order_line": orig.order_line.id,
                        "product_return_id": line.return_id.id,
                        "product_id": orig.product_id.id,
                        "description": f"Return of {orig.product_id.name}",
                        "account_number": orig.account_number.id,
                        "transaction_type": reversed_type,
                        "dr_amount": (amount if reversed_type == "dr" else 0),
                        "cr_amount": (amount if reversed_type == "cr" else 0),
                        "transaction_booking_id": trx.id,  # Use the new transaction booking you just created
                        "transaction_date": fields.Date.today(),
                    }
                    return_booking_lines.append(reversed_vals)

                self.env["idil.transaction_bookingline"].create(return_booking_lines)

                VendorTransaction = self.env["idil.vendor_transaction"]
                vendor_tx = VendorTransaction.search(
                    [
                        ("product_purchase_order_id", "=", self.original_order_id.id),
                    ],
                    limit=1,
                )
                # Check if any payment is linked to that vendor transaction
                if vendor_tx:
                    has_payment = (
                        self.env["idil.vendor_payment"].search_count(
                            [("vendor_transaction_id", "=", vendor_tx.id)]
                        )
                        > 0
                    )

                    if has_payment:
                        raise ValidationError(
                            f"You have already made a payment of {vendor_tx.paid_amount:.2f} to vendor '{vendor_tx.vendor_id.name}'.\n\n"
                            f"Returning product '{line.product_id.name}' with quantity {line.return_qty} and amount {line.amount:.2f} "
                            f"is not allowed while a payment exists.\n\n"
                            "If you wish to proceed with the return, please first delete the vendor payment from the 'Vendor Payments' menu, "
                            "then retry the return process."
                        )

                if vendor_tx:
                    updated_amount = vendor_tx.amount - trx.amount
                    updated_remaining = vendor_tx.remaining_amount - trx.amount
                    updated_paid = vendor_tx.paid_amount  # Keep as-is

                    if vendor_tx.payment_method == "cash":
                        updated_paid = vendor_tx.paid_amount - trx.amount
                        updated_remaining = (
                            0
                            if updated_paid == updated_amount
                            else updated_amount - updated_paid
                        )

                    vendor_tx.write(
                        {
                            "amount": updated_amount,
                            "remaining_amount": updated_remaining,
                            "paid_amount": updated_paid,
                            "payment_status": (
                                "paid"
                                if updated_remaining == 0
                                else "partial_paid" if updated_paid > 0 else "pending"
                            ),
                            "transaction_date": trx.trx_date,
                        }
                    )
        except Exception as e:
            logger.error(f"transaction failed: {str(e)}")
            raise ValidationError(f"Transaction failed: {str(e)}")

    @api.onchange("original_order_id")
    def _onchange_original_order_id(self):
        if self.original_order_id:
            self.return_lines = [(5, 0, 0)]  # Clear existing lines
            self.return_lines = [
                (
                    0,
                    0,
                    {
                        "order_line_id": line.id,
                        "return_qty": 0,
                    },
                )
                for line in self.original_order_id.order_lines
            ]

    @api.onchange("vendor_id")
    def _onchange_vendor_id(self):
        self.original_order_id = False
        self.return_lines = [(5, 0, 0)]

    def unlink(self):
        try:
            with self.env.cr.savepoint():
                for return_obj in self:
                    # Check for any vendor transaction first
                    vendor_tx = self.env["idil.vendor_transaction"].search(
                        [("order_number", "=", return_obj.original_order_id.id)],
                        limit=1,
                    )
                    if vendor_tx:
                        has_payment = (
                            self.env["idil.vendor_payment"].search_count(
                                [("vendor_transaction_id", "=", vendor_tx.id)]
                            )
                            > 0
                        )

                        if has_payment:
                            raise ValidationError(
                                f"A payment of {vendor_tx.paid_amount:.2f} has already been made to vendor '{vendor_tx.vendor_id.name}'.\n"
                                "Cannot delete this purchase return while payment exists.\n"
                                "Please delete the vendor payment first before proceeding."
                            )

                    total_amount = 0

                    for line in return_obj.return_lines:
                        # ✅ 1. Restore stock
                        product = line.product_id
                        # product.write(
                        #     {"stock_quantity": product.stock_quantity + line.return_qty}
                        # )

                        # ✅ 2. Remove product movement
                        self.env["idil.product.movement"].search(
                            [
                                ("purchase_return_id", "=", return_obj.id),
                                ("product_id", "=", line.product_id.id),
                            ]
                        ).unlink()

                        total_amount += line.amount

                    # ✅ 3. Remove transaction booking lines and bookings
                    booking_lines = self.env["idil.transaction_bookingline"].search(
                        [
                            ("product_return_id", "=", return_obj.id),
                        ]
                    )
                    booking_ids = booking_lines.mapped("transaction_booking_id").ids
                    booking_lines.unlink()
                    self.env["idil.transaction_booking"].browse(booking_ids).unlink()

                    # ✅ 4. Adjust vendor transaction (if exists and no payment)

                    if vendor_tx:
                        updated_amount = vendor_tx.amount + total_amount
                        updated_remaining = vendor_tx.remaining_amount + total_amount
                        updated_paid = vendor_tx.paid_amount  # Keep as-is

                        if vendor_tx.payment_method == "cash":
                            updated_paid = vendor_tx.paid_amount - total_amount
                            updated_remaining = (
                                0
                                if updated_paid == updated_amount
                                else updated_amount - updated_paid
                            )

                        vendor_tx.write(
                            {
                                "amount": updated_amount,
                                "remaining_amount": updated_remaining,
                                "paid_amount": updated_paid,
                                "payment_status": (
                                    "paid"
                                    if updated_remaining == 0
                                    else (
                                        "partial_paid"
                                        if updated_paid > 0
                                        else "pending"
                                    )
                                ),
                            }
                        )

                return super(ProductPurchaseReturn, self).unlink()
        except Exception as e:
            logger.error(f"transaction failed: {str(e)}")
            raise ValidationError(f"Transaction failed: {str(e)}")

    def write(self, vals):
        try:
            with self.env.cr.savepoint():
                for return_obj in self:
                    if return_obj.state != "confirmed":
                        return super(ProductPurchaseReturn, self).write(vals)

                    updated_lines = vals.get("return_lines", [])

                    # Prepare existing return line data
                    previous_data = {}
                    for line in return_obj.return_lines:
                        previous_data[line.id] = {
                            "qty": line.return_qty,
                            "amount": line.amount,
                            "product_id": line.product_id.id,
                        }

                    # Proceed with the write
                    res = super(ProductPurchaseReturn, self).write(vals)

                    # Refresh the object after write

                    return_obj = self.browse(return_obj.id)

                    for line in return_obj.return_lines:
                        old = previous_data.get(line.id)
                        if not old:
                            continue  # New line? (you can add logic for new lines too if needed)

                        old_qty = old["qty"]
                        new_qty = line.return_qty
                        qty_diff = new_qty - old_qty

                        if qty_diff == 0:
                            continue  # No change

                        product = line.product_id

                        # Adjust stock
                        # if qty_diff < 0:
                        #     # Reduced return → Return extra stock back
                        #     product.stock_quantity += abs(qty_diff)
                        # else:
                        #     # Increased return → Remove extra stock
                        #     if product.stock_quantity < qty_diff:
                        #         raise ValidationError(
                        #             f"Insufficient stock for {product.name}. Need {qty_diff}, but only {product.stock_quantity} available."
                        #         )
                        #     product.stock_quantity -= qty_diff

                        # Update movement
                        movement = self.env["idil.product.movement"].search(
                            [
                                ("purchase_return_id", "=", return_obj.id),
                                ("product_id", "=", product.id),
                            ],
                            limit=1,
                        )

                        if movement:
                            movement.write({"quantity": -new_qty})
                        else:
                            self.env["idil.product.movement"].create(
                                {
                                    "product_id": product.id,
                                    "product_purchase_order_id": return_obj.original_order_id.id,
                                    "vendor_id": return_obj.vendor_id.id,
                                    "transaction_number": return_obj.name,
                                    "date": fields.Date.today(),
                                    "quantity": -new_qty,
                                    "source_document": "Inventory",
                                    "destination": "Vendor",
                                    "movement_type": "out",
                                    "related_document": f"idil.product.purchase_return.line,{line.id}",
                                    "purchase_return_id": return_obj.id,
                                }
                            )

                        # Update transaction booking & lines
                        booking_lines = self.env["idil.transaction_bookingline"].search(
                            [
                                ("product_return_id", "=", return_obj.id),
                                ("product_id", "=", product.id),
                            ]
                        )
                        total_booking = 0
                        for bl in booking_lines:
                            if bl.transaction_type == "dr":
                                bl.write({"dr_amount": line.amount})
                                total_booking += line.amount
                            elif bl.transaction_type == "cr":
                                bl.write({"cr_amount": line.amount})
                                total_booking += line.amount

                        # Update main booking
                        bookings = booking_lines.mapped("transaction_booking_id")
                        bookings.write(
                            {
                                "amount": line.amount,
                            }
                        )

                        # Update vendor transaction
                        vendor_tx = self.env["idil.vendor_transaction"].search(
                            [
                                (
                                    "product_purchase_order_id",
                                    "=",
                                    return_obj.original_order_id.id,
                                )
                            ],
                            limit=1,
                        )

                        if vendor_tx:
                            diff_amount = line.amount - old["amount"]

                            updated_amount = vendor_tx.amount - diff_amount
                            updated_remaining = vendor_tx.remaining_amount - diff_amount
                            updated_paid = vendor_tx.paid_amount  # usually same

                            if vendor_tx.payment_method == "cash":
                                updated_paid = vendor_tx.paid_amount - diff_amount
                                updated_remaining = (
                                    0
                                    if updated_paid == updated_amount
                                    else updated_amount - updated_paid
                                )

                            vendor_tx.write(
                                {
                                    "amount": updated_amount,
                                    "remaining_amount": updated_remaining,
                                    "paid_amount": updated_paid,
                                    "payment_status": (
                                        "paid"
                                        if updated_remaining == 0
                                        else (
                                            "partial_paid"
                                            if updated_paid > 0
                                            else "pending"
                                        )
                                    ),
                                    "transaction_date": fields.Date.today(),
                                }
                            )

                return res
        except Exception as e:
            logger.error(f"transaction failed: {str(e)}")
            raise ValidationError(f"Transaction failed: {str(e)}")


class ProductPurchaseReturnLine(models.Model):
    _name = "idil.product.purchase_return.line"
    _description = "Purchase Return Line"

    return_id = fields.Many2one(
        "idil.product.purchase_return", string="Return", ondelete="cascade"
    )
    order_line_id = fields.Many2one(
        "idil.product.purchase.order.line",
        string="Original Line",
        required=True,
        store=True,
    )
    product_id = fields.Many2one(related="order_line_id.product_id", store=True)

    original_qty = fields.Float(related="order_line_id.quantity", store=True)

    cost_price = fields.Float(
        string="Cost Price",
        related="order_line_id.cost_price",
        store=True,
        readonly=True,
    )
    return_qty = fields.Integer(string="Return Quantity", required=True)
    amount = fields.Float(string="Amount", compute="_compute_return_amount", store=True)

    @api.depends("return_qty", "cost_price")
    def _compute_return_amount(self):
        for line in self:
            line.amount = line.return_qty * line.cost_price
