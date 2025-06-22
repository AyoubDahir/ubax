import base64
import io
import os

import xlsxwriter

from odoo import models, fields, api


class Product(models.Model):
    _name = "my_product.product"
    _description = "Product"

    name = fields.Char(string="Product Name", required=True)
    internal_reference = fields.Char(string="Internal Reference", required=True)
    stock_quantity = fields.Float(string="Stock Quantity", default=0.0)
    category_id = fields.Many2one("product.category", string="Product Category")
    # New field for POS categories
    available_in_pos = fields.Boolean(string="Available in POS", default=True)

    pos_categ_ids = fields.Many2many(
        "pos.category",
        string="POS Categories",
    )

    detailed_type = fields.Selection(
        [("consu", "Consumable"), ("service", "Service")],
        string="Product Type",
        default="consu",
        required=True,
        help="A storable product is a product for which you manage stock. The Inventory app has to be installed.\n"
        "A consumable product is a product for which stock is not managed.\n"
        "A service is a non-material product you provide.",
    )

    sale_price = fields.Float(string="Sales Price", required=True)

    is_cost_manual_purchase = fields.Boolean(
        string="Enter Cost Manually",
        help="Enable this option to manually enter the product cost instead of using the cost from the Bill of Materials (BOM).",
        default=False,
    )

    cost = fields.Float(
        string="Cost",
        compute="_compute_product_cost",
        digits=(16, 5),
        store=True,
        readonly=False,  # ✅ this is key to allow manual editing
    )
    sales_description = fields.Text(string="Sales Description")
    purchase_description = fields.Text(string="Purchase Description")
    uom_id = fields.Many2one("idil.unit.measure", string="Unit of Measure")

    taxes_id = fields.Many2one(
        "idil.chart.account",
        string="Taxes Account",
        help="Account to report Sales Taxes",
        domain="[('code', 'like', '5')]",  # Domain to filter accounts starting with '5'
    )

    income_account_id = fields.Many2one(
        "idil.chart.account",
        string="Income Account",
        help="Account to report Sales Income",
        required=True,
        domain="[('code', 'like', '4')]",  # Domain to filter accounts starting with '4'
    )

    asset_currency_id = fields.Many2one(
        "res.currency",
        string="Currency",
        required=True,
        default=lambda self: self.env.company.currency_id,
    )

    asset_account_id = fields.Many2one(
        "idil.chart.account",
        string="Inventory Asset Account",
        help="Account to report Asset of this item",
        required=True,
        tracking=True,
        domain="[('code', 'like', '1'), ('currency_id', '=', asset_currency_id)]",
        # Domain to filter accounts starting with '1' and in USD
    )

    bom_id = fields.Many2one("idil.bom", string="BOM", help="Select BOM for costing")
    image_1920 = fields.Binary(
        string="Image"
    )  # Assuming you use Odoo's standard image field

    currency_id = fields.Many2one(
        "res.currency",
        string="Currency",
        required=True,
        default=lambda self: self.env.company.currency_id,
    )

    account_id = fields.Many2one(
        "idil.chart.account",
        string="Commission Account",
        domain="[('account_type', 'like', 'commission'), ('code', 'like', '2%'), "
        "('currency_id', '=', currency_id)]",
    )

    currency_cogs_id = fields.Many2one(
        "res.currency",
        string="Currency",
        required=True,
        default=lambda self: self.env.company.currency_id,
    )

    account_cogs_id = fields.Many2one(
        "idil.chart.account",
        string="Cost of Goods Sold (Expense)",
        domain="[('account_type', 'like', 'COGS'), ('code', 'like', '5%'), "
        "('currency_id', '=', currency_cogs_id)]",
    )

    account_adjustment_id = fields.Many2one(
        "idil.chart.account",
        string="Adjustment Account (Expense)",
        domain="[('account_type', 'like', 'Adjustment'), ('code', 'like', '5%'), "
        "('currency_id', '=', currency_cogs_id)]",
    )

    is_commissionable = fields.Boolean(string="Commissionable", default=False)

    sales_currency_id = fields.Many2one(
        "res.currency",
        string="Currency",
        required=True,
        default=lambda self: self.env.company.currency_id,
    )
    sales_account_id = fields.Many2one(
        "idil.chart.account",
        string="Sales Commission Account",
        domain="[('account_type', 'like', 'commission'), ('code', 'like', '5%'), "
        "('currency_id', '=', sales_currency_id)]",
    )
    is_sales_commissionable = fields.Boolean(string="Commissionable", default=False)
    commission = fields.Float(string="Commission Rate")

    is_quantity_discount = fields.Boolean(string="Quantity Discount", default=False)
    discount_currency_id = fields.Many2one(
        "res.currency",
        string="Currency",
        required=True,
        default=lambda self: self.env.company.currency_id,
    )
    discount = fields.Float(string="Discount Rate")
    sales_discount_id = fields.Many2one(
        "idil.chart.account",
        string="Sales Discount Account",
        domain="[('account_type', 'like', 'discount'), ('code', 'like', '5%'), "
        "('currency_id', '=', discount_currency_id)]",
    )
    # New One2many field to track product movement history
    movement_ids = fields.One2many(
        "idil.product.movement", "product_id", string="Product Movements"
    )
    excel_file = fields.Binary("Excel File")
    excel_filename = fields.Char("Excel Filename")
    start_date = fields.Datetime(string="Start Date")
    end_date = fields.Datetime(string="End Date")

    # def export_movements_to_excel(self):
    #     # Define the base directory and file name
    #     base_directory = "C:\\product"
    #     if not os.path.exists(base_directory):
    #         os.makedirs(base_directory)

    #     # Increment file name if already exists
    #     file_number = 1
    #     while True:
    #         file_name = f"{self.name}_Product_Movements_{file_number}.xlsx"
    #         file_path = os.path.join(base_directory, file_name)
    #         if not os.path.exists(file_path):
    #             break
    #         file_number += 1

    #     # Apply date range filter
    #     domain = (
    #         [("date", ">=", self.start_date), ("date", "<=", self.end_date)]
    #         if self.start_date and self.end_date
    #         else []
    #     )
    #     filtered_movements = (
    #         self.movement_ids.filtered(
    #             lambda m: m.date and self.start_date <= m.date <= self.end_date
    #         )
    #         if domain
    #         else self.movement_ids
    #     )

    #     if not filtered_movements:
    #         # Show a notification if there are no movements to export
    #         return {
    #             "type": "ir.actions.client",
    #             "tag": "display_notification",
    #             "params": {
    #                 "title": "Export Failed",
    #                 "message": "No data available to export for the selected date range.",
    #                 "type": "warning",
    #             },
    #         }

    #     # Create an Excel file
    #     workbook = xlsxwriter.Workbook(file_path)
    #     worksheet = workbook.add_worksheet()

    #     # Define formats
    #     date_format = workbook.add_format({"num_format": "yyyy-mm-dd hh:mm:ss"})
    #     text_format = workbook.add_format({"text_wrap": True})
    #     number_format = workbook.add_format({"num_format": "0.00"})

    #     # Write headers with bold format
    #     headers = [
    #         "Date",
    #         "Movement Type",
    #         "Quantity",
    #         "Source Document",
    #         "Salesperson",
    #     ]
    #     worksheet.write_row("A1", headers, workbook.add_format({"bold": True}))

    #     # Fill the Excel with data from movements
    #     row = 1
    #     for movement in filtered_movements:
    #         worksheet.write(row, 0, movement.date or "", date_format)
    #         worksheet.write(row, 1, movement.movement_type or "", text_format)
    #         worksheet.write(
    #             row, 2, movement.quantity if movement.quantity else 0.0, number_format
    #         )
    #         worksheet.write(row, 3, movement.source_document or "", text_format)
    #         worksheet.write(row, 4, movement.sales_person_id.name or "", text_format)
    #         row += 1

    #     # Adjust column widths
    #     worksheet.set_column("A:A", 20)  # Date column
    #     worksheet.set_column("B:B", 15)  # Movement Type column
    #     worksheet.set_column("C:C", 12, number_format)  # Quantity column
    #     worksheet.set_column("D:D", 30)  # Source Document column
    #     worksheet.set_column("E:E", 20)  # Salesperson column

    #     workbook.close()

    #     # Open the directory in the file explorer
    #     os.startfile(base_directory)

    #     # Return a confirmation message
    #     return {
    #         "type": "ir.actions.client",
    #         "tag": "display_notification",
    #         "params": {
    #             "title": "Export Completed",
    #             "message": f"The product movements have been exported successfully and saved in {file_name}.",
    #             "type": "success",
    #         },
    #     }

    def export_movements_to_excel(self):
        for product in self:
            # Filter movements by date range
            filtered_movements = product.movement_ids
            if product.start_date and product.end_date:
                filtered_movements = filtered_movements.filtered(
                    lambda m: m.date
                    and product.start_date <= m.date <= product.end_date
                )

            if not filtered_movements:
                return {
                    "type": "ir.actions.client",
                    "tag": "display_notification",
                    "params": {
                        "title": "Export Failed",
                        "message": "No data available to export for the selected date range.",
                        "type": "warning",
                    },
                }

            # Create Excel in memory (no file on disk!)
            output = io.BytesIO()
            workbook = xlsxwriter.Workbook(output, {"in_memory": True})
            worksheet = workbook.add_worksheet()

            # Formats
            date_format = workbook.add_format({"num_format": "yyyy-mm-dd hh:mm:ss"})
            text_format = workbook.add_format({"text_wrap": True})
            number_format = workbook.add_format({"num_format": "0.00"})

            # Headers
            headers = [
                "Date",
                "Movement Type",
                "Quantity",
                "Source Document",
                "Salesperson",
            ]
            worksheet.write_row("A1", headers, workbook.add_format({"bold": True}))

            # Write data
            row = 1
            for movement in filtered_movements:
                worksheet.write(row, 0, movement.date or "", date_format)
                worksheet.write(row, 1, movement.movement_type or "", text_format)
                worksheet.write(
                    row,
                    2,
                    movement.quantity if movement.quantity else 0.0,
                    number_format,
                )
                worksheet.write(row, 3, movement.source_document or "", text_format)
                worksheet.write(
                    row, 4, movement.sales_person_id.name or "", text_format
                )
                row += 1

            # Set column widths
            worksheet.set_column("A:A", 20)
            worksheet.set_column("B:B", 15)
            worksheet.set_column("C:C", 12, number_format)
            worksheet.set_column("D:D", 30)
            worksheet.set_column("E:E", 20)

            workbook.close()
            output.seek(0)

            # Save file in binary field for download
            product.excel_file = base64.b64encode(output.read())
            product.excel_filename = f"{product.name}_Product_Movements.xlsx"

            # Return an action to download the file
            return {
                "type": "ir.actions.act_url",
                "url": f"/web/content/my_product.product/{product.id}/excel_file?download=true",
                "target": "self",
            }

    @api.onchange("asset_currency_id")
    def _onchange_asset_currency_id(self):
        """Updates the domain for account_id based on the selected currency."""
        for asset_account in self:
            if asset_account.asset_currency_id:
                asset_account.asset_account_id = False  # Clear the previous selection

                return {
                    "domain": {
                        "asset_account_id": [
                            ("code", "like", "1%"),
                            (
                                "asset_currency_id",
                                "=",
                                asset_account.asset_currency_id.id,
                            ),
                        ]
                    }
                }
            else:
                return {"domain": {"asset_account_id": [("code", "like", "1%")]}}

    @api.onchange("discount_currency_id")
    def _onchange_sales_currency_id(self):
        """Updates the domain for account_id based on the selected currency."""
        for discount in self:
            if discount.discount_currency_id:
                discount.discount_currency_id = False  # Clear the previous selection

                return {
                    "domain": {
                        "sales_discount_id": [
                            ("account_type", "like", "discount"),
                            ("code", "like", "5%"),
                            (
                                "discount_currency_id",
                                "=",
                                discount.sales_discount_id.id,
                            ),
                        ]
                    }
                }
            else:
                return {
                    "domain": {
                        "sales_discount_id": [
                            ("account_type", "like", "discount"),
                            ("code", "like", "5%"),
                        ]
                    }
                }

    @api.onchange("sales_currency_id")
    def _onchange_sales_currency_id(self):
        """Updates the domain for account_id based on the selected currency."""
        for sales_saft in self:
            if sales_saft.currency_id:
                sales_saft.sales_account_id = False  # Clear the previous selection

                return {
                    "domain": {
                        "sales_account_id": [
                            ("account_type", "like", "commission"),
                            ("code", "like", "5%"),
                            ("sales_currency_id", "=", sales_saft.currency_id.id),
                        ]
                    }
                }
            else:
                return {
                    "domain": {
                        "sales_account_id": [
                            ("account_type", "like", "commission"),
                            ("code", "like", "5%"),
                        ]
                    }
                }

    @api.onchange("currency_id")
    def _onchange_currency_id(self):
        """Updates the domain for account_id based on the selected currency."""
        for employee in self:
            if employee.currency_id:
                employee.account_id = False  # Clear the previous selection

                return {
                    "domain": {
                        "account_id": [
                            ("account_type", "like", "commission"),
                            ("code", "like", "5%"),
                            ("currency_id", "=", employee.currency_id.id),
                        ]
                    }
                }
            else:
                return {
                    "domain": {
                        "account_id": [
                            ("account_type", "like", "commission"),
                            ("code", "like", "5%"),
                        ]
                    }
                }

    @api.depends("bom_id", "bom_id.total_cost", "is_cost_manual_purchase")
    def _compute_product_cost(self):
        for product in self:
            if product.is_cost_manual_purchase:
                # Don't compute, leave manually entered value untouched
                continue
            if product.bom_id and product.bom_id.total_cost:
                product.cost = product.bom_id.total_cost

    @api.model
    def create(self, vals):
        res = super(Product, self).create(vals)
        res._sync_with_odoo_product()

        return res

    def write(self, vals):
        res = super(Product, self).write(vals)
        self._sync_with_odoo_product()
        return res

    def _sync_with_odoo_product(self):
        ProductProduct = self.env["product.product"]
        type_mapping = {
            "stockable": "product",
            "consumable": "consu",
            "service": "service",
        }
        for product in self:
            odoo_product = ProductProduct.search(
                [("default_code", "=", product.internal_reference)], limit=1
            )
            if not odoo_product:
                odoo_product = ProductProduct.create(
                    {
                        "my_product_id": product.id,
                        "name": product.name,
                        "default_code": product.internal_reference,
                        "type": product.detailed_type,
                        "list_price": product.sale_price,
                        "standard_price": product.cost,
                        "categ_id": product.category_id.id,
                        "pos_categ_ids": product.pos_categ_ids,
                        "uom_id": 1,
                        "available_in_pos": product.available_in_pos,
                        "image_1920": product.image_1920,
                    }
                )
            else:
                odoo_product.write(
                    {
                        "my_product_id": product.id,
                        "name": product.name,
                        "default_code": product.internal_reference,
                        "type": product.detailed_type,
                        "list_price": product.sale_price,
                        "standard_price": product.cost,
                        "categ_id": product.category_id.id,
                        "pos_categ_ids": product.pos_categ_ids,
                        "uom_id": 1,
                        "available_in_pos": product.available_in_pos,
                        "image_1920": product.image_1920,
                    }
                )

    @api.onchange("cost")
    def _onchange_cost(self):
        for rec in self:
            if not rec.is_cost_manual_purchase:
                return {
                    "warning": {
                        "title": "Manual Cost Entry Disabled",
                        "message": "To manually enter the cost, please enable the 'Enter Cost Manually' option first.",
                    }
                }
