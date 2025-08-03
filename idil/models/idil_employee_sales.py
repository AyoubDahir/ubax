from odoo import models, fields, api
from odoo.exceptions import ValidationError

import logging

_logger = logging.getLogger(__name__)


class IdilStaffSales(models.Model):
    _name = "idil.staff.sales"
    _description = "Staff Sales"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "id desc"

    name = fields.Char(string="Reference", readonly=True, default="New", tracking=True)
    employee_id = fields.Many2one(
        "idil.employee", string="Staff", required=True, tracking=True
    )
    company_id = fields.Many2one(
        related="employee_id.company_id", store=True, readonly=True
    )

    sales_date = fields.Date(
        string="Date", default=fields.Date.context_today, tracking=True
    )
    line_ids = fields.One2many("idil.staff.sales.line", "sales_id", string="Products")
    currency_id = fields.Many2one(related="employee_id.currency_id", readonly=True)

    total_amount = fields.Monetary(
        string="Total Amount", compute="_compute_total_amount", store=True
    )
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("confirmed", "Confirmed"),
            ("done", "Done"),
        ],
        default="draft",
        tracking=True,
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
    # Add payment status field
    payment_status = fields.Selection(
        [
            ("pending", "Pending"),
            ("paid", "Paid"),
        ],
        string="Payment Status",
        default="pending",
        tracking=True,
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

    @api.depends("line_ids.total")
    def _compute_total_amount(self):
        for rec in self:
            rec.total_amount = sum(line.total for line in rec.line_ids)

    @api.model
    def create(self, vals):
        if vals.get("name", "New") == "New":
            vals["name"] = (
                self.env["ir.sequence"].next_by_code("idil.staff.sales") or "New"
            )
        return super().create(vals)

    def action_approve(self):
        for rec in self:
            if rec.state != "draft":
                raise ValidationError("Only draft records can be approved.")

            try:
                with self.env.cr.savepoint():
                    if not rec.employee_id.account_receivable_id:
                        raise ValidationError(
                            "Employee does not have a receivable account set."
                        )

                    trx_source = self.env["idil.transaction.source"].search(
                        [("name", "=", "Staff Sales")], limit=1
                    )
                    if not trx_source:
                        raise ValidationError(
                            "Transaction source 'Staff Sales' not found."
                        )

                    booking = self.env["idil.transaction_booking"].create(
                        {
                            "employee_id": rec.employee_id.id,
                            "trx_source_id": trx_source.id,
                            "trx_date": rec.sales_date,
                            "amount": rec.total_amount,
                            "payment_method": "cash",
                            "payment_status": "pending",
                            "staff_sales_id": rec.id,
                        }
                    )

                    for line in rec.line_ids:
                        product = line.product_id
                        qty = line.quantity

                        if qty <= 0:
                            raise ValidationError(
                                f"Invalid quantity for product '{product.name}'."
                            )

                        if product.stock_quantity < qty:
                            raise ValidationError(
                                f"Not enough stock for '{product.name}'. "
                                f"Available: {product.stock_quantity}, Needed: {qty}"
                            )

                        amount_in_bom_sale_value_currency = float(line.total)
                        sale_value = amount_in_bom_sale_value_currency

                        bom_currency = (
                            product.bom_id.currency_id
                            if product.bom_id
                            else product.currency_id
                        )

                        amount_in_bom_currency = float(product.cost) * qty
                        if bom_currency.name == "USD":
                            cost_value = amount_in_bom_currency * self.rate
                        else:
                            cost_value = amount_in_bom_currency

                        # product_cost_amount = product.cost * line.quantity * self.rate

                        _logger.info(
                            f"Product Cost Amount: {cost_value} for product {product.name}"
                        )

                        # product_cost_amount = product.cost * line.quantity * self.rate

                        _logger.info(
                            f"Product Cost Amount: {cost_value} for product {product.name}"
                        )
                        _logger.info(
                            f"Sale Value: {sale_value} for product {product.name}"
                        )

                        # Ensure required accounts
                        if not product.asset_account_id:
                            raise ValidationError(
                                f"Product '{product.name}' missing Asset Account."
                            )
                        if not product.account_cogs_id:
                            raise ValidationError(
                                f"Product '{product.name}' missing COGS Account."
                            )
                        if not product.income_account_id:
                            raise ValidationError(
                                f"Product '{product.name}' missing Income Account."
                            )

                        # Check currency consistency
                        expected_currency = (
                            rec.employee_id.account_receivable_id.currency_id
                        )
                        currency_errors = []

                        if product.account_cogs_id.currency_id != expected_currency:
                            currency_errors.append(
                                f"COGS account for '{product.name}' has currency "
                                f"{product.account_cogs_id.currency_id.name}, expected {expected_currency.name}."
                            )
                        if product.asset_account_id.currency_id != expected_currency:
                            currency_errors.append(
                                f"Asset account for '{product.name}' has currency "
                                f"{product.asset_account_id.currency_id.name}, expected {expected_currency.name}."
                            )
                        if product.income_account_id.currency_id != expected_currency:
                            currency_errors.append(
                                f"Income account for '{product.name}' has currency "
                                f"{product.income_account_id.currency_id.name}, expected {expected_currency.name}."
                            )

                        if currency_errors:
                            raise ValidationError("\n".join(currency_errors))

                        # Create stock movement
                        self.env["idil.product.movement"].create(
                            {
                                "product_id": product.id,
                                "movement_type": "out",
                                "quantity": -qty,
                                "date": rec.sales_date,
                                "source_document": rec.name,
                                "destination": "Employee Sales",
                                "employee_id": rec.employee_id.id,
                                "staff_sales_id": rec.id,
                            }
                        )

                        # Booking lines
                        self.env["idil.transaction_bookingline"].create(
                            {
                                "transaction_booking_id": booking.id,
                                "product_id": product.id,
                                "account_number": rec.employee_id.account_receivable_id.id,
                                "transaction_type": "dr",
                                "dr_amount": sale_value,
                                "cr_amount": 0.0,
                                "transaction_date": rec.sales_date,
                                "description": f"Staff Sales Receivable - {product.name}",
                            }
                        )

                        self.env["idil.transaction_bookingline"].create(
                            {
                                "transaction_booking_id": booking.id,
                                "product_id": product.id,
                                "account_number": product.income_account_id.id,
                                "transaction_type": "cr",
                                "dr_amount": 0.0,
                                "cr_amount": sale_value,
                                "transaction_date": rec.sales_date,
                                "description": f"Revenue - {product.name}",
                            }
                        )

                        self.env["idil.transaction_bookingline"].create(
                            {
                                "transaction_booking_id": booking.id,
                                "product_id": product.id,
                                "account_number": product.account_cogs_id.id,
                                "transaction_type": "dr",
                                "dr_amount": cost_value,
                                "cr_amount": 0.0,
                                "transaction_date": rec.sales_date,
                                "description": f"COGS - {product.name}",
                            }
                        )

                        self.env["idil.transaction_bookingline"].create(
                            {
                                "transaction_booking_id": booking.id,
                                "product_id": product.id,
                                "account_number": product.asset_account_id.id,
                                "transaction_type": "cr",
                                "dr_amount": 0.0,
                                "cr_amount": cost_value,
                                "transaction_date": rec.sales_date,
                                "description": f"Inventory Reduction - {product.name}",
                            }
                        )

                    # ✅ Only mark as confirmed if all operations succeed
                    rec.write({"state": "confirmed"})

            except Exception as e:
                _logger.error(f"Approval failed for {rec.name}: {str(e)}")
                raise ValidationError(f"Transaction failed: {str(e)}")

    def write(self, vals):
        res = super().write(vals)

        for rec in self:
            if rec.state != "confirmed":
                continue  # Only adjust confirmed records

            # Update transaction booking
            booking = self.env["idil.transaction_booking"].search(
                [("staff_sales_id", "=", rec.id)], limit=1
            )
            if not booking:
                continue

            booking.write(
                {
                    "trx_date": rec.sales_date,
                    "amount": rec.total_amount,
                }
            )

            # Delete and recreate booking lines
            booking.booking_lines.unlink()

            # Delete and recreate stock movements
            self.env["idil.product.movement"].search(
                [("staff_sales_id", "=", rec.id)]
            ).unlink()

            for line in rec.line_ids:
                product = line.product_id
                qty = line.quantity

                if qty <= 0:
                    raise ValidationError(f"Invalid quantity for '{product.name}'.")

                if product.stock_quantity < qty:
                    raise ValidationError(
                        f"Not enough stock for '{product.name}'. "
                        f"Available: {product.stock_quantity}, Needed: {qty}"
                    )

                sale_value = float(line.total)
                bom_currency = (
                    product.bom_id.currency_id
                    if product.bom_id
                    else product.currency_id
                )
                amount_in_bom_currency = float(product.cost) * qty
                cost_value = (
                    amount_in_bom_currency * rec.rate
                    if bom_currency.name == "USD"
                    else amount_in_bom_currency
                )

                # Account checks
                if not (
                    product.asset_account_id
                    and product.account_cogs_id
                    and product.income_account_id
                ):
                    raise ValidationError(
                        f"Missing accounts for product '{product.name}'."
                    )

                # Currency validation
                expected_currency = rec.employee_id.account_receivable_id.currency_id
                for acc, acc_name in [
                    (product.account_cogs_id, "COGS"),
                    (product.asset_account_id, "Asset"),
                    (product.income_account_id, "Income"),
                ]:
                    if acc.currency_id and acc.currency_id != expected_currency:
                        raise ValidationError(
                            f"{acc_name} account currency for '{product.name}' is "
                            f"{acc.currency_id.name}, expected {expected_currency.name}."
                        )

                # Movement
                self.env["idil.product.movement"].create(
                    {
                        "product_id": product.id,
                        "movement_type": "out",
                        "quantity": -qty,
                        "date": rec.sales_date,
                        "source_document": rec.name,
                        "destination": "Employee Sales",
                        "employee_id": rec.employee_id.id,
                        "staff_sales_id": rec.id,
                    }
                )

                # Booking lines
                booking.booking_lines = [
                    (
                        0,
                        0,
                        {
                            "transaction_booking_id": booking.id,
                            "product_id": product.id,
                            "account_number": rec.employee_id.account_receivable_id.id,
                            "transaction_type": "dr",
                            "dr_amount": sale_value,
                            "cr_amount": 0.0,
                            "transaction_date": rec.sales_date,
                            "description": f"Staff Sales Receivable - {product.name}",
                            "staff_sales_id": rec.id,
                        },
                    ),
                    (
                        0,
                        0,
                        {
                            "transaction_booking_id": booking.id,
                            "product_id": product.id,
                            "account_number": product.income_account_id.id,
                            "transaction_type": "cr",
                            "dr_amount": 0.0,
                            "cr_amount": sale_value,
                            "transaction_date": rec.sales_date,
                            "description": f"Revenue - {product.name}",
                            "staff_sales_id": rec.id,
                        },
                    ),
                    (
                        0,
                        0,
                        {
                            "transaction_booking_id": booking.id,
                            "product_id": product.id,
                            "account_number": product.account_cogs_id.id,
                            "transaction_type": "dr",
                            "dr_amount": cost_value,
                            "cr_amount": 0.0,
                            "transaction_date": rec.sales_date,
                            "description": f"COGS - {product.name}",
                            "staff_sales_id": rec.id,
                        },
                    ),
                    (
                        0,
                        0,
                        {
                            "transaction_booking_id": booking.id,
                            "product_id": product.id,
                            "account_number": product.asset_account_id.id,
                            "transaction_type": "cr",
                            "dr_amount": 0.0,
                            "cr_amount": cost_value,
                            "transaction_date": rec.sales_date,
                            "description": f"Inventory Reduction - {product.name}",
                            "staff_sales_id": rec.id,
                        },
                    ),
                ]

        return res

    def unlink(self):
        for rec in self:
            if rec.state == "confirmed":
                # Delete related product movements
                self.env["idil.product.movement"].search(
                    [("staff_sales_id", "=", rec.id)]
                ).unlink()

                # Delete related transaction booking and its lines
                bookings = self.env["idil.transaction_booking"].search(
                    [("staff_sales_id", "=", rec.id)]
                )
                for booking in bookings:
                    booking.booking_lines.unlink()
                    booking.unlink()

        return super().unlink()


class IdilStaffSalesLine(models.Model):
    _name = "idil.staff.sales.line"
    _description = "Staff Sales Line"

    sales_id = fields.Many2one(
        "idil.staff.sales", string="Staff Sale", required=True, ondelete="cascade"
    )
    product_id = fields.Many2one("my_product.product", string="Product", required=True)
    quantity = fields.Float(string="Quantity", required=True, default=1.0)
    price_unit = fields.Float(
        string="Unit Price",
        required=True,
        digits=(16, 5),
    )

    total = fields.Float(string="Total", compute="_compute_total", store=True)

    stock_available = fields.Float(
        string="Available Stock",
        compute="_compute_stock_available",
        store=False,
        readonly=True,
    )

    @api.onchange("product_id")
    def _onchange_product_id_set_price(self):
        for line in self:
            if line.product_id:
                line.price_unit = line.product_id.sale_price

    @api.depends("product_id")
    def _compute_stock_available(self):
        for line in self:
            line.stock_available = (
                line.product_id.stock_quantity if line.product_id else 0.0
            )

    @api.depends("quantity", "price_unit")
    def _compute_total(self):
        for line in self:
            line.total = line.quantity * line.price_unit
