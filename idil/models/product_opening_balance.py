from odoo import models, fields, api, exceptions
from datetime import datetime
from datetime import date
import re
from odoo.exceptions import ValidationError, UserError
import logging

_logger = logging.getLogger(__name__)


class ProductOpeningBalance(models.Model):
    _name = "my_product.opening.balance"
    _description = "Product Opening Balance"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "id desc"

    name = fields.Char(string="Reference", readonly=True, default="New")
    date = fields.Date(string="Date", required=True)
    state = fields.Selection(
        [("draft", "Draft"), ("confirmed", "Confirmed")],
        default="draft",
        tracking=True,
    )
    note = fields.Text(string="Note")

    line_ids = fields.One2many(
        "my_product.opening.balance.line",
        "opening_balance_id",
        string="Products",
        copy=True,
    )

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
    total_amount = fields.Float(
        string="Total Amount", compute="_compute_total_amount", store=True
    )

    @api.depends("line_ids.total")
    def _compute_total_amount(self):
        for rec in self:
            rec.total_amount = sum(rec.line_ids.mapped("total"))

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

    @api.constrains("currency_id")
    def _check_exchange_rate_exists(self):
        for order in self:
            if order.currency_id:
                rate = self.env["res.currency.rate"].search_count(
                    [
                        ("currency_id", "=", order.currency_id.id),
                        ("name", "=", fields.Date.today()),
                        ("company_id", "=", self.env.company.id),
                    ]
                )
                if rate == 0:
                    raise exceptions.ValidationError(
                        "No exchange rate found for today. Please insert today's rate before saving."
                    )

    @api.model_create_multi
    def create(self, vals_list):
        try:
            with self.env.cr.savepoint():
                for vals in vals_list:
                    # Auto-generate name if needed
                    if vals.get("name", "New") == "New":
                        vals["name"] = (
                            self.env["ir.sequence"].next_by_code(
                                "my_product.opening.balance"
                            )
                            or "New"
                        )

                        # Check each product in line_ids for duplication
                        line_vals = vals.get("line_ids", [])
                        for command in line_vals:
                            # We are only interested in "create" commands (0)
                            if command[0] == 0:
                                product_id = command[2].get("product_id")
                                if product_id:
                                    # Check if this product already has an opening balance
                                    existing_line = self.env[
                                        "my_product.opening.balance.line"
                                    ].search([("product_id", "=", product_id)], limit=1)
                                    if existing_line:
                                        product_name = existing_line.product_id.name
                                        raise ValidationError(
                                            f"Cannot create opening balance. Product '{product_name}' already has an opening balance record."
                                        )

                return super().create(vals_list)
        except Exception as e:
            _logger.error(f"transaction failed: {str(e)}")
            raise ValidationError(f"Transaction failed: {str(e)}")

    def confirm_opening_balance(self):
        try:
            with self.env.cr.savepoint():
                TransactionBooking = self.env["idil.transaction_booking"]
                TransactionSource = self.env["idil.transaction.source"]
                ProductMovement = self.env["idil.product.movement"]
                ChartAccount = self.env["idil.chart.account"]

                EquityAccount = ChartAccount.search(
                    [("name", "=", "Opening Balance Account")], limit=1
                )
                if not EquityAccount:
                    raise ValidationError(
                        "Opening Balance Account not found. Please configure it."
                    )

                source = TransactionSource.search(
                    [("name", "=", "Product Opening Balance")], limit=1
                )
                if not source:
                    raise ValidationError(
                        "Transaction Source 'Product Opening Balance' not found."
                    )

                for line in self.line_ids:
                    product = line.product_id

                    if product.stock_quantity != 0:
                        raise ValidationError(
                            f"Cannot create opening balance. Product '{product.name}' already has stock: {product.stock_quantity}"
                        )

                    # 1. Update product stock
                    product.stock_quantity = line.stock_quantity

                    # 2. Determine amount in BOM currency
                    amount_in_bom_currency = line.stock_quantity * line.cost_price

                    # bom_currency = line.product_id.bom_id.currency_id
                    bom_currency = (
                        line.product_id.bom_id.currency_id
                        if line.product_id.bom_id
                        else line.product_id.currency_id
                    )

                    product_currency = product.asset_account_id.currency_id
                    equity_currency = EquityAccount.currency_id

                    # 3. Convert BOM amount to product currency if needed
                    if product_currency.id != bom_currency.id:
                        if not self.rate:
                            raise ValidationError(
                                "Exchange rate is required for currency conversion."
                            )
                        if bom_currency.name == "USD" and product_currency.name == "SL":
                            amount_for_product_account = (
                                amount_in_bom_currency * self.rate
                            )
                        elif (
                            bom_currency.name == "SL" and product_currency.name == "USD"
                        ):
                            amount_for_product_account = (
                                amount_in_bom_currency / self.rate
                            )
                        else:
                            raise ValidationError(
                                f"Unhandled conversion from BOM currency {bom_currency.name} to product currency {product_currency.name}."
                            )
                    else:
                        amount_for_product_account = amount_in_bom_currency

                    # 4. Convert BOM amount to equity currency if needed
                    if equity_currency.id != bom_currency.id:
                        if not self.rate:
                            raise ValidationError(
                                "Exchange rate is required for currency conversion."
                            )
                        if bom_currency.name == "USD" and equity_currency.name == "SL":
                            amount_for_equity_account = (
                                amount_in_bom_currency * self.rate
                            )
                        elif (
                            bom_currency.name == "SL" and equity_currency.name == "USD"
                        ):
                            amount_for_equity_account = (
                                amount_in_bom_currency / self.rate
                            )
                        else:
                            raise ValidationError(
                                f"Unhandled conversion from BOM currency {bom_currency.name} to equity currency {equity_currency.name}."
                            )
                    else:
                        amount_for_equity_account = amount_in_bom_currency

                    # 5. Find clearing accounts
                    source_clearing_account = ChartAccount.search(
                        [
                            ("name", "=", "Exchange Clearing Account"),
                            ("currency_id", "=", product_currency.id),
                        ],
                        limit=1,
                    )

                    target_clearing_account = ChartAccount.search(
                        [
                            ("name", "=", "Exchange Clearing Account"),
                            ("currency_id", "=", equity_currency.id),
                        ],
                        limit=1,
                    )

                    if not source_clearing_account or not target_clearing_account:
                        raise ValidationError(
                            "Exchange Clearing Accounts must exist for both the product and equity account currencies."
                        )

                    # 6. Create transaction booking
                    trx = TransactionBooking.create(
                        {
                            "transaction_number": self.env["ir.sequence"].next_by_code(
                                "idil.transaction_booking"
                            ),
                            "reffno": product.name,
                            "product_opening_balance_id": self.id,
                            "trx_date": self.date,
                            "amount": amount_in_bom_currency,
                            "amount_paid": amount_in_bom_currency,
                            "remaining_amount": 0,
                            "payment_status": "paid",
                            "payment_method": "other",
                            "trx_source_id": source.id,
                        }
                    )

                    # 7. Create booking lines
                    trx.booking_lines.create(
                        [
                            {
                                "transaction_booking_id": trx.id,
                                "product_opening_balance_id": self.id,
                                "description": f"Opening Balance for {product.name}",
                                "product_id": product.id,
                                "account_number": product.asset_account_id.id,
                                "transaction_type": "dr",
                                "dr_amount": amount_for_product_account,
                                "cr_amount": 0,
                                "transaction_date": self.date,
                            },
                            {
                                "transaction_booking_id": trx.id,
                                "product_opening_balance_id": self.id,
                                "description": "Opening Balance - Source Clearing",
                                "product_id": product.id,
                                "account_number": source_clearing_account.id,
                                "transaction_type": "cr",
                                "dr_amount": 0,
                                "cr_amount": amount_for_product_account,
                                "transaction_date": self.date,
                            },
                            {
                                "transaction_booking_id": trx.id,
                                "product_opening_balance_id": self.id,
                                "description": "Opening Balance - Target Clearing",
                                "product_id": product.id,
                                "account_number": target_clearing_account.id,
                                "transaction_type": "dr",
                                "dr_amount": amount_for_equity_account,
                                "cr_amount": 0,
                                "transaction_date": self.date,
                            },
                            {
                                "transaction_booking_id": trx.id,
                                "product_opening_balance_id": self.id,
                                "description": "Opening Balance - Equity Account",
                                "product_id": product.id,
                                "account_number": EquityAccount.id,
                                "transaction_type": "cr",
                                "dr_amount": 0,
                                "cr_amount": amount_for_equity_account,
                                "transaction_date": self.date,
                            },
                        ]
                    )
                    if bom_currency.name == "SL":
                        product.actual_cost = self.total_amount / self.rate
                    else:
                        product.actual_cost = self.total_amount

                    # 8. Create product movement
                    ProductMovement.create(
                        {
                            "product_id": product.id,
                            "product_opening_balance_id": self.id,
                            "date": self.date,
                            "quantity": line.stock_quantity,
                            "source_document": f"Opening Balance Inventory for product {product.name}",
                            "destination": "Inventory",
                            "movement_type": "in",
                        }
                    )

                self.state = "confirmed"
        except Exception as e:
            _logger.error(f"transaction failed: {str(e)}")
            raise ValidationError(f"Transaction failed: {str(e)}")

    def write(self, vals):
        try:
            with self.env.cr.savepoint():
                TransactionBooking = self.env["idil.transaction_booking"]
                TransactionSource = self.env["idil.transaction.source"]
                ProductMovement = self.env["idil.product.movement"]
                BookingLine = self.env["idil.transaction_bookingline"]
                ChartAccount = self.env["idil.chart.account"]

                EquityAccount = ChartAccount.search(
                    [("name", "=", "Opening Balance Account")], limit=1
                )
                if not EquityAccount:
                    raise ValidationError("Opening Balance Account not found.")

                source = TransactionSource.search(
                    [("name", "=", "Product Opening Balance")], limit=1
                )
                if not source:
                    raise ValidationError(
                        "Transaction Source 'Product Opening Balance' not found."
                    )

                for opening_balance in self:
                    old_line_ids = set(opening_balance.line_ids.ids)
                    old_data = {
                        line.id: {
                            "product_id": line.product_id.id,
                            "qty": line.stock_quantity,
                            "price": line.cost_price,
                        }
                        for line in opening_balance.line_ids
                    }

                res = super().write(vals)

                for opening_balance in self:
                    if opening_balance.state != "confirmed":
                        continue

                    for line in opening_balance.line_ids:
                        product = line.product_id
                        is_new_line = line.id not in old_line_ids
                        old_info = old_data.get(line.id)
                        qty_changed = (
                            not is_new_line and old_info["qty"] != line.stock_quantity
                        )
                        price_changed = (
                            not is_new_line and old_info["price"] != line.cost_price
                        )

                        # === Determine currencies ===
                        bom_currency = (
                            product.bom_id.currency_id
                            if product.bom_id
                            else product.currency_id
                        )
                        product_currency = product.asset_account_id.currency_id
                        equity_currency = EquityAccount.currency_id
                        rate = opening_balance.rate

                        amount_in_bom_currency = line.stock_quantity * line.cost_price

                        # Convert to product account currency
                        if product_currency.id != bom_currency.id:
                            if not rate:
                                raise ValidationError(
                                    "Exchange rate is required for currency conversion."
                                )
                            if (
                                bom_currency.name == "USD"
                                and product_currency.name == "SL"
                            ):
                                amount_for_product_account = (
                                    amount_in_bom_currency * rate
                                )
                            elif (
                                bom_currency.name == "SL"
                                and product_currency.name == "USD"
                            ):
                                amount_for_product_account = (
                                    amount_in_bom_currency / rate
                                )
                            else:
                                raise ValidationError(
                                    f"Unhandled conversion from BOM currency {bom_currency.name} to product currency {product_currency.name}."
                                )
                        else:
                            amount_for_product_account = amount_in_bom_currency

                        # Convert to equity currency
                        if equity_currency.id != bom_currency.id:
                            if not rate:
                                raise ValidationError(
                                    "Exchange rate is required for currency conversion."
                                )
                            if (
                                bom_currency.name == "USD"
                                and equity_currency.name == "SL"
                            ):
                                amount_for_equity_account = (
                                    amount_in_bom_currency * rate
                                )
                            elif (
                                bom_currency.name == "SL"
                                and equity_currency.name == "USD"
                            ):
                                amount_for_equity_account = (
                                    amount_in_bom_currency / rate
                                )
                            else:
                                raise ValidationError(
                                    f"Unhandled conversion from BOM currency {bom_currency.name} to equity currency {equity_currency.name}."
                                )
                        else:
                            amount_for_equity_account = amount_in_bom_currency

                        # === Find clearing accounts ===
                        source_clearing = ChartAccount.search(
                            [
                                ("name", "=", "Exchange Clearing Account"),
                                ("currency_id", "=", product_currency.id),
                            ],
                            limit=1,
                        )
                        target_clearing = ChartAccount.search(
                            [
                                ("name", "=", "Exchange Clearing Account"),
                                ("currency_id", "=", equity_currency.id),
                            ],
                            limit=1,
                        )
                        if not source_clearing or not target_clearing:
                            raise ValidationError(
                                f"Exchange Clearing accounts missing for product '{product.name}'."
                            )

                        # === NEW LINE ===
                        if is_new_line:
                            product.stock_quantity += line.stock_quantity
                            product.actual_cost += amount_for_product_account

                            trx = TransactionBooking.create(
                                {
                                    "transaction_number": self.env[
                                        "ir.sequence"
                                    ].next_by_code("idil.transaction_booking"),
                                    "reffno": product.name,
                                    "product_opening_balance_id": opening_balance.id,
                                    "trx_date": opening_balance.date,
                                    "amount": amount_in_bom_currency,
                                    "amount_paid": amount_in_bom_currency,
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
                                        "product_opening_balance_id": opening_balance.id,
                                        "description": f"Opening Balance for {product.name}",
                                        "product_id": product.id,
                                        "account_number": product.asset_account_id.id,
                                        "transaction_type": "dr",
                                        "dr_amount": amount_for_product_account,
                                        "cr_amount": 0,
                                        "transaction_date": opening_balance.date,
                                    },
                                    {
                                        "transaction_booking_id": trx.id,
                                        "product_opening_balance_id": opening_balance.id,
                                        "description": "Opening Balance - Source Clearing",
                                        "product_id": product.id,
                                        "account_number": source_clearing.id,
                                        "transaction_type": "cr",
                                        "dr_amount": 0,
                                        "cr_amount": amount_for_product_account,
                                        "transaction_date": opening_balance.date,
                                    },
                                    {
                                        "transaction_booking_id": trx.id,
                                        "product_opening_balance_id": opening_balance.id,
                                        "description": "Opening Balance - Target Clearing",
                                        "product_id": product.id,
                                        "account_number": target_clearing.id,
                                        "transaction_type": "dr",
                                        "dr_amount": amount_for_equity_account,
                                        "cr_amount": 0,
                                        "transaction_date": opening_balance.date,
                                    },
                                    {
                                        "transaction_booking_id": trx.id,
                                        "product_opening_balance_id": opening_balance.id,
                                        "description": "Opening Balance - Equity Account",
                                        "product_id": product.id,
                                        "account_number": EquityAccount.id,
                                        "transaction_type": "cr",
                                        "dr_amount": 0,
                                        "cr_amount": amount_for_equity_account,
                                        "transaction_date": opening_balance.date,
                                    },
                                ]
                            )

                            ProductMovement.create(
                                {
                                    "product_id": product.id,
                                    "product_opening_balance_id": opening_balance.id,
                                    "date": opening_balance.date,
                                    "quantity": line.stock_quantity,
                                    "source_document": f"Opening Balance for {product.name}",
                                    "destination": "Inventory",
                                    "movement_type": "in",
                                }
                            )

                        # === EXISTING LINE (Update) ===
                        elif qty_changed or price_changed:
                            used_elsewhere = self.env[
                                "idil.product.movement"
                            ].search_count(
                                [
                                    ("product_id", "=", product.id),
                                    (
                                        "product_opening_balance_id",
                                        "!=",
                                        opening_balance.id,
                                    ),
                                ]
                            )
                            if used_elsewhere > 0:
                                raise ValidationError(
                                    f"Cannot update '{product.name}': already used in other stock movements."
                                )

                            qty_diff = line.stock_quantity - old_info["qty"]
                            product.stock_quantity += qty_diff
                            product.actual_cost += amount_for_product_account

                            movement = ProductMovement.search(
                                [
                                    ("product_id", "=", product.id),
                                    (
                                        "product_opening_balance_id",
                                        "=",
                                        opening_balance.id,
                                    ),
                                ],
                                limit=1,
                            )
                            if movement:
                                movement.quantity = line.stock_quantity
                                movement.date = opening_balance.date

                            trx = TransactionBooking.search(
                                [
                                    (
                                        "product_opening_balance_id",
                                        "=",
                                        opening_balance.id,
                                    ),
                                    ("booking_lines.product_id", "=", product.id),
                                ],
                                limit=1,
                            )
                            if trx:
                                trx.amount = amount_in_bom_currency
                                trx.amount_paid = amount_in_bom_currency
                                trx.remaining_amount = 0
                                trx.trx_date = opening_balance.date

                                for bl in trx.booking_lines:
                                    if "Opening Balance for" in bl.description:
                                        bl.dr_amount = amount_for_product_account
                                        bl.transaction_date = opening_balance.date
                                    elif "Source Clearing" in bl.description:
                                        bl.cr_amount = amount_for_product_account
                                        bl.transaction_date = opening_balance.date
                                    elif "Target Clearing" in bl.description:
                                        bl.dr_amount = amount_for_equity_account
                                        bl.transaction_date = opening_balance.date
                                    elif "Equity Account" in bl.description:
                                        bl.cr_amount = amount_for_equity_account
                                        bl.transaction_date = opening_balance.date

                return res
        except Exception as e:
            _logger.error(f"transaction failed: {str(e)}")
            raise ValidationError(f"Transaction failed: {str(e)}")

    def unlink(self):
        try:
            with self.env.cr.savepoint():
                ProductMovement = self.env["idil.product.movement"]
                TransactionBooking = self.env["idil.transaction_booking"]
                ProductMovement = self.env["idil.product.movement"]

                for record in self:
                    # Allow deletion only for draft records

                    # 1. Revert product stock
                    # Check for any product in this opening balance being used elsewhere
                    for line in record.line_ids:
                        product = line.product_id

                        # 🔒 Check if the product is used in other product movements (not from this opening balance)
                        other_movements_exist = ProductMovement.search_count(
                            [
                                ("product_id", "=", product.id),
                                ("product_opening_balance_id", "!=", record.id),
                            ]
                        )
                        if other_movements_exist > 0:
                            raise ValidationError(
                                f"Cannot delete this opening balance. Product '{product.name}' has already been used in other stock movements. "
                                "Please remove those movements before deleting this record."
                            )

                    for line in record.line_ids:
                        # Only adjust stock if the opening balance is confirmed
                        if record.state != "draft":
                            line.product_id.write(
                                {
                                    "stock_quantity": line.product_id.stock_quantity
                                    - line.stock_quantity
                                }
                            )

                    # 2. Delete related booking lines and bookings
                    trx_to_delete = TransactionBooking.search(
                        [("product_opening_balance_id", "=", record.id)]
                    )
                    trx_to_delete.booking_lines.unlink()
                    trx_to_delete.unlink()

                    # 3. Delete related product movements
                    movement_to_delete = ProductMovement.search(
                        [("product_opening_balance_id", "=", record.id)]
                    )
                    movement_to_delete.unlink()

                return super(ProductOpeningBalance, self).unlink()
        except Exception as e:
            _logger.error(f"transaction failed: {str(e)}")
            raise ValidationError(f"Transaction failed: {str(e)}")


