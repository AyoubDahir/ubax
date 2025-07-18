from odoo import api, fields, models
from odoo.exceptions import ValidationError


class IdilItemOpeningBalance(models.Model):
    _name = "idil.item.opening.balance"
    _description = "Multi-Item Opening Balance"
    _inherit = ["mail.thread", "mail.activity.mixin"]

    name = fields.Char(string="Reference", readonly=True, default="New")

    date = fields.Date(string="Date", default=fields.Date.today, required=True)
    state = fields.Selection(
        [("draft", "Draft"), ("confirmed", "Confirmed")],
        default="confirmed",
        tracking=True,
    )
    note = fields.Text(string="Note")
    line_ids = fields.One2many(
        "idil.item.opening.balance.line",
        "opening_balance_id",
        string="Items",
        copy=True,
    )
    total_amount = fields.Float(
        string="Total Amount", compute="_compute_total_amount", store=True
    )

    @api.depends("line_ids.total")
    def _compute_total_amount(self):
        for rec in self:
            rec.total_amount = sum(line.total for line in rec.line_ids)

    def action_populate_zero_qty_items(self):
        """
        Fills the line_ids one2many in the UI with all zero-qty items,
        SKIPPING any already-present lines.
        DOES NOT save records to the DB until the main form is saved.
        """
        all_zero_items = self.env["idil.item"].search([("quantity", "=", 0)])
        existing_item_ids = self.line_ids.mapped("item_id").ids
        new_lines = []
        for item in all_zero_items:
            if item.id in existing_item_ids:
                continue
            new_lines.append(
                (
                    0,
                    0,
                    {
                        "item_id": item.id,
                        "quantity": 0,
                        "cost_price": item.cost_price,
                    },
                )
            )
        # Append new zero-qty lines to existing ones
        self.line_ids = (
            list(
                self.line_ids._origin.ids
                and [(4, id) for id in self.line_ids._origin.ids]
                or []
            )
            + new_lines
        )

    # Above: add new, retain any already present (to avoid removing manually entered)

    def confirm_opening_balance(self):
        TransactionBooking = self.env["idil.transaction_booking"]
        TransactionSource = self.env["idil.transaction.source"]
        ItemMovement = self.env["idil.item.movement"]

        EquityAccount = self.env["idil.chart.account"].search(
            [("name", "=", "Opening Balance Account")], limit=1
        )

        source = TransactionSource.search(
            [("name", "=", "Inventory Opening Balance")], limit=1
        )
        if not source:
            raise ValidationError(
                "Transaction Source 'Inventory Opening Balance' not found."
            )

        for line in self.line_ids:
            item = line.item_id

            # Validate stock is not already positive
            if item.quantity != 0:
                raise ValidationError(
                    f"Cannot create opening balance. Item '{item.name}' already has stock: {item.quantity}"
                )

            # Update stock
            item.quantity = line.quantity

            # Create transaction booking
            amount = line.quantity * line.cost_price
            trx = TransactionBooking.create(
                {
                    "transaction_number": self.env["ir.sequence"].next_by_code(
                        "idil.transaction_booking"
                    ),
                    "reffno": item.name,
                    "item_opening_balance_id": self.id,
                    "trx_date": self.date,
                    "amount": amount,
                    "amount_paid": amount,
                    "remaining_amount": 0,
                    "payment_status": "paid",
                    "payment_method": "other",
                    "trx_source_id": source.id,
                }
            )

            # Booking lines
            trx.booking_lines.create(
                [
                    {
                        "transaction_booking_id": trx.id,
                        "item_opening_balance_id": self.id,
                        "description": f"Opening Balance for {item.name}",
                        "item_id": item.id,
                        "account_number": item.asset_account_id.id,
                        "transaction_type": "dr",
                        "dr_amount": amount,
                        "cr_amount": 0,
                        "transaction_date": self.date,
                    },
                    {
                        "transaction_booking_id": trx.id,
                        "item_opening_balance_id": self.id,
                        "description": f"Opening Balance for {item.name}",
                        "item_id": item.id,
                        "account_number": EquityAccount.id,
                        "transaction_type": "cr",
                        "cr_amount": amount,
                        "dr_amount": 0,
                        "transaction_date": self.date,
                    },
                ]
            )

            # Create movement log
            ItemMovement.create(
                {
                    "item_id": item.id,
                    "item_opening_balance_id": self.id,
                    "date": self.date,
                    "quantity": line.quantity,
                    "source": f"Opening Balance Inventory for Item {item.name}",
                    "destination": "Inventory",
                    "movement_type": "in",
                }
            )

        # Update state to confirmed
        self.state = "confirmed"

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", "New") == "New":
                vals["name"] = self.env["ir.sequence"].next_by_code(
                    "idil.item.opening.balance"
                )
        records = super().create(vals_list)
        for record in records:
            if record.state == "confirmed":
                record.confirm_opening_balance()
        return records

    def write(self, vals):
        TransactionBooking = self.env["idil.transaction_booking"]
        ItemMovement = self.env["idil.item.movement"]

        for record in self:
            # Revert stock
            for line in record.line_ids:
                line.item_id.quantity -= line.quantity

            # Delete related bookings
            trx_to_delete = TransactionBooking.search(
                [("item_opening_balance_id", "=", record.id)]
            )
            trx_to_delete.booking_lines.unlink()
            trx_to_delete.unlink()

            # Delete related item movements
            movement_to_delete = ItemMovement.search(
                [("item_opening_balance_id", "=", record.id)]
            )
            movement_to_delete.unlink()

        # Write new values (lines, date, etc.)
        result = super(IdilItemOpeningBalance, self).write(vals)

        # Rebuild all booking/stock/movement logic freshly
        for record in self:
            record._rebuild_confirmed_balance()

        return result

    def _rebuild_confirmed_balance(self):
        TransactionBooking = self.env["idil.transaction_booking"]
        TransactionSource = self.env["idil.transaction.source"]
        ItemMovement = self.env["idil.item.movement"]

        EquityAccount = self.env["idil.chart.account"].search(
            [
                ("name", "=", "Opening Balance Account"),
                ("currency_id.name", "=", "USD"),
            ],
            limit=1,
        )

        source = TransactionSource.search(
            [("name", "=", "Inventory Opening Balance")], limit=1
        )

        if not source:
            raise ValidationError(
                "Transaction Source 'Inventory Opening Balance' not found."
            )

        for line in self.line_ids:
            item = line.item_id

            # Update stock
            item.quantity += line.quantity

            # Create booking
            amount = line.quantity * line.cost_price
            trx = TransactionBooking.create(
                {
                    "transaction_number": self.env["ir.sequence"].next_by_code(
                        "idil.transaction_booking"
                    ),
                    "reffno": item.name,
                    "item_opening_balance_id": self.id,
                    "trx_date": self.date,
                    "amount": amount,
                    "amount_paid": amount,
                    "remaining_amount": 0,
                    "payment_status": "paid",
                    "payment_method": "other",
                    "trx_source_id": source.id,
                }
            )

            trx.booking_lines.create(
                [
                    {
                        "transaction_booking_id": trx.id,
                        "item_opening_balance_id": self.id,
                        "description": f"Opening Balance for {item.name}",
                        "item_id": item.id,
                        "account_number": item.asset_account_id.id,
                        "transaction_type": "dr",
                        "dr_amount": amount,
                        "cr_amount": 0,
                        "transaction_date": self.date,
                    },
                    {
                        "transaction_booking_id": trx.id,
                        "item_opening_balance_id": self.id,
                        "description": f"Opening Balance for {item.name}",
                        "item_id": item.id,
                        "account_number": EquityAccount.id,
                        "transaction_type": "cr",
                        "cr_amount": amount,
                        "dr_amount": 0,
                        "transaction_date": self.date,
                    },
                ]
            )

            ItemMovement.create(
                {
                    "item_id": item.id,
                    "item_opening_balance_id": self.id,
                    "date": self.date,
                    "quantity": line.quantity,
                    "source": f"Opening Balance Inventory for Item {item.name}",
                    "destination": "Inventory",
                    "movement_type": "in",
                }
            )

    def unlink(self):
        TransactionBooking = self.env["idil.transaction_booking"]
        ItemMovement = self.env["idil.item.movement"]

        for record in self:
            # Revert item stock

            for line in record.line_ids:
                item = line.item_id
                if item.quantity < line.quantity:
                    raise ValidationError(
                        f"Cannot delete opening balance for item '{item.name}' because its current stock ({item.quantity}) is less than the opening balance quantity ({line.quantity}). "
                        "This means the item has already been used in manufacturing or other transactions. "
                        "To proceed, you must first delete the related manufacturing or stock usage records that consumed this item."
                    )

            for line in record.line_ids:
                line.item_id.quantity -= line.quantity

            # Delete related booking lines and bookings
            trx_to_delete = TransactionBooking.search(
                [("item_opening_balance_id", "=", record.id)]
            )
            trx_to_delete.booking_lines.unlink()
            trx_to_delete.unlink()

            # Delete related item movements
            movement_to_delete = ItemMovement.search(
                [("item_opening_balance_id", "=", record.id)]
            )
            movement_to_delete.unlink()

        return super(IdilItemOpeningBalance, self).unlink()


