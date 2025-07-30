from asyncio.log import logger
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError
from odoo.exceptions import UserError


class ProductAdjustment(models.Model):
    _name = "idil.product.adjustment"
    _description = "Product Adjustment"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "id desc"

    product_id = fields.Many2one("my_product.product", string="Product", required=True)
    adjustment_date = fields.Datetime(
        string="Adjustment Date", default=fields.Datetime.now, required=True
    )
    previous_quantity = fields.Float(
        string="Previous Quantity", readonly=True, store=True
    )
    # Disposal Quantity
    new_quantity = fields.Float(
        string="Disposal Quantity", required=True, digits=(16, 6)
    )

    cost_price = fields.Float(
        string="Product Cost Price", readonly=True, store=True, digits=(16, 6)
    )
    adjustment_amount = fields.Float(
        string="Adjustment Amount",
        compute="_compute_adjustment_amount",
        store=True,
        digits=(16, 4),
    )
    old_cost_price = fields.Float(
        string="Total Cost Price", readonly=True, store=True, digits=(16, 6)
    )

    reason_id = fields.Many2one(
        "idil.product.adjustment.reason",
        string="Adjustment Reason",
        required=True,
    )
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

    @api.depends("new_quantity", "previous_quantity", "cost_price", "product_id")
    def _compute_adjustment_amount(self):
        for rec in self:
            bom_currency = (
                self.product_id.bom_id.currency_id
                if self.product_id.bom_id
                else self.product_id.currency_id
            )

            if bom_currency.name == "USD":
                rec.adjustment_amount = (
                    abs(rec.new_quantity * rec.cost_price) * self.rate
                )
            else:
                rec.adjustment_amount = abs(rec.new_quantity * rec.cost_price)

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

    def _apply_adjustment(self):
        try:
            with self.env.cr.savepoint():
                for rec in self:
                    # 🔒 Check if exchange rate is set and valid
                    if rec.currency_id.name == "SL" and (
                        not rec.rate or rec.rate == 0.0
                    ):
                        raise ValidationError(
                            _(
                                "Exchange rate for USD is missing or zero. "
                                "Please insert the correct rate for today before proceeding."
                            )
                        )

                    # Enforce: new_quantity must be LESS than previous_quantity
                    if rec.new_quantity > rec.previous_quantity:
                        raise UserError(
                            _(
                                "Invalid adjustment: Disposal Quantity (%s) must be less than Previous Quantity (%s). Stock increase is not allowed."
                            )
                            % (rec.new_quantity, rec.previous_quantity)
                        )

                    difference = rec.new_quantity
                    if difference == 0:
                        return

                    # SL exchange rate
                    amount = abs(difference) * rec.cost_price * rec.rate

                    # Search for transaction source ID using "Receipt"
                    trx_source = self.env["idil.transaction.source"].search(
                        [("name", "=", "Product Adjustment")], limit=1
                    )
                    if not trx_source:
                        raise UserError(
                            "Transaction source 'Product Adjustment' not found."
                        )

                    # Update product stock quantity
                    # rec.product_id.stock_quantity = (
                    #     rec.product_id.stock_quantity - difference
                    # )
                    # Update stock

                    # Validate currency match between asset and adjustment accounts
                    asset_currency = rec.product_id.asset_account_id.currency_id
                    adjustment_currency = (
                        rec.product_id.account_adjustment_id.currency_id
                    )

                    if asset_currency.id != adjustment_currency.id:
                        raise ValidationError(
                            _(
                                "Mismatch in account currencies:\n- Asset Account: %s\n- Adjustment Account: %s\nCurrencies must be the same to proceed."
                            )
                            % (
                                asset_currency.name or "Undefined",
                                adjustment_currency.name or "Undefined",
                            )
                        )

                    # Create a transaction booking
                    # Create a transaction booking for the adjustment
                    transaction_booking = self.env["idil.transaction_booking"].create(
                        {
                            "trx_source_id": trx_source.id,  # assuming you're linking to this adjustment as source
                            "payment_method": "other",
                            "adjustment_id": rec.id,
                            "payment_status": "paid",
                            "trx_date": rec.adjustment_date,
                            "amount": rec.adjustment_amount,
                        }
                    )

                    # Accounting entries
                    self.env["idil.transaction_bookingline"].create(
                        {
                            "transaction_date": rec.adjustment_date,
                            "adjustment_id": rec.id,
                            "product_id": rec.product_id.id,
                            "transaction_booking_id": transaction_booking.id,
                            "description": f"Stock Adjustment: {rec.product_id.name} ({rec.reason_id or ''})",
                            "transaction_type": "dr",
                            "dr_amount": 0.0,
                            "cr_amount": rec.adjustment_amount,
                            "account_number": rec.product_id.asset_account_id.id,
                        }
                    )

                    self.env["idil.transaction_bookingline"].create(
                        {
                            "transaction_date": rec.adjustment_date,
                            "adjustment_id": rec.id,
                            "product_id": rec.product_id.id,
                            "transaction_booking_id": transaction_booking.id,
                            "description": f"Stock Adjustment: {rec.product_id.name} ({rec.reason_id or ''})",
                            "transaction_type": "cr",
                            "dr_amount": rec.adjustment_amount,
                            "cr_amount": 0.0,
                            "account_number": rec.product_id.account_adjustment_id.id,
                        }
                    )

                    # Product movement log
                    self.env["idil.product.movement"].create(
                        {
                            "product_id": rec.product_id.id,
                            "adjustment_id": rec.id,
                            "movement_type": "out",
                            "quantity": difference * -1,
                            "date": rec.adjustment_date,
                            "source_document": f"Product Manual Adjustment - Reason : {rec.reason_id} Adjusmrent Date :- {rec.adjustment_date}",
                        }
                    )
        except Exception as e:
            logger.error(f"transaction failed: {str(e)}")
            raise ValidationError(f"Transaction failed: {str(e)}")

    def write(self, vals):
        try:
            with self.env.cr.savepoint():
                for rec in self:
                    product = rec.product_id

                    # Allow product_id change
                    if "product_id" in vals:
                        product = self.env["my_product.product"].browse(
                            vals["product_id"]
                        )
                    if product:
                        vals["previous_quantity"] = product.stock_quantity
                        vals["cost_price"] = product.cost
                        vals["old_cost_price"] = product.stock_quantity * product.cost

                # Perform the write
                res = super().write(vals)

                for rec in self:
                    product = rec.product_id

                    # Get previous movement for this adjustment
                    movement = self.env["idil.product.movement"].search(
                        [("adjustment_id", "=", rec.id)], limit=1
                    )
                    old_qty = abs(movement.quantity) if movement else 0.0

                    new_qty = rec.new_quantity
                    qty_diff = (
                        old_qty - new_qty
                    )  # +ve = return to stock, -ve = reduce more

                    # Calculate new product stock
                    new_stock_qty = product.stock_quantity + qty_diff
                    if new_stock_qty < 0:
                        raise UserError(
                            "Stock adjustment would result in negative stock."
                        )

                    # Update product stock
                    # product.stock_quantity = new_stock_qty

                    # Recalculate amount
                    amount = abs(new_qty) * rec.cost_price * rec.rate

                    trx_source = self.env["idil.transaction.source"].search(
                        [("name", "=", "Product Adjustment")], limit=1
                    )
                    if not trx_source:
                        raise UserError(
                            "Transaction source 'Product Adjustment' not found."
                        )

                    asset_currency = product.asset_account_id.currency_id
                    adjustment_currency = product.account_adjustment_id.currency_id

                    if asset_currency.id != adjustment_currency.id:
                        raise ValidationError(
                            _(
                                "Mismatch in account currencies:\n- Asset Account: %s\n- Adjustment Account: %s\nCurrencies must be the same to proceed."
                            )
                            % (
                                asset_currency.name or "Undefined",
                                adjustment_currency.name or "Undefined",
                            )
                        )

                    # Update or create transaction booking
                    booking = self.env["idil.transaction_booking"].search(
                        [("adjustment_id", "=", rec.id)], limit=1
                    )
                    if booking:
                        booking.write(
                            {
                                "trx_date": rec.adjustment_date,
                                "amount": rec.adjustment_amount,
                            }
                        )
                    else:
                        booking = self.env["idil.transaction_booking"].create(
                            {
                                "trx_source_id": trx_source.id,
                                "payment_method": "other",
                                "payment_status": "paid",
                                "trx_date": rec.adjustment_date,
                                "amount": rec.adjustment_amount,
                                "adjustment_id": rec.id,
                            }
                        )

                    desc = f"Stock Adjustment: {product.name} ({rec.reason_id or ''})"

                    # Update or create transaction booking lines
                    lines = self.env["idil.transaction_bookingline"].search(
                        [
                            ("adjustment_id", "=", rec.id),
                            ("product_id", "=", product.id),
                        ]
                    )
                    if lines:
                        for line in lines:
                            if line.transaction_type == "dr":
                                line.write(
                                    {
                                        "transaction_date": self.adjustment_date,
                                        "dr_amount": 0.0,
                                        "cr_amount": rec.adjustment_amount,
                                        "description": desc,
                                        "account_number": product.asset_account_id.id,
                                    }
                                )
                            elif line.transaction_type == "cr":
                                line.write(
                                    {
                                        "transaction_date": self.adjustment_date,
                                        "dr_amount": rec.adjustment_amount,
                                        "cr_amount": 0.0,
                                        "description": desc,
                                        "account_number": product.account_adjustment_id.id,
                                    }
                                )
                    else:
                        self.env["idil.transaction_bookingline"].create(
                            {
                                "transaction_date": rec.adjustment_date,
                                "transaction_booking_id": booking.id,
                                "description": desc,
                                "product_id": rec.product_id.id,
                                "transaction_type": "dr",
                                "dr_amount": 0.0,
                                "cr_amount": rec.adjustment_amount,
                                "account_number": product.asset_account_id.id,
                                "adjustment_id": rec.id,
                            }
                        )
                        self.env["idil.transaction_bookingline"].create(
                            {
                                "transaction_date": rec.adjustment_date,
                                "transaction_booking_id": booking.id,
                                "description": desc,
                                "product_id": rec.product_id.id,
                                "transaction_type": "cr",
                                "dr_amount": rec.adjustment_amount,
                                "cr_amount": 0.0,
                                "account_number": product.account_adjustment_id.id,
                                "adjustment_id": rec.id,
                            }
                        )

                    # Update or create product movement
                    move_vals = {
                        "product_id": product.id,
                        "movement_type": "out",
                        "quantity": -1 * new_qty,
                        "date": rec.adjustment_date,
                        "source_document": f"Product Manual Adjustment - Reason : {rec.reason_id} Adjustment Date :- {rec.adjustment_date}",
                        "adjustment_id": rec.id,
                    }
                    if movement:
                        movement.write(move_vals)
                    else:
                        self.env["idil.product.movement"].create(move_vals)

                return res
        except Exception as e:
            logger.error(f"transaction failed: {str(e)}")
            raise ValidationError(f"Transaction failed: {str(e)}")

    def unlink(self):
        try:
            with self.env.cr.savepoint():
                for rec in self:
                    # Step 1: Restore the stock by reversing the disposal quantity
                    movement = self.env["idil.product.movement"].search(
                        [("adjustment_id", "=", rec.id)], limit=1
                    )
                    if movement:
                        restored_qty = abs(
                            movement.quantity
                        )  # movement.quantity is negative
                        # rec.product_id.stock_quantity += restored_qty

                    # Step 2: Delete movement record(s)
                    if movement:
                        movement.unlink()

                    # Step 3: Delete booking lines
                    booking_lines = self.env["idil.transaction_bookingline"].search(
                        [("adjustment_id", "=", rec.id)]
                    )
                    booking_lines.unlink()

                    # Step 4: Delete booking
                    booking = self.env["idil.transaction_booking"].search(
                        [("adjustment_id", "=", rec.id)], limit=1
                    )
                    if booking:
                        booking.unlink()

                return super(ProductAdjustment, self).unlink()

        except Exception as e:
            logger.error(f"transaction failed: {str(e)}")
            raise ValidationError(f"Transaction failed: {str(e)}")


class ProductAdjustmentReason(models.Model):
    _name = "idil.product.adjustment.reason"
    _description = "Product Adjustment Reason"
    _order = "name"

    name = fields.Char(string="Reason", required=True, translate=True)
