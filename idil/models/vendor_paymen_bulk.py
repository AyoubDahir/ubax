from odoo import models, fields, api, exceptions
import logging

from odoo.exceptions import ValidationError, UserError

_logger = logging.getLogger(__name__)


class VendorBulkPayment(models.Model):
    _name = "idil.vendor.bulk.payment"
    _description = "Vendor Bulk Payment"
    _order = "id desc"

    vendor_id = fields.Many2one(
        "idil.vendor.registration", string="Vendor", required=True
    )
    reffno = fields.Char(string="Reference Number")
    cash_account_id = fields.Many2one(
        "idil.chart.account",
        string="Select Payment Account",
        domain=[("account_type", "in", ["cash", "bank_transfer"])],
        help="Select the cash account for transactions.",
    )
    amount_paying = fields.Float(string="Total Amount Paying", required=True)
    order_ids = fields.One2many(
        "idil.vendor.bulk.payment.line", "payment_id", string="Vendor Orders"
    )
    payment_date = fields.Date(
        string="Payment Date",
        default=fields.Date.context_today,
        required=True,
        tracking=True,
    )
    transaction_booking_ids = fields.One2many(
        "idil.transaction_booking",
        "bulk_payment_id",
        string="Transaction Bookings",
        ondelete="cascade",
    )
    process_status = fields.Selection(
        [("pending", "Pending"), ("processed", "Processed")],
        string="Process Status",
        default="pending",  # Default status is pending
        help="Status of the bulk payment process.",
    )

    @api.constrains("amount_paying", "order_ids")
    def _check_amount_paying(self):
        """Ensures that amount_paying does not exceed the total remaining_amount before saving."""
        for record in self:
            total_remaining_amount = sum(record.order_ids.mapped("remaining_amount"))
            if record.amount_paying > total_remaining_amount:
                raise ValidationError(
                    f"The amount paying ({record.amount_paying}) cannot be greater than the total remaining amount ({total_remaining_amount})."
                )

    @api.constrains("amount_paying", "cash_account_id", "order_ids")
    def _validate_bulk_payment(self):
        """Validate payment constraints before processing."""

        for record in self:
            # Ensure a valid cash account is selected
            if not record.cash_account_id:
                raise ValidationError("A 'Select Payment Account' must be chosen.")

            # Ensure the amount paying is greater than zero
            if record.amount_paying <= 0:
                raise ValidationError(
                    "The 'Total Amount Paying' must be greater than zero."
                )

            # Compute the total remaining amount of all selected orders
            total_remaining_amount = sum(
                line.remaining_amount
                for line in record.order_ids
                if line.remaining_amount
            )

            # Ensure at least one order is selected
            if not record.order_ids:
                raise ValidationError("No vendor orders selected for bulk payment.")

            # Ensure amount_paying does not exceed total remaining amount
            if record.amount_paying > total_remaining_amount:
                raise ValidationError(
                    f"The 'Total Amount Paying' ({record.amount_paying}) cannot exceed the total remaining amount "
                    f"({total_remaining_amount}) of the selected orders."
                )

            _logger.info(
                f"Processing Bulk Payment: Amount Paying = {record.amount_paying}, "
                f"Total Remaining Amount = {total_remaining_amount}"
            )

    @api.onchange("vendor_id")
    def _onchange_vendor_id(self):
        """Populate orders based on the selected vendor."""
        if self.vendor_id:
            orders = self.env["idil.vendor_transaction"].search(
                [
                    ("vendor_id", "=", self.vendor_id.id),
                    ("payment_status", "!=", "paid"),
                ]
            )
            if not orders:
                # If no orders are found, clear existing lines and raise a warning
                self.order_ids = [(5, 0, 0)]  # Clear existing lines
                raise UserError(
                    f"No pending orders for the vendor '{self.vendor_id.name}'."
                )
            else:
                # Populate the order lines with explicit `remaining_amount`
                self.order_ids = [(5, 0, 0)]  # Clear existing lines
                self.order_ids = [
                    (
                        0,
                        0,
                        {
                            "order_id": order.id,
                            "transaction_number": order.transaction_number,
                            "order_number": order.order_number,
                            "reffno": order.reffno,
                            "amount": order.amount,
                            "remaining_amount": order.remaining_amount,  # Ensure this is populated
                            "payment_status": order.payment_status,
                        },
                    )
                    for order in orders
                ]

    def action_process_bulk_payment(self):
        """Processes the bulk payment and updates the status."""
        try:
            with self.env.cr.savepoint():
                for record in self:
                    if record.process_status == "processed":
                        raise exceptions.UserError(
                            "This bulk payment has already been processed."
                        )

                    # Ensure the cash account balance is sufficient
                    vendor_transaction = self.env["idil.vendor_transaction"].search(
                        [("vendor_id", "=", record.vendor_id.id)], limit=1
                    )
                    if not vendor_transaction:
                        raise exceptions.UserError(
                            "No vendor transaction found for this vendor."
                        )

                    has_balance = vendor_transaction._check_cash_account_balance(
                        record.cash_account_id.id, record.amount_paying
                    )

                    if not has_balance:
                        raise exceptions.ValidationError(
                            "The cash account balance is not enough to cover the paid amount."
                        )

                    # Call the existing processing function
                    record.process_bulk_payment()

                    # ✅ Update the status to 'processed' using `sudo()` to bypass restrictions
                    record.sudo().write({"process_status": "processed"})

                    _logger.info(
                        f"Bulk Payment {record.id} has been successfully processed."
                    )

                return True
        except Exception as e:
            _logger.error(f"transaction failed: {str(e)}")
            raise ValidationError(f"Transaction failed: {str(e)}")

    def process_bulk_payment(self):
        """Process payments for the selected vendor transactions based on order number, ensuring correct
        distribution."""

        try:
            with self.env.cr.savepoint():
                remaining_amount = (
                    self.amount_paying
                )  # The total amount available for payment

                for line in self.order_ids:
                    # Find the related vendor transaction using the order number
                    order = self.env["idil.vendor_transaction"].search(
                        [("order_number", "=", line.order_number)], limit=1
                    )

                    if order and remaining_amount > 0:
                        amount_to_pay = min(
                            remaining_amount, order.remaining_amount
                        )  # Ensure we only pay what's needed

                        if amount_to_pay == order.remaining_amount:
                            # Fully pay the order
                            order.write(
                                {
                                    "paid_amount": order.amount,  # Mark full amount as paid
                                    "remaining_amount": 0,  # No due amount
                                    "payment_status": "paid",
                                    # 'transaction_number': self.reffno,  # Store payment transaction number
                                }
                            )
                            line.write(
                                {
                                    # 'transaction_number': self.reffno,
                                    "payment_status": "paid",
                                    "remaining_amount": 0,  # Update bulk payment line remaining amount
                                    "bulk_paid_amount": amount_to_pay,  # ✅ Save paid portion
                                }
                            )
                        else:
                            # Partially pay the order
                            order.write(
                                {
                                    "paid_amount": order.paid_amount
                                    + amount_to_pay,  # Add the paid amount
                                    "remaining_amount": order.remaining_amount
                                    - amount_to_pay,  # Reduce due amount
                                    "payment_status": "partial_paid",
                                    # 'transaction_number': self.reffno,  # Store payment transaction number
                                }
                            )
                            line.write(
                                {
                                    # 'transaction_number': self.reffno,
                                    "payment_status": "partial_paid",
                                    "remaining_amount": order.remaining_amount,
                                    "bulk_paid_amount": amount_to_pay,  # ✅ Save paid portion
                                    # Update bulk payment line remaining amount
                                }
                            )

                        # ---- Update existing transaction_booking if linked ----
                        if order.transaction_booking_id:
                            booking = order.transaction_booking_id
                            updated_paid = booking.amount_paid + amount_to_pay
                            updated_remaining = booking.amount - updated_paid

                            booking.write(
                                {
                                    "amount_paid": updated_paid,
                                    "remaining_amount": updated_remaining,
                                    "payment_status": (
                                        "paid"
                                        if updated_remaining == 0
                                        else "partial_paid"
                                    ),
                                }
                            )

                        remaining_amount -= amount_to_pay  # Reduce the remaining amount by the actual amount paid
                        # ✅ **Create a Vendor Payment Record**
                        self.env["idil.vendor_payment"].create(
                            {
                                "vendor_id": self.vendor_id.id,
                                "vendor_bulk_payment_id": self.id,
                                "vendor_transaction_id": order.id,
                                "amount_paid": amount_to_pay,
                                "cheque_no": self.reffno,
                                "payment_date": self.payment_date,
                            }
                        )

                    if remaining_amount == 0:
                        break  # Stop processing if no more money is left

                # Log any unused remaining amount
                if remaining_amount > 0:
                    _logger.info(
                        f"Remaining amount of {remaining_amount} was not used."
                    )
                else:
                    _logger.info("All funds have been allocated.")

                self.env["idil.transaction_bookingline"].create(
                    {
                        "transaction_booking_id": order.transaction_booking_id.id,
                        "vendor_bulk_payment_id": self.id,
                        "description": f"Bulk payment for Vendor {self.vendor_id.name}",
                        "account_number": self.cash_account_id.id,
                        "transaction_type": "cr",  # Credit transaction
                        "cr_amount": self.amount_paying,  # Use the total amount paying
                        "dr_amount": 0,
                        "transaction_date": self.payment_date,
                    }
                )
                self.env["idil.transaction_bookingline"].create(
                    {
                        "transaction_booking_id": order.transaction_booking_id.id,
                        "vendor_bulk_payment_id": self.id,
                        "description": f"Bulk payment for Vendor {self.vendor_id.name}",
                        "account_number": self.vendor_id.account_payable_id.id,
                        "transaction_type": "dr",  # Credit transaction
                        "dr_amount": self.amount_paying,
                        "cr_amount": 0,  # Use the total amount paying
                        "transaction_date": self.payment_date,
                    }
                )

                # Log any unused remaining amount
                if remaining_amount > 0:
                    _logger.info(
                        f"Remaining amount of {remaining_amount} was not used."
                    )
                else:
                    _logger.info("All funds have been allocated.")
        except Exception as e:
            _logger.error(f"transaction failed: {str(e)}")
            raise ValidationError(f"Transaction failed: {str(e)}")

    def write(self, vals):
        """Prevent modification after saving, except updating status."""
        if self.process_status == "processed" and "process_status" not in vals:
            raise UserError(
                "Modifications are not allowed. Please delete and recreate the record with correct entries."
            )
        return super(VendorBulkPayment, self).write(vals)

    def unlink(self):
        """Revert the effect of payments before deleting the bulk payment record."""
        try:
            with self.env.cr.savepoint():
                for record in self:
                    for line in record.order_ids:
                        order = self.env["idil.vendor_transaction"].search(
                            [("order_number", "=", line.order_number)], limit=1
                        )

                    # Delete related transaction bookings
                    # Delete related vendor payment records
                    self.env["idil.vendor_payment"].search(
                        [("vendor_bulk_payment_id", "=", record.id)]
                    ).unlink()
                    # Delete related transaction bookings before removing bulk payment
                    self.env["idil.transaction_bookingline"].search(
                        [("vendor_bulk_payment_id", "=", record.id)]
                    ).unlink()

                    # Delete related payment lines
                    if record.order_ids:
                        record.order_ids.unlink()

                return super(VendorBulkPayment, self).unlink()
        except Exception as e:
            _logger.error(f"transaction failed: {str(e)}")
            raise ValidationError(f"Transaction failed: {str(e)}")


