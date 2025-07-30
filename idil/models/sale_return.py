from odoo import models, fields, api
from odoo.exceptions import UserError, ValidationError
import logging


import logging

_logger = logging.getLogger(__name__)


class SaleReturn(models.Model):
    _name = "idil.sale.return"
    _description = "Sale Return"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "id desc"

    name = fields.Char(
        string="Reference", default="New", readonly=True, copy=False, tracking=True
    )
    salesperson_id = fields.Many2one(
        "idil.sales.sales_personnel", string="Salesperson", required=True, tracking=True
    )
    sale_order_id = fields.Many2one(
        "idil.sale.order",
        string="Sale Order",
        required=True,
        domain="[('sales_person_id', '=', salesperson_id)]",
        help="Select a sales order related to the chosen salesperson.",
        tracking=True,
    )
    return_date = fields.Datetime(
        string="Return Date", default=fields.Datetime.now, required=True, tracking=True
    )
    return_lines = fields.One2many(
        "idil.sale.return.line", "return_id", string="Return Lines", required=True
    )
    state = fields.Selection(
        [("draft", "Draft"), ("confirmed", "Confirmed"), ("cancelled", "Cancelled")],
        default="draft",
        string="Status",
        track_visibility="onchange",
        tracking=True,
    )
    # Currency fields
    currency_id = fields.Many2one(
        "res.currency",
        string="Currency",
        required=True,
        default=lambda self: self.env["res.currency"].search(
            [("name", "=", "SL")], limit=1
        ),
        readonly=True,
        tracking=True,
    )
    rate = fields.Float(
        string="Exchange Rate",
        compute="_compute_exchange_rate",
        store=True,
        readonly=True,
        tracking=True,
    )
    total_returned_qty = fields.Float(
        string="Total Returned Quantity",
        compute="_compute_totals",
        store=False,
        tracking=True,
    )

    total_subtotal = fields.Float(
        string="Total Amount",
        compute="_compute_totals",
        store=False,
        tracking=True,
    )
    total_discount_amount = fields.Float(
        string="Total Discount Amount",
        compute="_compute_totals",
        store=False,
        tracking=True,
    )

    total_commission_amount = fields.Float(
        string="Total Commission Amount",
        compute="_compute_totals",
        store=False,
        tracking=True,
    )

    @api.depends(
        "return_lines.returned_quantity",
        "return_lines.price_unit",
        "return_lines.product_id.discount",
        "return_lines.product_id.commission",
        "return_lines.product_id.is_quantity_discount",
    )
    def _compute_totals(self):
        for rec in self:
            total_qty = 0.0
            total_subtotal = 0.0
            total_discount = 0.0
            total_commission = 0.0

            _logger.debug(
                "Computing totals for return %s with %s lines",
                rec.name,
                len(rec.return_lines),
            )

            for line in rec.return_lines:
                qty = line.returned_quantity
                price = line.price_unit
                product = line.product_id

                _logger.debug(
                    "Line product: %s | qty: %s | price: %s",
                    product.name if product else None,
                    qty,
                    price,
                )

                if not product or qty <= 0:
                    continue

                discount_qty = (
                    (product.discount / 100.0) * qty
                    if product.is_quantity_discount
                    else 0.0
                )
                discount_amount = discount_qty * price

                commission_base_qty = qty - discount_qty
                commission_amount = commission_base_qty * product.commission * price

                subtotal = qty * price

                total_qty += qty
                total_subtotal += subtotal
                total_discount += discount_amount
                total_commission += commission_amount

            rec.total_returned_qty = total_qty
            rec.total_subtotal = total_subtotal
            rec.total_discount_amount = total_discount
            rec.total_commission_amount = total_commission

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

    @api.onchange("sale_order_id")
    def _onchange_sale_order_id(self):
        if not self.sale_order_id:
            return
        sale_order = self.sale_order_id
        return_lines = [(5, 0, 0)]  # Clear existing lines

        for line in sale_order.order_lines:
            return_lines.append(
                (
                    0,
                    0,
                    {
                        "product_id": line.product_id.id,
                        "quantity": line.quantity,  # Ensure this is being set
                        "returned_quantity": 0.0,
                        "price_unit": line.price_unit,
                        "subtotal": line.subtotal,
                    },
                )
            )

        self.return_lines = return_lines

    def action_confirm(self):
        try:
            with self.env.cr.savepoint():
                for return_order in self:
                    if return_order.state != "draft":
                        raise UserError("Only draft return orders can be confirmed.")

                    for return_line in return_order.return_lines:
                        corresponding_sale_line = self.env[
                            "idil.sale.order.line"
                        ].search(
                            [
                                ("order_id", "=", return_order.sale_order_id.id),
                                ("product_id", "=", return_line.product_id.id),
                            ],
                            limit=1,
                        )

                        if not corresponding_sale_line:
                            raise ValidationError(
                                f"Sale line not found for product {return_line.product_id.name}."
                            )

                        # ✅ Calculate total previously returned qty for this product in this order
                        previous_returns = self.env["idil.sale.return.line"].search(
                            [
                                (
                                    "return_id.sale_order_id",
                                    "=",
                                    return_order.sale_order_id.id,
                                ),
                                ("product_id", "=", return_line.product_id.id),
                                (
                                    "return_id",
                                    "!=",
                                    return_order.id,
                                ),  # Exclude current draft
                                ("return_id.state", "=", "confirmed"),
                            ]
                        )

                        total_prev_returned = sum(
                            r.returned_quantity for r in previous_returns
                        )
                        new_total = total_prev_returned + return_line.returned_quantity

                        if new_total > corresponding_sale_line.quantity:
                            available_to_return = (
                                corresponding_sale_line.quantity - total_prev_returned
                            )
                            raise ValidationError(
                                f"Cannot return {return_line.returned_quantity:.2f} of {return_line.product_id.name}.\n\n"
                                f"✅ Already Returned: {total_prev_returned:.2f}\n"
                                f"✅ Available for Return: {available_to_return:.2f}\n"
                                f"🧾 Original Sold Quantity: {corresponding_sale_line.quantity:.2f}"
                            )

                    # Confirm valid return
                    self.book_sales_return_entry()

                    return_order.message_post(
                        body=f"✅ Sales Return confirmed with {len(return_order.return_lines.filtered(lambda l: l.returned_quantity > 0))} returned items. Total: {return_order.currency_id.symbol or ''}{sum(return_order.return_lines.filtered(lambda l: l.returned_quantity > 0).mapped('subtotal')):,.2f}"
                    )

                    return_order.write({"state": "confirmed"})

                return True
        except Exception as e:
            _logger.error(f"transaction failed: {str(e)}")
            raise ValidationError(f"Transaction failed: {str(e)}")

    def book_sales_return_entry(self):
        for return_order in self:
            if not return_order.salesperson_id.account_receivable_id:
                raise ValidationError(
                    "The salesperson does not have a receivable account set."
                )

            expected_currency = (
                return_order.salesperson_id.account_receivable_id.currency_id
            )

            trx_source = self.env["idil.transaction.source"].search(
                [("name", "=", "Sales Return")], limit=1
            )
            if not trx_source:
                raise UserError("Transaction source 'Sales Return' not found.")

            transaction_booking = self.env["idil.transaction_booking"].create(
                {
                    "sales_person_id": return_order.salesperson_id.id,
                    "sale_return_id": return_order.id,
                    "sale_order_id": return_order.sale_order_id.id,
                    "trx_source_id": trx_source.id,
                    "Sales_order_number": return_order.sale_order_id.id,
                    "payment_method": "bank_transfer",
                    "payment_status": "pending",
                    "trx_date": fields.Date.context_today(self),
                    "amount": sum(
                        line.subtotal
                        for line in return_order.return_lines
                        if line.returned_quantity > 0
                    ),
                }
            )

            for return_line in return_order.return_lines:
                if return_line.returned_quantity <= 0:
                    continue  # ✅ Skip lines with zero returned quantity

                product = return_line.product_id
                discount_quantity = float(
                    (
                        (product.discount / 100) * return_line.returned_quantity
                        if product.is_quantity_discount
                        else 0.0
                    )
                )
                discount_amount = float(discount_quantity * return_line.price_unit)
                commission_amount = float(
                    (
                        (return_line.returned_quantity - discount_quantity)
                        * product.commission
                        * return_line.price_unit
                    )
                )

                subtotal = float(
                    (
                        (return_line.returned_quantity * return_line.price_unit)
                        - discount_amount
                        - commission_amount
                    )
                )

                bom_currency = (
                    product.bom_id.currency_id
                    if product.bom_id
                    else product.currency_id
                )
                amount_in_bom_currency = product.cost * return_line.returned_quantity
                product_cost_amount = float(
                    (
                        amount_in_bom_currency * self.rate
                        if bom_currency.name == "USD"
                        else amount_in_bom_currency
                    )
                )

                self.env["idil.transaction_bookingline"].create(
                    {
                        "transaction_booking_id": transaction_booking.id,
                        "sale_return_id": return_order.id,
                        "description": f"Sales Return for -- Expanses COGS Account ( {product.name} ) ",
                        "product_id": product.id,
                        "account_number": product.account_cogs_id.id,
                        "transaction_type": "cr",
                        "dr_amount": 0,
                        "cr_amount": float(product_cost_amount),
                        "transaction_date": fields.Date.context_today(self),
                    }
                )

                self.env["idil.transaction_bookingline"].create(
                    {
                        "transaction_booking_id": transaction_booking.id,
                        "sale_return_id": return_order.id,
                        "description": f"Sales Return for -- Product Inventory Account ( {product.name} ) ",
                        "product_id": product.id,
                        "account_number": product.asset_account_id.id,
                        "transaction_type": "dr",
                        "dr_amount": float(product_cost_amount),
                        "cr_amount": 0,
                        "transaction_date": fields.Date.context_today(self),
                    }
                )

                self.env["idil.transaction_bookingline"].create(
                    {
                        "transaction_booking_id": transaction_booking.id,
                        "sale_return_id": return_order.id,
                        "description": f"Sales Return for -- Account Receivable Account ( {product.name} ) ",
                        "product_id": product.id,
                        "account_number": return_order.salesperson_id.account_receivable_id.id,
                        "transaction_type": "cr",
                        "dr_amount": 0,
                        "cr_amount": float(subtotal),
                        "transaction_date": fields.Date.context_today(self),
                    }
                )

                self.env["idil.transaction_bookingline"].create(
                    {
                        "transaction_booking_id": transaction_booking.id,
                        "sale_return_id": return_order.id,
                        "description": f"Sales Return for -- Revenue Account Account ( {product.name} ) ",
                        "product_id": product.id,
                        "account_number": product.income_account_id.id,
                        "transaction_type": "dr",
                        "dr_amount": float(
                            subtotal + discount_amount + commission_amount
                        ),
                        "cr_amount": 0,
                        "transaction_date": fields.Date.context_today(self),
                    }
                )

                if product.is_sales_commissionable and commission_amount > 0:
                    self.env["idil.transaction_bookingline"].create(
                        {
                            "transaction_booking_id": transaction_booking.id,
                            "sale_return_id": return_order.id,
                            "description": f"Sales Return for -- Commission Expense Account ( {product.name} ) ",
                            "product_id": product.id,
                            "account_number": product.sales_account_id.id,
                            "transaction_type": "cr",
                            "dr_amount": 0,
                            "cr_amount": float(commission_amount),
                            "transaction_date": fields.Date.context_today(self),
                        }
                    )

                if discount_amount > 0:
                    self.env["idil.transaction_bookingline"].create(
                        {
                            "transaction_booking_id": transaction_booking.id,
                            "sale_return_id": return_order.id,
                            "description": f"Sales Return for -- Discount Expense Account ( {product.name} ) ",
                            "product_id": product.id,
                            "account_number": product.sales_discount_id.id,
                            "transaction_type": "cr",
                            "dr_amount": 0,
                            "cr_amount": float(discount_amount),
                            "transaction_date": fields.Date.context_today(self),
                        }
                    )

                self.env["idil.salesperson.transaction"].create(
                    {
                        "sales_person_id": return_order.salesperson_id.id,
                        "date": fields.Date.today(),
                        "sale_return_id": return_order.id,
                        "order_id": return_order.sale_order_id.id,
                        "transaction_type": "in",
                        "amount": subtotal + discount_amount + commission_amount,
                        "description": f"Sales Refund of - Order Line for {product.name} (Qty: {return_line.returned_quantity})",
                    }
                )

                self.env["idil.salesperson.transaction"].create(
                    {
                        "sales_person_id": return_order.salesperson_id.id,
                        "date": fields.Date.today(),
                        "sale_return_id": return_order.id,
                        "order_id": return_order.sale_order_id.id,
                        "transaction_type": "in",
                        "amount": -commission_amount,
                        "description": f"Sales Refund of - Commission for {product.name} (Qty: {return_line.returned_quantity})",
                    }
                )

                self.env["idil.salesperson.transaction"].create(
                    {
                        "sales_person_id": return_order.salesperson_id.id,
                        "date": fields.Date.today(),
                        "sale_return_id": return_order.id,
                        "order_id": return_order.sale_order_id.id,
                        "transaction_type": "in",
                        "amount": -discount_amount,
                        "description": f"Sales Refund of - Discount for {product.name} (Qty: {return_line.returned_quantity})",
                    }
                )

                self.env["idil.product.movement"].create(
                    {
                        "product_id": product.id,
                        "movement_type": "in",
                        "quantity": return_line.returned_quantity,
                        "date": return_order.return_date,
                        "source_document": return_order.name,
                        "sales_person_id": return_order.salesperson_id.id,
                    }
                )

                # product.stock_quantity += return_line.returned_quantity

            sales_receipt = self.env["idil.sales.receipt"].search(
                [("sales_order_id", "=", return_order.sale_order_id.id)], limit=1
            )

            if sales_receipt:
                total_return_amount = (
                    sum(
                        line.subtotal
                        for line in return_order.return_lines
                        if line.returned_quantity > 0
                    )
                    - sum(
                        (
                            (
                                line.product_id.discount
                                / 100
                                * line.returned_quantity
                                * line.price_unit
                            )
                            if line.product_id.is_quantity_discount
                            else 0.0
                        )
                        for line in return_order.return_lines
                        if line.returned_quantity > 0
                    )
                    - sum(
                        (
                            (
                                line.returned_quantity
                                - (
                                    (line.product_id.discount / 100)
                                    * line.returned_quantity
                                    if line.product_id.is_quantity_discount
                                    else 0.0
                                )
                            )
                            * line.product_id.commission
                            * line.price_unit
                        )
                        for line in return_order.return_lines
                        if line.returned_quantity > 0
                    )
                )

                sales_receipt.due_amount -= total_return_amount
                sales_receipt.paid_amount = min(
                    sales_receipt.paid_amount, sales_receipt.due_amount
                )
                sales_receipt.remaining_amount = (
                    sales_receipt.due_amount - sales_receipt.paid_amount
                )

                sales_receipt.payment_status = (
                    "paid" if sales_receipt.due_amount <= 0 else "pending"
                )

    # def write(self, vals):
    #     for record in self:
    #         if record.state != "confirmed":
    #             return super(SaleReturn, record).write(vals)

    #         # 1. Capture old data
    #         old_data = {
    #             line.id: {
    #                 "product": line.product_id.display_name,
    #                 "qty": line.returned_quantity,
    #                 "price": line.price_unit,
    #                 "subtotal": line.subtotal,
    #             }
    #             for line in record.return_lines
    #         }
    #         old_total_subtotal = sum(d["subtotal"] for d in old_data.values())
    #         old_line_ids = set(old_data.keys())

    #         # 2. Perform write
    #         result = super(SaleReturn, record).write(vals)

    #         # 3. Capture new data for audit logging
    #         new_data = {
    #             line.id: {
    #                 "product": line.product_id.display_name,
    #                 "qty": line.returned_quantity,
    #                 "price": line.price_unit,
    #                 "subtotal": line.subtotal,
    #             }
    #             for line in record.return_lines
    #         }
    #         new_line_ids = set(new_data.keys())

    #         added_lines = new_line_ids - old_line_ids
    #         removed_lines = old_line_ids - new_line_ids
    #         updated_lines = {
    #             line_id
    #             for line_id in old_line_ids & new_line_ids
    #             if old_data[line_id] != new_data[line_id]
    #         }

    #         messages = []
    #         for line_id in added_lines:
    #             line = new_data[line_id]
    #             messages.append(
    #                 f"🟢 Added: {line['product']} — Qty: {line['qty']}, Price: {line['price']}, Subtotal: {line['subtotal']}"
    #             )
    #         for line_id in removed_lines:
    #             line = old_data[line_id]
    #             messages.append(
    #                 f"🔴 Removed: {line['product']} — Qty: {line['qty']}, Price: {line['price']}, Subtotal: {line['subtotal']}"
    #             )
    #         for line_id in updated_lines:
    #             old = old_data[line_id]
    #             new = new_data[line_id]
    #             changes = []
    #             if old["qty"] != new["qty"]:
    #                 changes.append(f"Qty: {old['qty']} → {new['qty']}")
    #             if old["price"] != new["price"]:
    #                 changes.append(f"Price: {old['price']} → {new['price']}")
    #             if old["subtotal"] != new["subtotal"]:
    #                 changes.append(f"Subtotal: {old['subtotal']} → {new['subtotal']}")
    #             if changes:
    #                 messages.append(
    #                     f"🟡 Updated: {old['product']} — " + ", ".join(changes)
    #                 )

    #         if messages:
    #             record.message_post(
    #                 body="📌 **Audit Log - Sale Return Line Changes**"
    #                 + "".join(messages)
    #             )

    #         # 4. Adjust stock
    #         for line in record.return_lines:
    #             old_qty = old_data.get(line.id, {}).get("qty", 0.0)
    #             delta_qty = line.returned_quantity - old_qty
    #             if delta_qty and line.product_id:
    #                 new_qty = line.product_id.stock_quantity + delta_qty
    #                 line.product_id.sudo().write({"stock_quantity": new_qty})

    #         # 5. Adjust receipt
    #         receipt = self.env["idil.sales.receipt"].search(
    #             [("sales_order_id", "=", record.sale_order_id.id)], limit=1
    #         )
    #         # if receipt:
    #         #     new_total_subtotal = sum(line.subtotal for line in record.return_lines)
    #         #     total_discount = 0.0
    #         #     total_commission = 0.0

    #         #     for line in record.return_lines:
    #         #         product = line.product_id
    #         #         discount_qty = (
    #         #             product.discount / 100 * delta_qty
    #         #             if product.is_quantity_discount
    #         #             else 0.0
    #         #         )
    #         #         discount_amt = discount_qty * line.price_unit
    #         #         commission_amt = (
    #         #             (delta_qty - discount_qty)
    #         #             * product.commission
    #         #             * line.price_unit
    #         #         )
    #         #         total_discount += discount_amt
    #         #         total_commission += commission_amt

    #         #     delta_amount = (
    #         #         (new_total_subtotal - old_total_subtotal) - total_discount
    #         #     ) - total_commission
    #         #     receipt.due_amount -= delta_amount

    #         #     receipt.paid_amount = min(receipt.paid_amount, receipt.due_amount)
    #         #     receipt.remaining_amount = receipt.due_amount - receipt.paid_amount
    #         #     receipt.payment_status = (
    #         #         "paid" if receipt.due_amount <= 0 else "pending"
    #         #     )

    #         if receipt:
    #             new_total_subtotal = sum(line.subtotal for line in record.return_lines)
    #             _logger.info(
    #                 f"[Receipt Adjustment] 🔁 New Total Subtotal: {new_total_subtotal}"
    #             )
    #             _logger.info(
    #                 f"[Receipt Adjustment] 🧾 Old Total Subtotal: {old_total_subtotal}"
    #             )

    #             total_discount = 0.0
    #             total_commission = 0.0

    #             for line in record.return_lines:
    #                 product = line.product_id
    #                 price_unit = line.price_unit
    #                 new_qty = line.returned_quantity
    #                 old_qty = old_data.get(line.id, {}).get("qty", 0.0)
    #                 delta_qty = new_qty - old_qty
    #                 subtotal = line.subtotal

    #                 discount_pct = product.discount or 0.0
    #                 commission_pct = product.commission or 0.0
    #                 is_qty_discount = product.is_quantity_discount

    #                 discount_qty = (
    #                     (discount_pct / 100 * delta_qty) if is_qty_discount else 0.0
    #                 )
    #                 discount_amt = discount_qty * price_unit

    #                 commission_base_qty = delta_qty - discount_qty
    #                 commission_amt = commission_base_qty * commission_pct * price_unit

    #                 total_discount += discount_amt
    #                 total_commission += commission_amt

    #                 _logger.info(
    #                     f"[Line Trace] ▶️ Product: {product.display_name} | "
    #                     f"Old Qty: {old_qty} → New Qty: {new_qty} | Delta Qty: {delta_qty}"
    #                 )
    #                 _logger.info(
    #                     f"[Line Trace] 💵 Price/Unit: {price_unit} | Subtotal: {subtotal}"
    #                 )
    #                 _logger.info(
    #                     f"[Line Trace] 📉 Discount%: {discount_pct}% "
    #                     f"(Qty-Based: {is_qty_discount}) → Discount Qty: {discount_qty}, Amount: {discount_amt}"
    #                 )
    #                 _logger.info(
    #                     f"[Line Trace] 🎯 Commission%: {commission_pct} → "
    #                     f"Base Qty: {commission_base_qty}, Amount: {commission_amt}"
    #                 )

    #             _logger.info(
    #                 f"[Receipt Adjustment] ✅ Total Discount Amount: {total_discount}"
    #             )
    #             _logger.info(
    #                 f"[Receipt Adjustment] ✅ Total Commission Amount: {total_commission}"
    #             )

    #             delta_amount = (
    #                 (new_total_subtotal - old_total_subtotal) - total_discount
    #             ) - total_commission
    #             _logger.info(
    #                 f"[Receipt Adjustment] 🔄 Net Delta Amount to adjust: {delta_amount}"
    #             )

    #             original_due = receipt.due_amount
    #             original_paid = receipt.paid_amount
    #             original_remaining = receipt.remaining_amount

    #             receipt.due_amount -= delta_amount
    #             receipt.paid_amount = min(receipt.paid_amount, receipt.due_amount)
    #             receipt.remaining_amount = receipt.due_amount - receipt.paid_amount
    #             receipt.payment_status = (
    #                 "paid" if receipt.due_amount <= 0 else "pending"
    #             )

    #             _logger.info(
    #                 f"[Receipt Summary] 📊 Before Update → Due: {original_due}, Paid: {original_paid}, Remaining: {original_remaining}"
    #             )
    #             _logger.info(
    #                 f"[Receipt Summary] 📊 After  Update → Due: {receipt.due_amount}, Paid: {receipt.paid_amount}, Remaining: {receipt.remaining_amount}"
    #             )
    #             _logger.info(
    #                 f"[Receipt Summary] 🧾 Payment Status: {receipt.payment_status}"
    #             )

    #         # 6. Clear old records
    #         self.env["idil.transaction_bookingline"].search(
    #             [("sale_return_id", "=", record.id)]
    #         ).unlink()
    #         self.env["idil.salesperson.transaction"].search(
    #             [("sale_return_id", "=", record.id)]
    #         ).unlink()
    #         self.env["idil.product.movement"].search(
    #             [("source_document", "=", record.name), ("movement_type", "=", "in")]
    #         ).unlink()

    #         # 7. Re-book financials and movements
    #         trx_source = self.env["idil.transaction.source"].search(
    #             [("name", "=", "Sales Return")], limit=1
    #         )
    #         booking = self.env["idil.transaction_booking"].create(
    #             {
    #                 "sales_person_id": record.salesperson_id.id,
    #                 "sale_return_id": record.id,
    #                 "sale_order_id": record.sale_order_id.id,
    #                 "trx_source_id": trx_source.id,
    #                 "Sales_order_number": record.sale_order_id.id,
    #                 "payment_method": "bank_transfer",
    #                 "payment_status": "pending",
    #                 "trx_date": fields.Date.context_today(self),
    #                 "amount": sum(line.subtotal for line in record.return_lines),
    #             }
    #         )

    #         for line in record.return_lines:
    #             product = line.product_id
    #             discount_qty = (
    #                 product.discount / 100 * line.returned_quantity
    #                 if product.is_quantity_discount
    #                 else 0.0
    #             )
    #             discount_amt = discount_qty * line.price_unit
    #             commission_amt = (
    #                 (line.returned_quantity - discount_qty)
    #                 * product.commission
    #                 * line.price_unit
    #             )
    #             subtotal = (
    #                 (line.returned_quantity * line.price_unit)
    #                 - discount_amt
    #                 - commission_amt
    #             )

    #             bom_currency = (
    #                 product.bom_id.currency_id
    #                 if product.bom_id
    #                 else product.currency_id
    #             )

    #             amount_in_bom_currency = product.cost * line.returned_quantity
    #             cost_amt = (
    #                 amount_in_bom_currency * self.rate
    #                 if bom_currency.name == "USD"
    #                 else amount_in_bom_currency
    #             )

    #             # Booking lines
    #             self.env["idil.transaction_bookingline"].create(
    #                 {
    #                     "transaction_booking_id": booking.id,
    #                     "sale_return_id": record.id,
    #                     "description": f"Sales Return COGS for {product.name}",
    #                     "product_id": product.id,
    #                     "account_number": product.account_cogs_id.id,
    #                     "transaction_type": "cr",
    #                     "cr_amount": cost_amt,
    #                     "transaction_date": fields.Date.context_today(self),
    #                 }
    #             )
    #             self.env["idil.transaction_bookingline"].create(
    #                 {
    #                     "transaction_booking_id": booking.id,
    #                     "sale_return_id": record.id,
    #                     "description": f"Sales Return Inventory for {product.name}",
    #                     "product_id": product.id,
    #                     "account_number": product.asset_account_id.id,
    #                     "transaction_type": "dr",
    #                     "dr_amount": cost_amt,
    #                     "transaction_date": fields.Date.context_today(self),
    #                 }
    #             )
    #             self.env["idil.transaction_bookingline"].create(
    #                 {
    #                     "transaction_booking_id": booking.id,
    #                     "sale_return_id": record.id,
    #                     "description": f"Sales Return Receivable for {product.name}",
    #                     "product_id": product.id,
    #                     "account_number": record.salesperson_id.account_receivable_id.id,
    #                     "transaction_type": "cr",
    #                     "cr_amount": subtotal,
    #                     "transaction_date": fields.Date.context_today(self),
    #                 }
    #             )
    #             self.env["idil.transaction_bookingline"].create(
    #                 {
    #                     "transaction_booking_id": booking.id,
    #                     "sale_return_id": record.id,
    #                     "description": f"Sales Return Revenue for {product.name}",
    #                     "product_id": product.id,
    #                     "account_number": product.income_account_id.id,
    #                     "transaction_type": "dr",
    #                     "dr_amount": subtotal + discount_amt + commission_amt,
    #                     "transaction_date": fields.Date.context_today(self),
    #                 }
    #             )

    #             if product.is_sales_commissionable and commission_amt > 0:
    #                 self.env["idil.transaction_bookingline"].create(
    #                     {
    #                         "transaction_booking_id": booking.id,
    #                         "sale_return_id": record.id,
    #                         "description": f"Sales Return Commission for {product.name}",
    #                         "product_id": product.id,
    #                         "account_number": product.sales_account_id.id,
    #                         "transaction_type": "cr",
    #                         "cr_amount": commission_amt,
    #                         "transaction_date": fields.Date.context_today(self),
    #                     }
    #                 )

    #             if discount_amt > 0:
    #                 self.env["idil.transaction_bookingline"].create(
    #                     {
    #                         "transaction_booking_id": booking.id,
    #                         "sale_return_id": record.id,
    #                         "description": f"Sales Return Discount for {product.name}",
    #                         "product_id": product.id,
    #                         "account_number": product.sales_discount_id.id,
    #                         "transaction_type": "cr",
    #                         "cr_amount": discount_amt,
    #                         "transaction_date": fields.Date.context_today(self),
    #                     }
    #                 )

    #             # Salesperson transactions
    #             self.env["idil.salesperson.transaction"].create(
    #                 {
    #                     "sales_person_id": record.salesperson_id.id,
    #                     "date": fields.Date.today(),
    #                     "sale_return_id": record.id,
    #                     "order_id": record.sale_order_id.id,
    #                     "transaction_type": "in",
    #                     "amount": subtotal + discount_amt + commission_amt,
    #                     "description": f"Return Total for {product.name} (Qty {line.returned_quantity})",
    #                 }
    #             )
    #             self.env["idil.salesperson.transaction"].create(
    #                 {
    #                     "sales_person_id": record.salesperson_id.id,
    #                     "date": fields.Date.today(),
    #                     "sale_return_id": record.id,
    #                     "order_id": record.sale_order_id.id,
    #                     "transaction_type": "in",
    #                     "amount": -commission_amt,
    #                     "description": f"Return Commission Reversal for {product.name}",
    #                 }
    #             )
    #             self.env["idil.salesperson.transaction"].create(
    #                 {
    #                     "sales_person_id": record.salesperson_id.id,
    #                     "date": fields.Date.today(),
    #                     "sale_return_id": record.id,
    #                     "order_id": record.sale_order_id.id,
    #                     "transaction_type": "in",
    #                     "amount": -discount_amt,
    #                     "description": f"Return Discount Reversal for {product.name}",
    #                 }
    #             )

    #             # Movement
    #             self.env["idil.product.movement"].create(
    #                 {
    #                     "product_id": product.id,
    #                     "movement_type": "in",
    #                     "quantity": line.returned_quantity,
    #                     "date": return_order.date_order,
    #                     "source_document": record.name,
    #                     "sales_person_id": record.salesperson_id.id,
    #                 }
    #             )

    #         # Post final status message

    #     return result
    def write(self, vals):
        for rec in self:
            if rec.state == "confirmed":
                raise UserError(
                    "🛑 Editing is not allowed for this sales return at the moment."
                )
        return super(SaleReturn, self).write(vals)

    def unlink(self):
        try:
            with self.env.cr.savepoint():
                for record in self:
                    if record.state != "confirmed":
                        return super(SaleReturn, record).unlink()

                    # 🔒 Block deletion if receipt has amount_paid > 0
                    receipt = self.env["idil.sales.receipt"].search(
                        [
                            ("sales_order_id", "=", record.sale_order_id.id),
                            ("paid_amount", ">", 0),
                        ],
                        limit=1,
                    )
                    if receipt:
                        raise ValidationError(
                            f"⚠️ You cannot delete this sales return '{record.name}' because a payment of "
                            f"{receipt.paid_amount:.2f} has already been received on the related sales order."
                        )

                    # === 1. Reverse stock quantity ===
                    # for line in record.return_lines:
                    #     if line.product_id and line.returned_quantity:
                    #         new_qty = (
                    #             line.product_id.stock_quantity - line.returned_quantity
                    #         )
                    #         line.product_id.sudo().write({"stock_quantity": new_qty})

                    # === 2. Adjust sales receipt ===
                    receipt = self.env["idil.sales.receipt"].search(
                        [("sales_order_id", "=", record.sale_order_id.id)], limit=1
                    )
                    if receipt:
                        total_subtotal = sum(
                            line.subtotal for line in record.return_lines
                        )
                        total_discount = 0.0
                        total_commission = 0.0

                        for line in record.return_lines:
                            product = line.product_id
                            discount_qty = (
                                product.discount / 100 * line.returned_quantity
                                if product.is_quantity_discount
                                else 0.0
                            )
                            discount_amt = discount_qty * line.price_unit
                            commission_amt = (
                                (line.returned_quantity - discount_qty)
                                * product.commission
                                * line.price_unit
                            )
                            total_discount += discount_amt
                            total_commission += commission_amt

                        return_amount = (
                            total_subtotal - total_discount - total_commission
                        )
                        receipt.due_amount += return_amount
                        receipt.remaining_amount = (
                            receipt.due_amount - receipt.paid_amount
                        )
                        receipt.payment_status = (
                            "paid" if receipt.due_amount <= 0 else "pending"
                        )

                    # === 3. Delete related records ===
                    self.env["idil.transaction_bookingline"].search(
                        [("sale_return_id", "=", record.id)]
                    ).unlink()

                    self.env["idil.salesperson.transaction"].search(
                        [("sale_return_id", "=", record.id)]
                    ).unlink()

                    self.env["idil.product.movement"].search(
                        [
                            ("source_document", "=", record.name),
                            ("movement_type", "=", "in"),
                        ]
                    ).unlink()

                    self.env["idil.transaction_booking"].search(
                        [("sale_return_id", "=", record.id)]
                    ).unlink()

                return super(SaleReturn, self).unlink()
        except Exception as e:
            _logger.error(f"transaction failed: {str(e)}")
            raise ValidationError(f"Transaction failed: {str(e)}")

    @api.model
    def create(self, vals):
        if vals.get("name", "New") == "New":
            vals["name"] = (
                self.env["ir.sequence"].next_by_code("idil.sale.return") or "New"
            )
        return super(SaleReturn, self).create(vals)


