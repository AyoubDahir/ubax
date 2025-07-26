from odoo import models, fields, api
from odoo.exceptions import ValidationError
from datetime import datetime

import logging

_logger = logging.getLogger(__name__)


class item(models.Model):
    _name = "idil.item"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _description = "Idil Purchased Items"

    ITEM_TYPE_SELECTION = [
        ("service", "Service"),
        ("inventory", "Inventory"),
        ("non_inventory", "Non-Inventory"),
        ("discount", "Discount"),
        ("payment", "Payment"),
        ("tax", "Tax"),
        ("mileage", "Mileage"),
        # Add more QuickBooks item types as needed
    ]
    name = fields.Char(string="Item Name", required=True, tracking=True)
    active = fields.Boolean(string="Archive", default=True, tracking=True)

    description = fields.Text(string="Description", tracking=True)
    item_type = fields.Selection(
        selection=ITEM_TYPE_SELECTION, string="Item Type", required=True, tracking=True
    )
    quantity = fields.Float(
        string="Quantity", required=True, tracking=True, default=0.0
    )

    purchase_date = fields.Date(
        string="Purchase Date", required=True, tracking=True, default=fields.Date.today
    )
    expiration_date = fields.Date(
        string="Expiration Date", required=True, tracking=True
    )
    item_category_id = fields.Many2one(
        comodel_name="idil.item.category",
        string="Item Category",
        required=True,
        help="Select Item Category",
        tracking=True,
    )
    unitmeasure_id = fields.Many2one(
        comodel_name="idil.unit.measure",
        string="Unit of Measure",
        required=True,
        help="Select Unit of Measure",
        tracking=True,
    )
    min = fields.Float(string="Min Order", required=True, tracking=True)

    cost_price = fields.Float(
        string="Price per Unit", digits=(16, 5), required=True, tracking=True
    )

    allergens = fields.Char(string="Allergens/Ingredients", tracking=True)
    image = fields.Binary(string=" Image")
    order_information = fields.Char(string="Order Information", tracking=True)
    bar_code = fields.Char(string="Bar Code", tracking=True)
    # Currency fields
    currency_id = fields.Many2one(
        "res.currency",
        string="Currency",
        required=True,
        default=lambda self: self.env.company.currency_id,
    )

    purchase_account_id = fields.Many2one(
        "idil.chart.account",
        string="Purchase Account",
        help="Account to report purchases of this item",
        required=True,
        tracking=True,
        domain="[('account_type', 'like', 'COGS'), ('currency_id', '=', currency_id)]",
    )
    sales_account_id = fields.Many2one(
        "idil.chart.account",
        string="Sales Account",
        help="Account to report sales of this item",
        tracking=True,
        domain="[('code', 'like', '4'), ('currency_id', '=', currency_id)]",
        # Domain to filter accounts starting with '4' and in USD
    )
    asset_account_id = fields.Many2one(
        "idil.chart.account",
        string="Asset Account",
        help="Account to report Asset of this item",
        required=True,
        tracking=True,
        domain="[('code', 'like', '1'), ('currency_id', '=', currency_id)]",
        # Domain to filter accounts starting with '1' and in USD
    )

    adjustment_account_id = fields.Many2one(
        "idil.chart.account",
        string="Asset Account",
        help="Account to report adjustment of this item",
        required=True,
        tracking=True,
        domain="[('code', 'like', '1'), ('code', 'like', '5'), ('currency_id', '=', currency_id)]",
        # Domain to filter accounts starting with '1' and in USD
    )

    days_until_expiration = fields.Integer(
        string="Days Until Expiration",
        compute="_compute_days_until_expiration",
        store=True,
        readonly=True,
    )
    # New computed field
    total_price = fields.Float(
        string="Total Price",
        compute="compute_item_total_value",
        store=False,
        digits=(16, 5),
        tracking=True,
    )

    is_tfg = fields.Boolean(string="Is TFG", default=False, tracking=True)
    is_commission = fields.Boolean(string="Is Commission", default=False, tracking=True)

    # New field to track item movements
    movement_ids = fields.One2many(
        "idil.item.movement", "item_id", string="Item Movements"
    )

    # Add a method to update currency_id for existing records
    def update_currency_id(self):
        usd_currency = self.env.ref("base.USD")
        self.search([]).write({"currency_id": usd_currency.id})

    # @api.depends("quantity", "cost_price")
    # def _compute_total_price(self):
    #     for item in self:
    #         item.total_price = round(item.quantity * item.cost_price, 5)

    @api.depends_context("uid")
    def compute_item_total_value(self):
        """Compute total value per item: sum(dr_amount - cr_amount) where account is asset_account_id."""
        for item in self:
            item.total_price = 0.0  # Default value

            if not item.asset_account_id:
                continue

            self.env.cr.execute(
                """
                SELECT 
                    COALESCE(SUM(dr_amount), 0) - COALESCE(SUM(cr_amount), 0) AS balance
                FROM idil_transaction_bookingline
                WHERE item_id = %s AND account_number = %s
            """,
                (item.id, item.asset_account_id.id),
            )

            result = self.env.cr.fetchone()
            item.total_price = round(result[0], 5) if result and result[0] else 0.0

    @api.constrains("name")
    def _check_unique_name(self):
        for record in self:
            if self.search([("name", "=", record.name), ("id", "!=", record.id)]):
                raise ValidationError(
                    'Item name must be unique. The name "%s" is already in use.'
                    % record.name
                )

    @api.depends("expiration_date")
    def _compute_days_until_expiration(self):
        for record in self:
            if record.expiration_date:
                delta = record.expiration_date - fields.Date.today()
                record.days_until_expiration = delta.days
            else:
                record.days_until_expiration = 0

    @api.constrains("purchase_date", "expiration_date")
    def check_date_not_in_past(self):
        for record in self:
            today = fields.Date.today()
            if record.purchase_date < today or record.expiration_date < today:
                raise ValidationError(
                    "Both purchase and expiration dates must be today or in the future."
                )

    def adjust_stock(self, qty):
        """Safely adjust the stock quantity, preventing negative values."""
        for record in self:
            new_quantity = record.quantity - qty
            if new_quantity < 0:
                # Prevent adjustment to negative. Raise a ValidationError to inform the user.
                raise ValidationError(
                    f"Cannot reduce stock below zero for item {record.name}. Adjustment quantity: {qty}, Current stock: {record.quantity}"
                )
            else:
                record.quantity = new_quantity

    @api.constrains("quantity", "cost_price")
    def _check_positive_values(self):
        for record in self:
            if record.quantity < 0:
                raise ValidationError("Quantity must be a positive value.")
            if record.cost_price < 0:
                raise ValidationError("Cost price must be a positive value.")

    def check_reorder(self):
        """Send notifications for items that need reordering."""
        for record in self:
            if record.quantity < record.min:
                # Logic to send notification or create a reorder
                record.message_post(
                    body=f"Item {record.name} needs reordering. Current stock: {record.quantity}"
                )

    def _create_transaction_booking(self, item):
        """Helper method to create transaction booking only within this model."""

        # Ensure this transaction is only created when explicitly triggered by this model
        if self.env.context.get("manual_update"):
            return  # Skip transaction creation when updating externally

        inventory_opening_balance_source = self.env["idil.transaction.source"].search(
            [("name", "=", "Inventory Opening Balance")], limit=1
        )
        if not inventory_opening_balance_source:
            raise ValidationError(
                "Transaction source 'Inventory Opening Balance' not found. "
                "Please configure the transaction source correctly."
            )

        equity_account = self.env["idil.chart.account"].search(
            [("account_type", "=", "Owners Equity"), ("currency_id.name", "=", "USD")],
            limit=1,
        )
        if not equity_account:
            raise ValidationError(
                "Equity account not found. Please configure the equity account correctly."
            )

        # Validate currency matching between debit and credit accounts
        if item.asset_account_id.currency_id != equity_account.currency_id:
            raise ValidationError(
                f"Currency mismatch between debit account '{item.asset_account_id.name}' and credit account "
                f"'{equity_account.name}'. "
                f"Debit Account Currency: {item.asset_account_id.currency_id.name}, "
                f"Credit Account Currency: {equity_account.currency_id.name}."
            )

        transaction_booking = self.env["idil.transaction_booking"].create(
            {
                "transaction_number": self.env["ir.sequence"].next_by_code(
                    "idil.transaction_booking"
                ),
                "reffno": item.name,
                "trx_date": fields.Date.today(),
                "amount": item.quantity * item.cost_price,
                "amount_paid": item.quantity * item.cost_price,
                "remaining_amount": 0,
                "payment_status": "paid",
                "payment_method": "other",
                "trx_source_id": inventory_opening_balance_source.id,
            }
        )

        transaction_booking.booking_lines.create(
            {
                "transaction_booking_id": transaction_booking.id,
                "description": "Opening Balance for Item %s" % item.name,
                "item_id": item.id,
                "account_number": item.asset_account_id.id,
                "transaction_type": "dr",
                "dr_amount": item.quantity * item.cost_price,
                "cr_amount": 0,
                "transaction_date": fields.Date.today(),
            }
        )

        transaction_booking.booking_lines.create(
            {
                "transaction_booking_id": transaction_booking.id,
                "description": "Opening Balance for Item %s" % item.name,
                "item_id": item.id,
                "account_number": equity_account.id,
                "transaction_type": "cr",
                "cr_amount": item.quantity * item.cost_price,
                "dr_amount": 0,
                "transaction_date": fields.Date.today(),
            }
        )

        self.env["idil.item.movement"].create(
            {
                "item_id": item.id,
                "date": fields.Date.today(),
                "quantity": item.quantity,
                "source": "Opening Balance Inventory for Item %s" % item.name,
                "destination": "Inventory",
                "movement_type": "in",
            }
        )


