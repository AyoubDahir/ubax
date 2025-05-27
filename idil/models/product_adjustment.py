from odoo import models, fields, api, _
from odoo.exceptions import ValidationError
from odoo.exceptions import UserError


class ProductAdjustment(models.Model):
    _name = "idil.product.adjustment"
    _description = "Product Adjustment"
    _inherit = ["mail.thread", "mail.activity.mixin"]

    product_id = fields.Many2one("my_product.product", string="Product", required=True)
    adjustment_date = fields.Datetime(
        string="Adjustment Date", default=fields.Datetime.now, required=True
    )
    previous_quantity = fields.Float(
        string="Previous Quantity", readonly=True, store=True
    )
    new_quantity = fields.Float(string="New Quantity", required=True, digits=(16, 4))
    cost_price = fields.Float(
        string="Current Cost Price", readonly=True, store=True, digits=(16, 4)
    )
    adjustment_amount = fields.Float(
        string="Adjustment Value",
        compute="_compute_adjustment_amount",
        store=True,
        digits=(16, 4),
    )
    old_cost_price = fields.Float(
        string="Old Cost Price", readonly=True, store=True, digits=(16, 4)
    )
    reason = fields.Char(string="Reason", required=True)
    source_document = fields.Char(string="Source Document")
    company_id = fields.Many2one("res.company", default=lambda self: self.env.company)
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

    @api.onchange("product_id")
    def _onchange_product_id(self):
        if self.product_id:
            self.previous_quantity = self.product_id.stock_quantity
            self.cost_price = self.product_id.cost
            self.old_cost_price = self.product_id.stock_quantity * self.product_id.cost

    @api.depends("new_quantity", "previous_quantity", "cost_price")
    def _compute_adjustment_amount(self):
        for rec in self:
            rec.adjustment_amount = (
                abs(rec.new_quantity - rec.previous_quantity) * rec.cost_price
            )

    @api.model
    def create(self, vals):
        product = self.env["my_product.product"].browse(vals.get("product_id"))
        if product:
            vals["previous_quantity"] = product.stock_quantity
            vals["cost_price"] = product.cost
            vals["old_cost_price"] = product.stock_quantity * product.cost

        res = super().create(vals)
        res._apply_adjustment()
        return res

    def write(self, vals):
        for rec in self:
            product = rec.product_id
            if "product_id" in vals:
                product = self.env["my_product.product"].browse(vals["product_id"])
            if product:
                vals["previous_quantity"] = product.stock_quantity
                vals["cost_price"] = product.cost
                vals["old_cost_price"] = product.stock_quantity * product.cost

        res = super().write(vals)
        for rec in self:
            rec._apply_adjustment()
        return res

    def _apply_adjustment(self):
        for rec in self:
            # Enforce: new_quantity must be LESS than previous_quantity
            if rec.new_quantity >= rec.previous_quantity:
                raise UserError(
                    _(
                        "Invalid adjustment: New Quantity (%s) must be less than Previous Quantity (%s). Stock increase is not allowed."
                    )
                    % (rec.new_quantity, rec.previous_quantity)
                )

            difference = rec.previous_quantity - rec.new_quantity
            if difference == 0:
                return

            amount = abs(difference) * rec.cost_price * rec.rate

            # Search for transaction source ID using "Receipt"
            trx_source = self.env["idil.transaction.source"].search(
                [("name", "=", "Receipt")], limit=1
            )
            if not trx_source:
                raise UserError("Transaction source 'Receipt' not found.")

            # Update product stock quantity
            rec.product_id.stock_quantity = rec.product_id.stock_quantity - difference
            # Update stock

            # Create a transaction booking
            # Create a transaction booking for the adjustment
            transaction_booking = self.env["idil.transaction_booking"].create(
                {
                    "trx_source_id": trx_source.id,  # assuming you're linking to this adjustment as source
                    "payment_method": "other",
                    "payment_status": "paid",
                    "trx_date": rec.adjustment_date,
                    "amount": amount,
                }
            )

            # Accounting entries
            self.env["idil.transaction_bookingline"].create(
                {
                    "transaction_date": rec.adjustment_date,
                    "transaction_booking_id": transaction_booking.id,
                    "description": f"Stock Adjustment: {rec.product_id.name} ({rec.reason or ''})",
                    "transaction_type": "dr",
                    "dr_amount": 0.0,
                    "cr_amount": amount,
                    "account_number": rec.product_id.asset_account_id.id,
                }
            )

            self.env["idil.transaction_bookingline"].create(
                {
                    "transaction_date": rec.adjustment_date,
                    "transaction_booking_id": transaction_booking.id,
                    "description": f"Stock Adjustment: {rec.product_id.name} ({rec.reason or ''})",
                    "transaction_type": "cr",
                    "dr_amount": amount,
                    "cr_amount": 0.0,
                    "account_number": rec.product_id.account_adjustment_id.id,
                }
            )

            # Product movement log
            self.env["idil.product.movement"].create(
                {
                    "product_id": rec.product_id.id,
                    "movement_type": "out",
                    "quantity": difference * -1,
                    "date": rec.adjustment_date,
                    "source_document": f"Product Manual Adjustment - Reason : {rec.reason} Adjusmrent Date :- {rec.adjustment_date}",
                }
            )