class SaleReturnLine(models.Model):
    _name = "idil.sale.return.line"
    _description = "Sale Return Line"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "id desc"

    return_id = fields.Many2one(
        "idil.sale.return",
        string="Sale Return",
        required=True,
        ondelete="cascade",
        tracking=True,
    )
    product_id = fields.Many2one(
        "my_product.product", string="Product", required=True, tracking=True
    )
    quantity = fields.Float(string="Original Quantity", required=True)
    returned_quantity = fields.Float(
        string="Returned Quantity", required=True, tracking=True
    )
    price_unit = fields.Float(string="Unit Price", required=True, tracking=True)
    subtotal = fields.Float(
        string="Subtotal", compute="_compute_subtotal", store=True, tracking=True
    )
    previously_returned_qty = fields.Float(
        string="Previously Returned Qty",
        compute="_compute_previously_returned_qty",
        store=False,
        readonly=True,
        tracking=True,
    )
    available_return_qty = fields.Float(
        string="Available to Return",
        compute="_compute_available_return_qty",
        store=False,
        readonly=True,
        tracking=True,
    )

    @api.depends("product_id", "return_id.sale_order_id")
    def _compute_previously_returned_qty(self):
        for line in self:
            if (
                not line.product_id
                or not line.return_id
                or not line.return_id.sale_order_id
            ):
                line.previously_returned_qty = 0.0
                continue

            domain = [
                ("product_id", "=", line.product_id.id),
                ("return_id.sale_order_id", "=", line.return_id.sale_order_id.id),
                ("return_id.state", "=", "confirmed"),
            ]

            # Avoid filtering by ID if the line is not saved (has no numeric ID)
            if isinstance(line.id, int):
                domain.append(("id", "!=", line.id))

            previous_lines = self.env["idil.sale.return.line"].search(domain)
            line.previously_returned_qty = sum(
                r.returned_quantity for r in previous_lines
            )

    @api.depends("returned_quantity", "price_unit")
    def _compute_subtotal(self):
        for line in self:
            line.subtotal = line.returned_quantity * line.price_unit

    @api.depends("product_id", "return_id.sale_order_id")
    def _compute_available_return_qty(self):
        for line in self:
            line.available_return_qty = 0.0
            if (
                not line.product_id
                or not line.return_id
                or not line.return_id.sale_order_id
            ):
                continue

            sale_line = self.env["idil.sale.order.line"].search(
                [
                    ("order_id", "=", line.return_id.sale_order_id.id),
                    ("product_id", "=", line.product_id.id),
                ],
                limit=1,
            )

            if not sale_line:
                continue

            domain = [
                ("product_id", "=", line.product_id.id),
                ("return_id.sale_order_id", "=", line.return_id.sale_order_id.id),
                ("return_id.state", "=", "confirmed"),
            ]
            if isinstance(line.id, int):
                domain.append(("id", "!=", line.id))

            previous_lines = self.env["idil.sale.return.line"].search(domain)
            total_prev_returned = sum(r.returned_quantity for r in previous_lines)
            line.available_return_qty = max(
                sale_line.quantity - total_prev_returned, 0.0
            )
