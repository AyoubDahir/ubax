from asyncio.log import logger
from odoo import models, fields, api
from odoo.exceptions import ValidationError


class PurchaseReturn(models.Model):
    _name = "idil.purchase_return"
    _description = "Purchase Return"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "id desc"

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
        "idil.purchase_order",
        string="Original Order",
        domain="[('vendor_id', '=', vendor_id)]",
        required=True,
    )
    return_date = fields.Date(default=fields.Date.today, string="Return Date")

    return_lines = fields.One2many(
        "idil.purchase_return.line", "return_id", string="Return Lines"
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
        return super(PurchaseReturn, self).create(vals)

    def action_process_return(self):
        try:
            with self.env.cr.savepoint():
                for return_obj in self:
                    if return_obj.state == "confirmed":
                        raise ValidationError(
                            f"Return '{return_obj.name}' has already been confirmed."
                        )
                    for line in return_obj.return_lines:
                        item = line.item_id
                        if item.quantity < line.return_qty:
                            raise ValidationError(
                                f"Cannot return {line.return_qty} of {item.name}. Only {item.quantity} in stock."
                            )

                        item.write({"quantity": item.quantity - line.return_qty})

                        self.env["idil.item.movement"].create(
                            {
                                "item_id": item.id,
                                "purchase_order_line_id": line.order_line_id.id,
                                "vendor_id": return_obj.vendor_id.id,
                                "transaction_number": return_obj.name,
                                "date": fields.Date.today(),
                                "quantity": -line.return_qty,
                                "source": "Inventory",
                                "destination": "Vendor",
                                "movement_type": "out",
                                "related_document": f"idil.purchase_return.line,{line.id}",
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
                    [("name", "=", "Purchase Return")], limit=1
                )
                if not trx_source:
                    raise ValidationError(
                        ('Transaction source "Purchase Return" not found.')
                    )

                trx = self.env["idil.transaction_booking"].create(
                    {
                        "transaction_number": trx_number,
                        "return_id": self.id,
                        "reffno": f"{self.original_order_id.reffno}/RET",
                        "vendor_id": self.vendor_id.id,
                        "trx_source_id": trx_source.id,
                        "purchase_order_id": self.original_order_id.id,
                        "trx_date": fields.Date.today(),
                        "amount": amount,
                        "remaining_amount": 0,
                        "amount_paid": 0,
                        "payment_method": "ap",
                        "payment_status": "posted",
                        "order_number": self.original_order_id.id,
                    }
                )
                # Find all original booking lines for this item/order_line
                original_booking_lines = self.env[
                    "idil.transaction_bookingline"
                ].search(
                    [
                        ("order_line", "=", line.order_line_id.id),
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
                        "return_id": line.return_id.id,
                        "item_id": orig.item_id.id,
                        "description": f"Return of {orig.item_id.name}",
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
                        ("order_number", "=", trx.order_number),
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
                            f"Returning item '{line.item_id.name}' with quantity {line.return_qty} and amount {line.amount:.2f} "
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
                        item = line.item_id
                        item.write({"quantity": item.quantity + line.return_qty})

                        # ✅ 2. Remove item movement
                        self.env["idil.item.movement"].search(
                            [
                                ("purchase_return_id", "=", return_obj.id),
                                ("item_id", "=", line.item_id.id),
                            ]
                        ).unlink()

                        total_amount += line.amount

                    # ✅ 3. Remove transaction booking lines and bookings
                    booking_lines = self.env["idil.transaction_bookingline"].search(
                        [
                            ("return_id", "=", return_obj.id),
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

                return super(PurchaseReturn, self).unlink()
        except Exception as e:
            logger.error(f"transaction failed: {str(e)}")
            raise ValidationError(f"Transaction failed: {str(e)}")


class PurchaseReturnLine(models.Model):
    _name = "idil.purchase_return.line"
    _description = "Purchase Return Line"
    _order = "id desc"

    return_id = fields.Many2one(
        "idil.purchase_return", string="Return", ondelete="cascade"
    )
    order_line_id = fields.Many2one(
        "idil.purchase_order.line", string="Original Line", required=True
    )
    item_id = fields.Many2one(related="order_line_id.item_id", store=True)
    original_qty = fields.Integer(related="order_line_id.quantity", store=True)
    cost_price = fields.Float(
        string="Cost Price",
        related="order_line_id.cost_price",
        store=True,
        readonly=True,
    )
    return_qty = fields.Integer(string="Return Quantity", required=True)
    amount = fields.Float(string="Amount", compute="_compute_return_amount", store=True)

    @api.depends("return_qty", "order_line_id.cost_price")
    def _compute_return_amount(self):
        for line in self:
            line.amount = line.return_qty * line.order_line_id.cost_price

    def write(self, values):
        try:
            with self.env.cr.savepoint():
                for line in self:
                    old_qty = line.return_qty
                    new_qty = values.get("return_qty", old_qty)

                    if new_qty == old_qty:
                        return super(PurchaseReturnLine, self).write(values)

                    item = line.item_id
                    qty_diff = new_qty - old_qty
                    amount_diff = qty_diff * line.cost_price

                    # ↓↓↓ If increasing return quantity
                    if qty_diff > 0:
                        if item.quantity < qty_diff:
                            raise ValidationError(
                                f"Cannot return additional {qty_diff} of {item.name}. Only {item.quantity} available in stock."
                            )
                        item.write({"quantity": item.quantity - qty_diff})

                    # ↓↓↓ If reducing return quantity
                    elif qty_diff < 0:
                        item.write({"quantity": item.quantity + abs(qty_diff)})

                    # Adjust item movement
                    # Adjust item movement by return_id only
                    movement = self.env["idil.item.movement"].search(
                        [
                            ("purchase_return_id", "=", line.return_id.id),
                            ("item_id", "=", line.item_id.id),
                        ],
                        limit=1,
                    )

                    if movement:
                        movement.unlink()

                    self.env["idil.item.movement"].create(
                        {
                            "item_id": line.item_id.id,
                            "purchase_order_line_id": line.order_line_id.id,
                            "purchase_return_id": line.return_id.id,  # Make sure this field exists in your model
                            "date": fields.Date.today(),
                            "quantity": -new_qty,  # replace with updated quantity
                            "source": "Inventory",
                            "destination": "Vendor",
                            "movement_type": "out",
                        }
                    )

                    # Adjust transaction lines
                    # 🔁 Remove existing transaction booking and lines
                    booking_lines = self.env["idil.transaction_bookingline"].search(
                        [
                            ("return_id", "=", line.return_id.id),
                        ]
                    )
                    booking_ids = booking_lines.mapped("transaction_booking_id").ids

                    # First remove transaction lines
                    booking_lines.unlink()

                    # Then remove corresponding bookings
                    self.env["idil.transaction_booking"].browse(booking_ids).unlink()

                    # ✅ Now recreate booking and lines
                    trx_number = self.env[
                        "idil.transaction_booking"
                    ]._get_next_transaction_number()
                    purchase_account = line.order_line_id._validate_purchase_account()
                    stock_account = line.item_id.asset_account_id.id
                    amount = new_qty * line.cost_price

                    trx_source = self.env["idil.transaction.source"].search(
                        [("name", "=", "Purchase Return")], limit=1
                    )
                    if not trx_source:
                        raise ValidationError(
                            "Transaction source 'Purchase Return' not found."
                        )

                    booking = self.env["idil.transaction_booking"].create(
                        {
                            "transaction_number": trx_number,
                            "return_id": line.return_id.id,
                            "reffno": f"{line.return_id.original_order_id.reffno}/RET",
                            "vendor_id": line.return_id.vendor_id.id,
                            "trx_source_id": trx_source.id,
                            "purchase_order_id": line.return_id.original_order_id.id,
                            "trx_date": fields.Date.today(),
                            "amount": amount,
                            "remaining_amount": 0,
                            "amount_paid": 0,
                            "payment_method": "ap",
                            "payment_status": "posted",
                            "order_number": line.return_id.original_order_id.id,
                        }
                    )

                    self.env["idil.transaction_bookingline"].create(
                        [
                            {
                                "order_line": line.order_line_id.id,
                                "return_id": line.return_id.id,
                                "item_id": line.item_id.id,
                                "description": f"Return of {line.item_id.name}",
                                "account_number": stock_account,
                                "transaction_type": "cr",
                                "cr_amount": amount,
                                "transaction_booking_id": booking.id,
                                "transaction_date": fields.Date.today(),
                            },
                            {
                                "order_line": line.order_line_id.id,
                                "return_id": line.return_id.id,
                                "item_id": line.item_id.id,
                                "description": f"Return of {line.item_id.name}",
                                "account_number": purchase_account,
                                "transaction_type": "dr",
                                "dr_amount": amount,
                                "transaction_booking_id": booking.id,
                                "transaction_date": fields.Date.today(),
                            },
                        ]
                    )

                    # ✅ Now update or create vendor transaction (and fix variable name)

                    VendorTransaction = self.env["idil.vendor_transaction"]
                    vendor_tx = VendorTransaction.search(
                        [
                            (
                                "order_number",
                                "=",
                                line.return_id.original_order_id.id,
                            ),
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
                                f"Returning item '{line.item_id.name}' with quantity {line.return_qty} and amount {line.amount:.2f} "
                                f"is not allowed while a payment exists.\n\n"
                                "If you wish to proceed with the return, please first delete the vendor payment from the 'Vendor Payments' menu, "
                                "then retry the return process."
                            )

                    if vendor_tx:
                        updated_amount = vendor_tx.amount - amount_diff
                        updated_remaining = vendor_tx.remaining_amount - amount_diff
                        updated_paid = vendor_tx.paid_amount  # Keep as-is

                        if vendor_tx.payment_method == "cash":
                            updated_paid = vendor_tx.paid_amount - amount_diff
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

                    return super(PurchaseReturnLine, self).write(values)
        except Exception as e:
            logger.error(f"transaction failed: {str(e)}")
            raise ValidationError(f"Transaction failed: {str(e)}")
