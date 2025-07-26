from venv import logger
from odoo import models, fields, api
from odoo.exceptions import ValidationError
import re
from datetime import datetime


class StockAdjustment(models.Model):
    _name = "idil.stock.adjustment"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _description = "Stock Adjustment"
    _order = "id desc"

    name = fields.Char(
        string="Reference",
        required=True,
        readonly=True,
        default="New",
        tracking=True,
    )
    item_id = fields.Many2one(
        "idil.item",
        string="Item",
        required=True,
        help="Select the item to adjust",
        tracking=True,
    )
    adjustment_qty = fields.Float(
        string="Adjustment Quantity",
        required=True,
        help="Enter the quantity to adjust",
        tracking=True,
    )
    adjustment_type = fields.Selection(
        [("decrease", "Decrease"), ("increase", "Increase")],
        string="Adjustment Type",
        required=True,
        help="Select adjustment type",
        tracking=True,
    )
    adjustment_date = fields.Date(
        string="Adjustment Date",
        default=fields.Date.today,
        required=True,
        tracking=True,
    )

    reason_id = fields.Many2one(
        "idil.item.adjustment.reason",
        string="Reason for Adjustment",
        help="Reason for the adjustment",
        required=True,
    )
    cost_price = fields.Float(
        string="Cost Price",
        related="item_id.cost_price",
        store=True,
        readonly=True,
        help="Cost price of the item being adjusted",
        tracking=True,
    )
    total_amount = fields.Float(
        string="Total Amount",
        compute="_compute_total_amount",
        store=True,
        help="Total value of the stock adjustment (qty × cost price)",
        tracking=True,
    )

    def _generate_stock_adjustment_reference(self, item):
        item_code = (
            re.sub(r"[^A-Za-z0-9]+", "", item.name[:2]).upper()
            if item and item.name
            else "XX"
        )
        date_str = "/" + datetime.now().strftime("%d%m%Y")
        day_night = "/DAY/" if datetime.now().hour < 12 else "/NIGHT/"
        sequence = self.env["ir.sequence"].next_by_code(
            "idil.stock.adjustment.sequence"
        )
        sequence = sequence[-3:] if sequence else "000"
        return f"ADJ/{item_code}{date_str}{day_night}{sequence}"

    @api.depends("adjustment_qty", "cost_price")
    def _compute_total_amount(self):
        for record in self:
            record.total_amount = record.adjustment_qty * record.cost_price

    @api.model
    def create(self, vals):
        try:
            with self.env.cr.savepoint():
                if vals.get("name", "New") == "New":
                    item = self.env["idil.item"].browse(vals.get("item_id"))
                    vals["name"] = self._generate_stock_adjustment_reference(item)
                adjustment = super(StockAdjustment, self).create(vals)
                item = adjustment.item_id

                if adjustment.adjustment_type == "decrease":
                    if item.quantity < adjustment.adjustment_qty:
                        raise ValidationError("Cannot decrease quantity below zero.")
                    new_quantity = item.quantity - adjustment.adjustment_qty
                    movement_quantity = -adjustment.adjustment_qty
                    movement_type = "out"
                elif adjustment.adjustment_type == "increase":
                    new_quantity = item.quantity + adjustment.adjustment_qty
                    movement_quantity = adjustment.adjustment_qty
                    movement_type = "in"

                item.with_context(update_transaction_booking=False).write(
                    {"quantity": new_quantity}
                )

                trx_source = self.env["idil.transaction.source"].search(
                    [("name", "=", "stock_adjustments")], limit=1
                )

                transaction = self.env["idil.transaction_booking"].create(
                    {
                        "reffno": "Stock Adjustments%s" % adjustment.id,
                        "trx_date": adjustment.adjustment_date,
                        "amount": abs(
                            adjustment.adjustment_qty * adjustment.cost_price
                        ),
                        "trx_source_id": trx_source.id if trx_source else False,
                    }
                )

                # Corrected DR/CR logic for decrease/increase
                amount = abs(adjustment.adjustment_qty * adjustment.cost_price)
                if adjustment.adjustment_type == "decrease":
                    booking_lines = [
                        {
                            "transaction_booking_id": transaction.id,
                            "description": "Stock Adjustment Decrease - Credit Asset",
                            "item_id": item.id,
                            "account_number": item.asset_account_id.id,  # Asset account (Credit)
                            "transaction_type": "cr",
                            "dr_amount": 0.0,
                            "cr_amount": amount,
                            "transaction_date": adjustment.adjustment_date,
                        },
                        {
                            "transaction_booking_id": transaction.id,
                            "description": "Stock Adjustment Decrease - Debit Adjustment Account",
                            "item_id": item.id,
                            "account_number": item.adjustment_account_id.id,  # Adjustment/Loss account (Debit)
                            "transaction_type": "dr",
                            "cr_amount": 0.0,
                            "dr_amount": amount,
                            "transaction_date": adjustment.adjustment_date,
                        },
                    ]
                else:
                    booking_lines = [
                        {
                            "transaction_booking_id": transaction.id,
                            "description": "Stock Adjustment Increase - Debit Asset",
                            "item_id": item.id,
                            "account_number": item.asset_account_id.id,  # Asset account (Debit)
                            "transaction_type": "dr",
                            "cr_amount": 0.0,
                            "dr_amount": amount,
                            "transaction_date": adjustment.adjustment_date,
                        },
                        {
                            "transaction_booking_id": transaction.id,
                            "description": "Stock Adjustment Increase - Credit Adjustment Account",
                            "item_id": item.id,
                            "account_number": item.adjustment_account_id.id,  # Adjustment/Gain account (Credit)
                            "transaction_type": "cr",
                            "dr_amount": 0.0,
                            "cr_amount": amount,
                            "transaction_date": adjustment.adjustment_date,
                        },
                    ]

                self.env["idil.transaction_bookingline"].create(booking_lines)

                self.env["idil.item.movement"].create(
                    {
                        "item_id": item.id,
                        "date": adjustment.adjustment_date,
                        "quantity": movement_quantity,
                        "source": "Stock Adjustment",
                        "destination": item.name,
                        "movement_type": movement_type,
                        "related_document": "idil.stock.adjustment,%d" % adjustment.id,
                        "transaction_number": transaction.id or "/",
                    }
                )

                return adjustment
        except Exception as e:
            logger.error(f"transaction failed: {str(e)}")
            raise ValidationError(f"Transaction failed: {str(e)}")

    def write(self, vals):
        try:
            with self.env.cr.savepoint():
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
                    adjustment_date = vals.get(
                        "adjustment_date", record.adjustment_date
                    )

                    new_item_qty = item.quantity

                    if record.adjustment_type == "decrease":
                        if difference > 0:
                            if item.quantity < difference:
                                raise ValidationError(
                                    "Cannot decrease quantity below zero."
                                )
                            new_item_qty = item.quantity - difference
                        elif difference < 0:
                            new_item_qty = item.quantity + abs(difference)
                        movement_quantity = -new_qty
                    elif record.adjustment_type == "increase":
                        if difference > 0:
                            new_item_qty = item.quantity + difference
                        elif difference < 0:
                            if item.quantity < abs(difference):
                                raise ValidationError(
                                    "Cannot decrease quantity below zero."
                                )
                            new_item_qty = item.quantity - abs(difference)
                        movement_quantity = new_qty

                    item.with_context(update_transaction_booking=False).write(
                        {"quantity": new_item_qty}
                    )

                    transaction = self.env["idil.transaction_booking"].search(
                        [("reffno", "=", "Stock Adjustments%s" % record.id)], limit=1
                    )

                    amount = abs(new_qty * cost_price)
                    if transaction and transaction.booking_lines:
                        # Update header fields
                        transaction.write(
                            {
                                "amount": amount,
                                "trx_date": adjustment_date,
                            }
                        )
                        # There are always two lines: one DR, one CR
                        dr_line = None
                        cr_line = None
                        for line in transaction.booking_lines:
                            if line.transaction_type == "dr":
                                dr_line = line
                            elif line.transaction_type == "cr":
                                cr_line = line

                        # Update DR/CR logic according to type
                        if record.adjustment_type == "decrease":
                            # Credit asset, Debit adjustment/loss account
                            if cr_line:
                                cr_line.write(
                                    {
                                        "description": "Stock Adjustment Decrease - Credit Asset",
                                        "account_number": item.asset_account_id.id,
                                        "cr_amount": amount,
                                        "dr_amount": 0.0,
                                        "transaction_date": adjustment_date,
                                    }
                                )
                            if dr_line:
                                dr_line.write(
                                    {
                                        "description": "Stock Adjustment Decrease - Debit Adjustment Account",
                                        "account_number": item.adjustment_account_id.id,
                                        "dr_amount": amount,
                                        "cr_amount": 0.0,
                                        "transaction_date": adjustment_date,
                                    }
                                )
                        else:
                            # Debit asset, Credit adjustment/gain account
                            if dr_line:
                                dr_line.write(
                                    {
                                        "description": "Stock Adjustment Increase - Debit Asset",
                                        "account_number": item.asset_account_id.id,
                                        "dr_amount": amount,
                                        "cr_amount": 0.0,
                                        "transaction_date": adjustment_date,
                                    }
                                )
                            if cr_line:
                                cr_line.write(
                                    {
                                        "description": "Stock Adjustment Increase - Credit Adjustment Account",
                                        "account_number": item.adjustment_account_id.id,
                                        "cr_amount": amount,
                                        "dr_amount": 0.0,
                                        "transaction_date": adjustment_date,
                                    }
                                )

                    movement = self.env["idil.item.movement"].search(
                        [
                            (
                                "related_document",
                                "=",
                                "idil.stock.adjustment,%d" % record.id,
                            )
                        ],
                        limit=1,
                    )
                    if movement:
                        movement.write(
                            {
                                "quantity": movement_quantity,
                                "date": adjustment_date,
                            }
                        )

                return super(StockAdjustment, self).write(vals)
        except Exception as e:
            logger.error(f"transaction failed: {str(e)}")
            raise ValidationError(f"Transaction failed: {str(e)}")

    def unlink(self):
        try:
            with self.env.cr.savepoint():
                for record in self:
                    item = record.item_id

                    if record.adjustment_type == "decrease":
                        new_qty = item.quantity + record.adjustment_qty
                    elif record.adjustment_type == "increase":
                        new_qty = item.quantity - record.adjustment_qty
                        if new_qty < 0:
                            raise ValidationError(
                                "Cannot reverse increase; would make quantity negative."
                            )

                    item.with_context(update_transaction_booking=False).write(
                        {"quantity": new_qty}
                    )

                    transaction = self.env["idil.transaction_booking"].search(
                        [("reffno", "=", "Stock Adjustments%s" % record.id)], limit=1
                    )

                    if transaction:
                        transaction.booking_lines.unlink()
                        transaction.unlink()

                    movement = self.env["idil.item.movement"].search(
                        [
                            (
                                "related_document",
                                "=",
                                "idil.stock.adjustment,%d" % record.id,
                            )
                        ],
                        limit=1,
                    )

                    if movement:
                        movement.unlink()

                return super(StockAdjustment, self).unlink()
        except Exception as e:
            logger.error(f"transaction failed: {str(e)}")
            raise ValidationError(f"Transaction failed: {str(e)}")


class SrockAdjustmentReason(models.Model):
    _name = "idil.item.adjustment.reason"
    _description = "Stock Adjustment Reason"
    _order = "name"

    name = fields.Char(string="Reason", required=True, translate=True)