class ItemMovement(models.Model):
    _name = "idil.item.movement"
    _description = "Item Movement"
    _inherit = ["mail.thread", "mail.activity.mixin"]

    item_id = fields.Many2one("idil.item", string="Item", required=True, tracking=True)
    date = fields.Date(
        string="Date", required=True, default=fields.Date.today, tracking=True
    )
    quantity = fields.Float(string="Quantity", required=True, tracking=True)
    source = fields.Char(string="Source", required=True, tracking=True)
    destination = fields.Char(string="Destination", required=True, tracking=True)
    movement_type = fields.Selection(
        [("in", "In"), ("out", "Out")],
        string="Movement Type",
        required=True,
        tracking=True,
    )
    related_document = fields.Reference(
        selection=[
            ("idil.purchase_order.line", "Purchase Order Line"),
            ("idil.manufacturing.order.line", "Manufacturing Order Line"),
            ("idil.stock.adjustment", "Stock Adjustment"),
            ("idil.purchase_return.line", "Purchase Return Line"),
        ],
        string="Related Document",
    )

    vendor_id = fields.Many2one(
        "idil.vendor.registration",
        string="Vendor",
        tracking=True,
        help="Vendor associated with this movement if it originated from a purchase order",
    )

    product_id = fields.Many2one(
        "my_product.product",
        string="Product",
        tracking=True,
        help="Product associated with this movement if it relates to a manufacturing order",
    )
    transaction_number = fields.Char(string="Transaction Number", tracking=True)

    purchase_order_line_id = fields.Many2one(
        "idil.purchase_order.line",
        string="Purchase Order Line",
        ondelete="cascade",  # Enables automatic deletion
        index=True,
    )
    item_opening_balance_id = fields.Many2one(
        "idil.item.opening.balance",
        string="Item Opening Balance",
        ondelete="cascade",  # ✅ auto-delete booking when opening balance is deleted
        index=True,
    )
    purchase_return_id = fields.Many2one(
        "idil.purchase_return",
        string="Purchase Return",
        ondelete="cascade",
    )

    @api.model
    def create(self, vals):
        # Automatically fill in the vendor_id or product_id based on related_document
        if vals.get("related_document"):
            model_name, document_id = vals["related_document"].split(",")
            if model_name == "idil.purchase_order.line":
                purchase_order_line = self.env["idil.purchase_order.line"].browse(
                    int(document_id)
                )
                vals["vendor_id"] = purchase_order_line.order_id.vendor_id.id
                vals["transaction_number"] = (
                    purchase_order_line.order_id.reffno
                )  # Assuming transaction number is refno

            elif model_name == "idil.manufacturing.order.line":
                manufacturing_order_line = self.env[
                    "idil.manufacturing.order.line"
                ].browse(int(document_id))
                vals["product_id"] = (
                    manufacturing_order_line.manufacturing_order_id.product_id.id
                )
                vals["transaction_number"] = (
                    manufacturing_order_line.manufacturing_order_id.name
                )  # Assuming order name as transaction number

        return super(ItemMovement, self).create(vals)