class ProductOpeningBalanceLine(models.Model):
    _name = "my_product.opening.balance.line"
    _description = "Opening Balance Line"

    opening_balance_id = fields.Many2one(
        "my_product.opening.balance", string="Opening Balance", ondelete="cascade"
    )
    product_id = fields.Many2one("my_product.product", string="Product", required=True)
    stock_quantity = fields.Float(string="stock_quantity", required=True)
    cost_price = fields.Float(string="Cost Price", store=True)
    total = fields.Float(string="Total", compute="_compute_total", store=True)

    @api.onchange("product_id")
    def _onchange_product_id(self):
        if self.product_id:
            self.cost_price = self.product_id.cost

    @api.depends("stock_quantity", "cost_price")
    def _compute_total(self):
        for line in self:
            line.total = line.stock_quantity * line.cost_price

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("product_id") and not vals.get("cost_price"):
                product = self.env["my_product.product"].browse(vals["product_id"])
                vals["cost_price"] = product.cost
        return super().create(vals_list)

    def unlink(self):
        try:
            with self.env.cr.savepoint():
                for line in self:
                    line._check_product_usage(
                        line.product_id, line.opening_balance_id.id
                    )

                    product = line.product_id
                    qty_to_remove = line.stock_quantity

                    # ✅ Block deletion if the product has any other movement (not from this opening balance)
                    other_movements = self.env["idil.product.movement"].search_count(
                        [
                            ("product_id", "=", product.id),
                            (
                                "product_opening_balance_id",
                                "!=",
                                line.opening_balance_id.id,
                            ),
                        ]
                    )
                    if other_movements > 0:
                        raise ValidationError(
                            f"Cannot delete product '{product.name}' because it has already been used in other stock movements. "
                            "Please remove those movements first."
                        )

                    # 2. Reduce product stock
                    product.write(
                        {"stock_quantity": product.stock_quantity - qty_to_remove}
                    )

                    # 3. Delete related movement
                    movement = self.env["idil.product.movement"].search(
                        [
                            (
                                "product_opening_balance_id",
                                "=",
                                line.opening_balance_id.id,
                            ),
                            ("product_id", "=", product.id),
                        ],
                        limit=1,
                    )
                    if movement:
                        movement.unlink()

                    # 4. Delete booking lines
                    booking_lines = self.env["idil.transaction_bookingline"].search(
                        [
                            (
                                "product_opening_balance_id",
                                "=",
                                line.opening_balance_id.id,
                            ),
                            ("product_id", "=", product.id),
                        ]
                    )
                    trx = None
                    if booking_lines:
                        trx = booking_lines[0].transaction_booking_id
                        booking_lines.unlink()

                    # 5. Delete booking if no lines left
                    if trx and not trx.booking_lines:
                        trx.unlink()

                return super().unlink()
        except Exception as e:
            _logger.error(f"transaction failed: {str(e)}")
            raise ValidationError(f"Transaction failed: {str(e)}")

    def _check_product_usage(self, product, opening_balance_id):
        # Count all movements for this product excluding the opening balance itself
        movement_count = self.env["idil.product.movement"].search_count(
            [
                ("product_id", "=", product.id),
                ("product_opening_balance_id", "!=", opening_balance_id),
            ]
        )

        if movement_count > 0:
            raise ValidationError(
                f"Cannot update or delete opening balance for product '{product.name}' "
                "because it has already been used in other stock movements. "
                "You must remove those movements first to preserve data integrity."
            )
