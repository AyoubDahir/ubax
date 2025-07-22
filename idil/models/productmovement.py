from odoo import models, fields


class ProductMovement(models.Model):
    _name = "idil.product.movement"
    _description = "Product Movement History"
    _order = "id desc"

    product_id = fields.Many2one(
        "my_product.product", string="Product", required=True, ondelete="cascade"
    )
    movement_type = fields.Selection(
        [("in", "In"), ("out", "Out")], string="Movement Type", required=True
    )
    quantity = fields.Float(string="Quantity", required=True)
    date = fields.Datetime(string="Date", default=fields.Datetime.now, required=True)
    source_document = fields.Char(string="Source Document")
    destination = fields.Char(string="Destination", tracking=True)

    manufacturing_order_id = fields.Many2one(
        "idil.manufacturing.order",
        string="Manufacturing Order",
        tracking=True,
        ondelete="cascade",  # Add this to enable automatic deletion
    )
    sales_person_id = fields.Many2one(
        "idil.sales.sales_personnel", string="Salesperson"
    )
    customer_id = fields.Many2one("idil.customer.registration", string="Customer Id")
    product_purchase_order_id = fields.Many2one(
        "idil.product.purchase.order",
        string="Product Purchase Order",
        ondelete="cascade",
    )
    purchase_return_id = fields.Many2one(
        "idil.product.purchase_return.line",
        string="Product Purchase Return",
        ondelete="cascade",
    )
    adjustment_id = fields.Many2one(
        "idil.product.adjustment",
        string="Product Adjustment",
        ondelete="set null",
    )
    sale_order_id = fields.Many2one(
        "idil.sale.order", string="Sales Order", ondelete="cascade"
    )
    transaction_number = fields.Char(string="Transaction Number", tracking=True)

    vendor_id = fields.Many2one(
        "idil.vendor.registration",
        string="Vendor",
        tracking=True,
        help="Vendor associated with this movement if it originated from a purchase order",
    )
    related_document = fields.Reference(
        selection=[
            ("idil.product.purchase_return.line", "Product Purchase Return Line"),
            ("idil.product.purchase.order.line", "Product Purchase Order Line"),
        ],
        string="Related Document",
    )
    product_opening_balance_id = fields.Many2one(
        "my_product.opening.balance",
        string="Product Opening Balance",
        ondelete="cascade",  # ✅ auto-delete booking when opening balance is deleted
        index=True,
    )
