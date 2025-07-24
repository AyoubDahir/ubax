from odoo import models, fields, api, exceptions
import logging

_logger = logging.getLogger(__name__)


class VendorPayment(models.Model):
    _name = "idil.vendor_payment"
    _description = "Vendor Payment"
    _order = "id desc"

    payment_date = fields.Date(
        string="Payment Date", default=lambda self: fields.Date.today()
    )
    vendor_id = fields.Many2one(
        "idil.vendor.registration", string="Vendor", ondelete="restrict", required=True
    )
    vendor_name = fields.Char(
        related="vendor_id.name", string="Vendor Name", readonly=True
    )
    vendor_phone = fields.Char(
        related="vendor_id.phone", string="Vendor Phone", readonly=True
    )
    vendor_email = fields.Char(
        related="vendor_id.email", string="Vendor Email", readonly=True
    )
    vendor_transaction_id = fields.Many2one(
        "idil.vendor_transaction", string="Vendor Transaction", ondelete="cascade"
    )

    amount_paid = fields.Float(string="Amount Paid", required=True)

    reffno = fields.Char(
        related="vendor_transaction_id.reffno", string="Reference Number", readonly=True
    )
    bookingline_ids = fields.One2many(
        "idil.transaction_bookingline", "vendor_payment_id", string="Booking Lines"
    )
    cheque_no = fields.Char(string="Cheque No")
    vendor_bulk_payment_id = fields.Many2one(
        "idil.vendor.bulk.payment",
        string="Vendor Bulk Payment",
        ondelete="cascade",  # Ensures deletion of vendor payments when bulk is deleted
    )

    def write(self, vals):
        try:
            with self.env.cr.savepoint():
                for record in self:
                    if "amount_paid" in vals:
                        old_amount_paid = record.amount_paid
                        new_amount_paid = vals["amount_paid"]
                        amount_difference = new_amount_paid - old_amount_paid
                        record._update_related_transaction_booking_lines(
                            new_amount_paid
                        )
                        record._update_related_booking_and_transaction(
                            amount_difference
                        )
                return super(VendorPayment, self).write(vals)
        except Exception as e:
            _logger.error(f"transaction failed: {str(e)}")
            raise exceptions.ValidationError(f"Transaction failed: {str(e)}")

    def _update_related_transaction_booking_lines(self, new_amount_paid):
        for line in self.bookingline_ids:
            if line.transaction_type == "dr":
                line.dr_amount = new_amount_paid
            elif line.transaction_type == "cr":
                line.cr_amount = new_amount_paid

    def _update_related_booking_and_transaction(self, amount_difference):
        transaction_booking = self.vendor_transaction_id.transaction_booking_id
        if transaction_booking:
            updated_paid_amount = transaction_booking.amount_paid + amount_difference
            remaining_amount = transaction_booking.amount - updated_paid_amount
            payment_status = (
                "partial_paid"
                if 0 < updated_paid_amount < transaction_booking.amount
                else ("paid" if remaining_amount == 0 else "pending")
            )
            transaction_booking.write(
                {
                    "amount_paid": updated_paid_amount,
                    "remaining_amount": remaining_amount,
                    "payment_status": payment_status,
                }
            )

            vendor_transaction = self.vendor_transaction_id
            if vendor_transaction:
                vendor_paid_amount = vendor_transaction.paid_amount + amount_difference
                vendor_remaining_amount = vendor_transaction.amount - vendor_paid_amount
                vendor_payment_status = (
                    "partial_paid"
                    if 0 < vendor_paid_amount < vendor_transaction.amount
                    else ("paid" if vendor_remaining_amount == 0 else "pending")
                )
                vendor_transaction.write(
                    {
                        "paid_amount": vendor_paid_amount,
                        "remaining_amount": vendor_remaining_amount,
                        "payment_status": vendor_payment_status,
                    }
                )

    def unlink(self):
        for record in self:
            amount_paid = record.amount_paid
            amount_difference = -amount_paid
            record._update_related_booking_and_transaction(amount_difference)
            record.bookingline_ids.unlink()
        return super(VendorPayment, self).unlink()