class IdilItemOpeningBalanceLine(models.Model):
    _name = "idil.item.opening.balance.line"
    _description = "Opening Balance Line"

    opening_balance_id = fields.Many2one(
        "idil.item.opening.balance", string="Opening Balance", ondelete="cascade"
    )
    item_id = fields.Many2one("idil.item", string="Item", required=True)
    quantity = fields.Float(string="Quantity", required=True)
    cost_price = fields.Float(string="Cost Price", store=True)
    total = fields.Float(string="Total", compute="_compute_total", store=True)

    @api.onchange("item_id")
    def _onchange_item_id(self):
        if self.item_id:
            self.cost_price = self.item_id.cost_price

    @api.depends("quantity", "cost_price")
    def _compute_total(self):
        for line in self:
            line.total = line.quantity * line.cost_price

    def create(self, vals_list):
        for vals in vals_list:
            # If cost_price not explicitly passed, pull from item
            if vals.get("item_id") and not vals.get("cost_price"):
                item = self.env["idil.item"].browse(vals["item_id"])
                vals["cost_price"] = item.cost_price
        records = super().create(vals_list)
        return records

    def write(self, vals):
        for line in self:
            if "item_id" in vals and "cost_price" not in vals:
                item = self.env["idil.item"].browse(vals["item_id"])
                vals["cost_price"] = item.cost_price
        return super().write(vals)
