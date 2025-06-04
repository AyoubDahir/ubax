from odoo import models, fields, api
from odoo.exceptions import ValidationError


class StockAdjustment(models.Model):
    _name = "idil.stock.adjustment"
    _description = "Stock Adjustment"

    item_id = fields.Many2one(
        "idil.item", string="Item", required=True, help="Select the item to adjust"
    )
    adjustment_qty = fields.Float(
        string="Adjustment Quantity", required=True, help="Enter the quantity to adjust"
    )
    adjustment_type = fields.Selection(
        [("decrease", "Decrease")],
        string="Adjustment Type",
        required=True,
        help="Select adjustment type",
    )
    adjustment_date = fields.Date(
        string="Adjustment Date", default=fields.Date.today, required=True
    )
    reason = fields.Text(
        string="Reason for Adjustment", help="Reason for the adjustment"
    )
    cost_price = fields.Float(
        string="Cost Price",
        related="item_id.cost_price",
        store=True,
        readonly=True,
        help="Cost price of the item being adjusted",
    )

    @api.model
    def create(self, vals):
        """Override the create method to adjust item quantity and log item movement."""
        adjustment = super(StockAdjustment, self).create(vals)
        item = adjustment.item_id

        # Calculate new quantity based on the adjustment type

        if adjustment.adjustment_type == "decrease":
            if item.quantity < adjustment.adjustment_qty:
                raise ValidationError("Cannot decrease quantity below zero.")
            new_quantity = item.quantity - adjustment.adjustment_qty
            movement_type = "out"
            # Update the item's quantity using context to prevent triggering other actions
            item.with_context(update_transaction_booking=False).write(
                {"quantity": new_quantity}
            )

        # Fetch the transaction source ID for stock adjustments
        trx_source = self.env["idil.transaction.source"].search(
            [("name", "=", "stock_adjustments")], limit=1
        )

        # Book the main transaction
        transaction = self.env["idil.transaction_booking"].create(
            {
                "reffno": "Stock Adjustments%s"
                % adjustment.id,  # Corrected reference number generation
                "trx_date": adjustment.adjustment_date,
                "amount": abs(adjustment.adjustment_qty * adjustment.cost_price),
                "trx_source_id": (
                    trx_source.id if trx_source else False
                ),  # Assign the source ID if found
            }
        )

        # Create booking lines for the transaction
        self.env["idil.transaction_bookingline"].create(
            [
                {
                    "transaction_booking_id": transaction.id,
                    "description": "Stock Adjustment Debit",
                    "item_id": item.id,
                    "account_number": item.adjustment_account_id.id,
                    "transaction_type": "dr",
                    "dr_amount": adjustment.adjustment_qty
                    * item.cost_price,  # Use cost price for debit amount
                    "cr_amount": 0,  # Use cost price for credit amount
                    "transaction_date": adjustment.adjustment_date,
                },
                {
                    "transaction_booking_id": transaction.id,
                    "description": "Stock Adjustment Credit",
                    "item_id": item.id,
                    "account_number": item.asset_account_id.id,
                    "transaction_type": "cr",
                    "cr_amount": adjustment.adjustment_qty
                    * item.cost_price,  # Use cost price for credit amount
                    "dr_amount": 0,  # Use cost price for debit amount
                    "transaction_date": adjustment.adjustment_date,
                },
            ]
        )
        # Corrected creation of item movement
        self.env["idil.item.movement"].create(
            {
                "item_id": item.id,
                "date": adjustment.adjustment_date,
                "quantity": adjustment.adjustment_qty * -1,
                "source": "Stock Adjustment",
                "destination": item.name,
                "movement_type": movement_type,
                "related_document": "idil.stock.adjustment,%d"
                % adjustment.id,  # Corrected value format
                "transaction_number": transaction.id or "/",
            }
        )

        return adjustment

    def write(self, vals):
        for record in self:
            old_qty = record.adjustment_qty
            new_qty = vals.get("adjustment_qty", old_qty)
            difference = new_qty - old_qty

            if difference == 0 and not any(
                k in vals for k in ["adjustment_date", "cost_price"]
            ):
                return super(StockAdjustment, self).write(vals)

            item = record.item_id
            cost_price = item.cost_price
            adjustment_date = vals.get("adjustment_date", record.adjustment_date)

            # Update item quantity based on difference
            new_item_qty = item.quantity
            if difference < 0:  # Increase
                if item.quantity < abs(difference):
                    raise ValidationError("Cannot decrease quantity below zero.")
                new_item_qty = item.quantity + abs(difference)
            elif difference > 0:  # Decrease
                new_item_qty = item.quantity - abs(difference)

            item.with_context(update_transaction_booking=False).write(
                {"quantity": new_item_qty}
            )

            # Update transaction and lines
            transaction = self.env["idil.transaction_booking"].search(
                [("reffno", "=", "Stock Adjustments%s" % record.id)], limit=1
            )

            if transaction:
                transaction.write(
                    {
                        "amount": abs(new_qty * cost_price),
                        "trx_date": adjustment_date,
                    }
                )

                for line in transaction.booking_lines:
                    if line.transaction_type == "dr":
                        line.write(
                            {
                                "dr_amount": new_qty * cost_price,
                                "transaction_date": adjustment_date,
                            }
                        )
                    elif line.transaction_type == "cr":
                        line.write(
                            {
                                "cr_amount": new_qty * cost_price,
                                "transaction_date": adjustment_date,
                            }
                        )

            # Update item movement
            movement = self.env["idil.item.movement"].search(
                [("related_document", "=", "idil.stock.adjustment,%d" % record.id)],
                limit=1,
            )

            if movement:
                movement.write(
                    {
                        "quantity": new_qty * -1,
                        "date": adjustment_date,
                    }
                )

        return super(StockAdjustment, self).write(vals)

    def unlink(self):
        for record in self:
            item = record.item_id

            # Revert the stock quantity
            if record.adjustment_type == "decrease":
                new_qty = item.quantity + record.adjustment_qty
                item.with_context(update_transaction_booking=False).write(
                    {"quantity": new_qty}
                )

            # Delete related transaction and booking lines
            transaction = self.env["idil.transaction_booking"].search(
                [("reffno", "=", "Stock Adjustments%s" % record.id)], limit=1
            )

            if transaction:
                transaction.booking_lines.unlink()
                transaction.unlink()

            # Delete related item movement
            movement = self.env["idil.item.movement"].search(
                [("related_document", "=", "idil.stock.adjustment,%d" % record.id)],
                limit=1,
            )

            if movement:
                movement.unlink()

        return super(StockAdjustment, self).unlink()