class VendorBulkPaymentLine(models.Model):
    _name = "idil.vendor.bulk.payment.line"
    _description = "Vendor Bulk Payment Line"
    _order = "id desc"

    payment_id = fields.Many2one("idil.vendor.bulk.payment", string="Payment")
    order_id = fields.Many2one("idil.vendor_transaction", string="Order")
    order_number = fields.Char(string="Order Number")
    transaction_number = fields.Char(string="Transaction Number")
    reffno = fields.Char(string="Reference Number")
    amount = fields.Float(string="Transaction Amount")
    remaining_amount = fields.Float(string="Remaining Amount", store=True)
    payment_status = fields.Selection(
        [("pending", "Pending"), ("partial_paid", "Partial Paid"), ("paid", "Paid")],
        string="Payment Status",
    )
    bulk_paid_amount = fields.Float(
        string="Paid Amount (This Bulk)", required=True, default=0.0
    )

    @api.onchange(
        "order_id",
        "order_number",
        "transaction_number",
        "reffno",
        "amount",
        "remaining_amount",
        "payment_status",
    )
    def _onchange_prevent_modification(self):
        """Prevent users from modifying fields once they are set."""
        # This ensures the record already exists
        raise UserError(
            "Do not try to modify the correct information. Modification is not allowed."
        )
