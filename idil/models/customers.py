from odoo import models, fields, api


class Customer(models.Model):
    _name = "idil.customer.registration"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _description = "Customer Registration"

    name = fields.Char(string="Name", required=True, tracking=True)
    type_id = fields.Many2one(
        comodel_name="idil.customer.type.registration",
        string="Customer Type",
        help="Select type of registration",
    )
    phone = fields.Char(string="Phone", required=True, tracking=True)
    email = fields.Char(string="Email", tracking=True)
    gender = fields.Selection(
        [("male", "Male"), ("female", "Female")], string="Gender", tracking=True
    )
    status = fields.Boolean(string="Status", tracking=True)
    active = fields.Boolean(string="Archive", default=True, tracking=True)
    image = fields.Binary(string="Image")
    currency_id = fields.Many2one(
        "res.currency",
        string="Currency",
        required=True,
        default=lambda self: self.env.company.currency_id,
    )

    account_receivable_id = fields.Many2one(
        "idil.chart.account",
        string="Sales Receivable Account",
        domain="[('account_type', 'like', 'receivable'), ('code', 'like', '1%'), "
        "('currency_id', '=', currency_id)]",
        help="Select the receivable account for transactions.",
        required=True,
    )

    # Relation field to display related sale orders (transactions)
    sale_order_ids = fields.One2many(
        "idil.customer.sale.order",  # Model name of the sale order
        "customer_id",  # Field in the sale order model that links back to customer
        string="Sale Orders",
    )
    customer_balance = fields.Float(
        string="Customer Balance",
        compute="_compute_total_receipt_due",
        store=False,  # set to True if you want to store the result in the database
    )

    # Relation field to display related sale orders (transactions)
    cusotmer_payment_ids = fields.One2many(
        "idil.customer.sale.payment",  # Model name of the sale order
        "customer_id",  # Field in the sale order model that links back to customer
        string="Sale Orders",
    )
    customer_Payment_balance = fields.Float(
        string="Customer Balance",
        compute="_compute_customer_payment_balance",
        store=False,  # set to True if you want to store the result in the database
    )

    employee_id = fields.Many2one(
        "idil.employee",
        string="Employee",
        help="Select Employee",
    )

    @api.depends("sale_order_ids")
    def _compute_total_receipt_due(self):
        for rec in self:
            total_due = 0.0
            receipts = self.env["idil.sales.receipt"].search(
                [("customer_id", "=", rec.id), ("payment_status", "!=", "paid")]
            )
            total_due = sum(receipts.mapped("remaining_amount"))
            rec.customer_balance = total_due

    @api.depends("sale_order_ids.balance_due", "sale_order_ids.state")
    def _compute_customer_balance(self):
        for rec in self:
            balance = 0.0
            for order in rec.sale_order_ids:
                if order.state != "cancel":
                    balance += order.balance_due
            rec.customer_balance = balance

    @api.depends("cusotmer_payment_ids.amount")
    def _compute_customer_payment_balance(self):
        """
        Computes the customer's payment balance by summing up the balance due for each sale order
        associated with the customer. This method is triggered when the amount in customer payment
        ids changes.
        """
        for rec in self:
            balance = 0.0
            for order in rec.sale_order_ids:
                balance += order.balance_due
            rec.customer_balance = balance
