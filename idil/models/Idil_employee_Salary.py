import base64
import calendar
import io
from datetime import datetime

from dateutil.relativedelta import relativedelta
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import landscape, letter
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from odoo import models, fields, api
from odoo.exceptions import UserError, ValidationError
from reportlab.platypus import Image
from reportlab.lib.pagesizes import A4, landscape
from reportlab.platypus import SimpleDocTemplate
import logging
from io import BytesIO

_logger = logging.getLogger(__name__)


class IdilEmployeeSalary(models.Model):
    _name = "idil.employee.salary"
    _description = "Employee Salary"
    _inherit = ["mail.thread", "mail.activity.mixin"]

    employee_id = fields.Many2one(
        "idil.employee", string="Employee", required=True, tracking=True
    )
    salary_date = fields.Date(
        string="Salary Date",
        default=fields.Date.context_today,
        required=True,
        tracking=True,
    )
    account_id = fields.Many2one("idil.chart.account", string="Account", required=True)

    basic_salary = fields.Monetary(
        string="Basic Salary",
        related="employee_id.salary",
        readonly=True,
        tracking=True,
    )
    bonus = fields.Monetary(string="Bonus", tracking=True)

    deductions = fields.Monetary(
        string="Deductions",
        default=0.0,
        required=True,
        help="Deductions reduce the net salary.",
        tracking=True,
    )
    advance_deduction = fields.Monetary(
        string="Advance Salary", default=0.0, store=True, tracking=True
    )

    total_salary = fields.Monetary(
        string="Total Salary",
        compute="_compute_total_salary",
        store=True,
        tracking=True,
    )
    total_pending_sales = fields.Monetary(
        string="Total Pending Sales",
        compute="_compute_total_pending_sales",
        store=True,
        currency_field="currency_id",
        tracking=True,
    )

    currency_id = fields.Many2one(
        "res.currency",
        string="Currency",
        readonly=True,
        default=lambda self: self.env.ref("base.USD").id,
        tracking=True,
    )
    is_paid = fields.Boolean(string="Paid", default=True, tracking=True)
    remarks = fields.Text(string="Remarks", tracking=True)

    advances_this_month = fields.One2many(
        "idil.employee.salary.advance",
        string="Advances This Month",
        compute="_compute_advances_this_month",
    )
    pending_sales_ids = fields.One2many(
        comodel_name="idil.staff.sales",
        compute="_compute_pending_sales",
        string="Pending Sales",
    )
    number_of_days = fields.Integer(string="Number of Days", tracking=True)
    salary_by_days = fields.Monetary(
        string="Salary by Days",
        compute="_compute_salary_by_days",
        store=True,
        currency_field="currency_id",
        tracking=True,
    )
    total_earnings = fields.Monetary(
        string="Total Earnings",
        compute="_compute_total_earnings",
        store=True,
        currency_field="currency_id",
        tracking=True,
    )

    @api.onchange("employee_id")
    def _onchange_employee_bonus(self):
        if self.employee_id:
            self.bonus = self.employee_id.bonus

    @api.depends("salary_by_days", "bonus")
    def _compute_total_earnings(self):
        for rec in self:
            rec.total_earnings = rec.salary_by_days + rec.bonus

    @api.depends("basic_salary", "number_of_days")
    def _compute_salary_by_days(self):
        for rec in self:
            rec.salary_by_days = rec.basic_salary * rec.number_of_days

    @api.depends("pending_sales_ids.total_amount")
    def _compute_total_pending_sales(self):
        for record in self:
            record.total_pending_sales = sum(
                record.pending_sales_ids.mapped("total_amount")
            )

    @api.depends("employee_id")
    def _compute_pending_sales(self):
        for rec in self:
            if rec.employee_id:
                rec.pending_sales_ids = self.env["idil.staff.sales"].search(
                    [
                        ("employee_id", "=", rec.employee_id.id),
                        ("payment_status", "=", "pending"),
                    ]
                )
            else:
                rec.pending_sales_ids = self.env["idil.staff.sales"]

    @api.depends("salary_date", "employee_id")
    def _compute_advances_this_month(self):
        for record in self:
            if record.salary_date and record.employee_id:
                salary_month = record.salary_date.month
                salary_year = record.salary_date.year

                # Compute the start and end of the month
                start_date = f"{salary_year}-{salary_month:02d}-01"
                end_date = f"{salary_year}-{salary_month:02d}-{calendar.monthrange(salary_year, salary_month)[1]}"

                # Search for salary advances
                advances = self.env["idil.employee.salary.advance"].search(
                    [
                        ("employee_id", "=", record.employee_id.id),
                        ("state", "in", ["approved", "deducted"]),
                        ("request_date", ">=", start_date),
                        ("request_date", "<=", end_date),
                    ]
                )

                record.advances_this_month = advances
            else:
                # Assign an empty recordset instead of None or False
                record.advances_this_month = self.env["idil.employee.salary.advance"]

    @api.constrains("employee_id", "salary_date")
    def _check_employee_salary_and_contract(self):
        """Ensure the employee has a valid salary and contract."""
        for record in self:

            # Check if the employee has a defined salary
            if not record.employee_id.salary:
                raise ValidationError(
                    f"Cannot process salary for {record.employee_id.name}. No salary is defined for this employee."
                )

            # Check if the employee's contract is valid
            if (
                record.employee_id.contract_start_date
                and record.employee_id.contract_end_date
            ):
                if not (
                    record.employee_id.contract_start_date
                    <= record.salary_date
                    <= record.employee_id.contract_end_date
                ):
                    raise ValidationError(
                        f"Cannot process salary for {record.employee_id.name}. The contract is not valid on the salary date."
                    )
            else:
                raise ValidationError(
                    f"Cannot process salary for {record.employee_id.name}. Contract dates are missing."
                )

    @api.onchange("employee_id", "salary_date")
    def _onchange_employee_id(self):
        """Populate advance_deduction based on selected employee and same month check."""
        for record in self:
            if record.employee_id and record.salary_date:
                # Extract year and month from salary_date
                salary_month = fields.Date.from_string(record.salary_date).month
                salary_year = fields.Date.from_string(record.salary_date).year

                # Search for advances within the same year and month as salary_date
                advances = self.env["idil.employee.salary.advance"].search(
                    [
                        ("employee_id", "=", record.employee_id.id),
                        ("state", "=", "approved"),
                        ("request_date", ">=", f"{salary_year}-{salary_month:02d}-01"),
                        (
                            "request_date",
                            "<=",
                            f"{salary_year}-{salary_month:02d}-{calendar.monthrange(salary_year, salary_month)[1]}",
                        ),
                    ]
                )
                record.advance_deduction = sum(advances.mapped("advance_amount"))
            else:
                record.advance_deduction = 0.0

    from datetime import datetime

    @api.model
    def create(self, vals):
        # Calculate advance deduction if employee_id and salary_date are provided
        if vals.get("employee_id") and vals.get("salary_date"):
            salary_date = fields.Date.from_string(vals["salary_date"])
            # Extract the year and month from the salary_date
            year_month = salary_date.strftime("%Y-%m")

            # Search for advances within the same month and year as the salary_date
            advances = self.env["idil.employee.salary.advance"].search(
                [
                    ("employee_id", "=", vals["employee_id"]),
                    ("state", "=", "approved"),
                    ("request_date", ">=", year_month + "-01"),  # Start of the month
                    (
                        "request_date",
                        "<",
                        (salary_date + relativedelta(months=1)).strftime("%Y-%m-%d"),
                    ),  # End of the month
                ]
            )

            vals["advance_deduction"] = sum(advances.mapped("advance_amount"))
            advances.write({"state": "deducted"})  # Mark advances as deducted

        record = super(IdilEmployeeSalary, self).create(vals)

        # Book transaction booking and lines
        self._book_transaction(record)

        return record

    def _book_transaction(self, record):
        """Books a transaction and validates account balances."""
        # Ensure account_id is a valid Many2one field
        credit_account = record.account_id

        if not credit_account:
            raise ValidationError("Please choose a valid account.")

        # Fetch the account record for "Salary Advance Expense"
        salary_expense_account = self.env["idil.chart.account"].search(
            [
                (
                    "name",
                    "=",
                    "Salary Expense",
                )  # Assuming the type is COGS, adjust based on your setup
            ],
            limit=1,
        )

        # Fetch the trx source record for "Salary Advance Expense"
        salary_expense_trx_source = self.env["idil.transaction.source"].search(
            [
                (
                    "name",
                    "=",
                    "Salary Expense",
                )  # Assuming the type is COGS, adjust based on your setup
            ],
            limit=1,
        )

        # Compute current balance
        transaction_lines = self.env["idil.transaction_bookingline"].search(
            [
                (
                    "account_number",
                    "=",
                    credit_account.id,
                )  # Now credit_account is a recordset, so .id works fine
            ]
        )
        current_balance = sum(transaction_lines.mapped("dr_amount")) - sum(
            transaction_lines.mapped("cr_amount")
        )

        # Check if sufficient balance is available
        if current_balance < record.total_salary:
            raise ValidationError(
                f"Insufficient balance in account '{credit_account.name}'. "
                f"Available balance: {current_balance}, required: {record.total_salary}."
            )

        # Create a transaction booking record
        transaction_booking = self.env["idil.transaction_booking"].create(
            {
                "transaction_number": self.env["ir.sequence"].next_by_code(
                    "idil.transaction_booking"
                )
                or "/",
                "reffno": record.id,
                "employee_salary_id": record.id,
                "employee_id": record.employee_id.id,
                "trx_source_id": salary_expense_trx_source.id,
                "payment_method": "cash",
                "payment_status": "paid",
                "trx_date": record.salary_date,
                "amount": record.total_salary,
                "amount_paid": record.total_salary,
                "remaining_amount": 0,
            }
        )

        # Create transaction booking lines
        self.env["idil.transaction_bookingline"].create(
            {
                "transaction_booking_id": transaction_booking.id,
                "employee_salary_id": record.id,
                "description": "Salary Payment of - "
                + record.salary_date.strftime("%Y-%m")
                + " for - "
                + record.employee_id.name,
                "account_number": salary_expense_account.id,  # Debit salary expense account
                "transaction_type": "dr",
                "dr_amount": record.total_salary,
                "cr_amount": 0,
                "transaction_date": record.salary_date,
            }
        )

        self.env["idil.transaction_bookingline"].create(
            {
                "transaction_booking_id": transaction_booking.id,
                "employee_salary_id": record.id,
                "description": "Salary Payment of - "
                + record.salary_date.strftime("%Y-%m")
                + " for - "
                + record.employee_id.name,
                "account_number": credit_account.id,  # Credit the advance account
                "transaction_type": "cr",
                "dr_amount": 0,
                "cr_amount": record.total_salary,
                "transaction_date": record.salary_date,
            }
        )

    @api.depends(
        "deductions",
        "advance_deduction",
        "total_pending_sales",
        "total_earnings",
    )
    def _compute_total_salary(self):
        for record in self:
            record.total_salary = (
                record.total_earnings
                - record.deductions
                - record.advance_deduction
                - record.total_pending_sales
            )

    @api.depends("employee_id", "salary_date")
    def _compute_advance_deduction(self):
        for record in self:
            if record.employee_id and record.salary_date:
                year_month = record.salary_date.strftime("%Y-%m")
                advances = self.env["idil.employee.salary.advance"].search(
                    [
                        ("employee_id", "=", record.employee_id.id),
                        ("state", "=", "draft"),
                        ("request_date", ">=", year_month + "-01"),
                        (
                            "request_date",
                            "<",
                            (record.salary_date + relativedelta(months=1)).strftime(
                                "%Y-%m-%d"
                            ),
                        ),
                    ]
                )
                record.advance_deduction = sum(advances.mapped("advance_amount"))
                advances.write({"state": "deducted"})  # Mark advances as deducted
            else:
                record.advance_deduction = 0.0

    def write(self, vals):
        """
        Override the write method to first update the salary model and then update the associated
        transaction booking and booking lines.
        """
        for record in self:
            # Step 1: Recalculate advance deduction if 'employee_id' or 'salary_date' is updated
            if "employee_id" in vals or "salary_date" in vals:
                employee_id = vals.get("employee_id", record.employee_id.id)
                salary_date = vals.get("salary_date", record.salary_date)
                salary_date = fields.Date.from_string(salary_date)
                year_month = salary_date.strftime("%Y-%m")

                # Search for advances within the same month and year as the salary_date
                advances = self.env["idil.employee.salary.advance"].search(
                    [
                        ("employee_id", "=", employee_id),
                        ("state", "=", "draft"),
                        ("request_date", ">=", year_month + "-01"),
                        (
                            "request_date",
                            "<",
                            (salary_date + relativedelta(months=1)).strftime(
                                "%Y-%m-%d"
                            ),
                        ),
                    ]
                )

                # Update advance deduction and mark advances as deducted
                vals["advance_deduction"] = sum(advances.mapped("advance_amount"))
                advances.write({"state": "deducted"})

            # Step 2: Update the salary model
            result = super(IdilEmployeeSalary, self).write(vals)

            # Step 3: Update the associated transaction booking
            transaction_booking = self.env["idil.transaction_booking"].search(
                [("employee_salary_id", "=", record.id)], limit=1
            )

            if transaction_booking:
                transaction_booking.write(
                    {
                        "trx_date": record.salary_date,
                        "amount": record.total_salary,
                        "amount_paid": record.total_salary,
                        "remaining_amount": 0,
                    }
                )

            # Step 4: Update booking lines for the associated transaction booking
            booking_lines = self.env["idil.transaction_bookingline"].search(
                [("transaction_booking_id.employee_salary_id", "=", record.id)]
            )

            for line in booking_lines:
                if line.transaction_type == "dr":
                    line.write(
                        {
                            "dr_amount": record.total_salary,
                            "transaction_date": record.salary_date,
                        }
                    )
                elif line.transaction_type == "cr":
                    line.write(
                        {
                            "cr_amount": record.total_salary,
                            "transaction_date": record.salary_date,
                        }
                    )

        return result

    def unlink(self):
        for record in self:
            # Extract year and month from salary_date
            if record.salary_date:
                salary_month = fields.Date.from_string(record.salary_date).month
                salary_year = fields.Date.from_string(record.salary_date).year

                # Search for advances within the same month and year as salary_date
                advances = self.env["idil.employee.salary.advance"].search(
                    [
                        ("employee_id", "=", record.employee_id.id),
                        ("state", "=", "deducted"),
                        ("request_date", ">=", f"{salary_year}-{salary_month:02d}-01"),
                        (
                            "request_date",
                            "<=",
                            f"{salary_year}-{salary_month:02d}-{calendar.monthrange(salary_year, salary_month)[1]}",
                        ),
                    ]
                )

                # Mark all relevant advances as approved
                advances.write({"state": "approved"})

        return super(IdilEmployeeSalary, self).unlink()

    @api.model
    def process_monthly_salary(self, _logger=None):
        employees = self.env["idil.employee"].search([])
        salary_logs = []
        for employee in employees:
            if employee.contract_start_date and employee.contract_end_date:
                today = fields.Date.today()
                if employee.contract_start_date <= today <= employee.contract_end_date:
                    # Create salary record
                    self.create(
                        {
                            "employee_id": employee.id,
                            "basic_salary": employee.salary,
                            "bonus": employee.bonus,
                        }
                    )
                else:
                    salary_logs.append(
                        f"Contract expired for employee {employee.name}."
                    )
            else:
                salary_logs.append(
                    f"Missing contract dates for employee {employee.name}."
                )

        if salary_logs:
            # Log the warnings
            _logger.warning("\n".join(salary_logs))
            raise UserError("\n".join(salary_logs))

    @api.constrains("employee_id", "salary_date")
    def _check_duplicate_salary(self):
        for record in self:
            # Get the month and year of the salary_date
            salary_month = record.salary_date.strftime("%Y-%m")
            # Check if a record exists for the same employee and month
            duplicate = self.search(
                [
                    ("employee_id", "=", record.employee_id.id),
                    ("salary_date", ">=", f"{salary_month}-01"),
                    ("salary_date", "<=", f"{salary_month}-31"),
                    ("id", "!=", record.id),  # Exclude the current record
                ]
            )
            if duplicate:
                raise ValidationError(
                    f"A salary record for {record.employee_id.name} already exists for this month."
                )

    def action_generate_salary_report_pdf(self):
        """Generate the payment slip for the selected employee."""
        for record in self:
            if not record.employee_id:
                raise ValidationError(
                    "Employee must be selected to generate the payment slip."
                )
            return self.generate_salary_report_pdf(employee_id=record.employee_id.id)

    def generate_salary_report_pdf(self, employee_id=None, export_type="pdf"):
        """Generate and download the latest salary payment report."""
        _logger.info("Starting salary report generation...")

        where_clauses = ["es.is_paid = true"]
        params = []

        if employee_id:
            where_clauses.append("es.employee_id = %s AND es.id = %s")
            params.extend([employee_id, self.id])

        where_clause = " AND ".join(where_clauses)

        query = f"""
            SELECT
                e.name AS employee_name,
                es.salary_date,
                e.salary,
                e.bonus,
                es.deductions,
                es.advance_deduction,
                es.total_salary,
                e.staff_id
            FROM
                idil_employee_salary es
            INNER JOIN
                idil_employee e ON es.employee_id = e.id
            WHERE
                {where_clause}
            ORDER BY
                es.salary_date DESC
            LIMIT 1;  -- Fetch only the latest payment
        """
        _logger.info(f"Executing query: {query} with params: {params}")
        self.env.cr.execute(query, tuple(params))
        results = self.env.cr.fetchall()
        _logger.info(f"Query results: {results}")

        if not results:
            raise ValidationError("No payment records found for the selected employee.")

        record = results[0]
        report_data = {
            "employee_name": record[0],
            "salary_date": record[1].strftime("%Y-%m-%d"),
            "basic_salary": record[2] or 0,
            "bonus": record[3] or 0,
            "deductions": record[4] or 0,
            "advance_deduction": record[5] or 0,
            "total_salary": record[6] or 0,
            "staff_id": record[7],
        }

        company = self.env.company  # Fetch active company details

        if export_type == "pdf":
            _logger.info("Generating PDF...")
            output = io.BytesIO()
            doc = SimpleDocTemplate(output, pagesize=landscape(letter))
            elements = []

            styles = getSampleStyleSheet()
            title_style = styles["Title"]
            normal_style = styles["Normal"]

            # Center alignment for the company information
            centered_style = styles["Title"].clone("CenteredStyle")
            centered_style.alignment = TA_CENTER
            centered_style.fontSize = 14
            centered_style.leading = 20

            normal_centered_style = styles["Normal"].clone("NormalCenteredStyle")
            normal_centered_style.alignment = TA_CENTER
            normal_centered_style.fontSize = 10
            normal_centered_style.leading = 12

            # Header with Company Name, Address, and Logo
            if company.logo:
                logo = Image(
                    io.BytesIO(base64.b64decode(company.logo)), width=60, height=60
                )
                logo.hAlign = "CENTER"  # Center-align the logo
                elements.append(logo)

            # Add company name and address
            elements.append(Paragraph(f"<b>{company.name}</b>", centered_style))
            elements.append(
                Paragraph(
                    f"{company.street}, {company.city}, {company.country_id.name}",
                    normal_centered_style,
                )
            )
            elements.append(
                Paragraph(
                    f"Phone: {company.phone} | Email: {company.email}",
                    normal_centered_style,
                )
            )
            elements.append(Spacer(1, 12))

            # Unified Table: Employee Details and Payment Section
            payment_table_data = [
                # Header for Payment Slip Voucher
                ["", "PAYMENT SLIP VOUCHER", ""],  # Title row spanning multiple columns
                ["Employee Details", "", "", ""],  # Sub-header for Employee Details
                # Employee Details Rows
                [
                    "Employee Name",
                    report_data["employee_name"],
                    "Salary Date",
                    report_data["salary_date"],
                ],
                ["Employee ID", report_data["staff_id"], "Pay Cycle", "Monthly"],
                ["Bank Details", "Bank XYZ", "", ""],
                # Header for Earnings and Deductions
                ["Earnings", "Amount", "Deductions", "Amount"],
                # Payment Details Rows
                [
                    "Basic Salary",
                    f"${report_data['basic_salary']:,.2f}",
                    "Deductions",
                    f"${report_data['deductions']:,.2f}",
                ],
                [
                    "Bonus",
                    f"${report_data['bonus']:,.2f}",
                    "Advance Deduction",
                    f"${report_data['advance_deduction']:,.2f}",
                ],
                [
                    "Gross Earnings",
                    f"${report_data['basic_salary'] + report_data['bonus']:,.2f}",
                    "Total Deductions",
                    f"${report_data['deductions'] + report_data['advance_deduction']:,.2f}",
                ],
                # Net Pay Row
                [
                    "Net Pay",
                    f"${(report_data['basic_salary'] + report_data['bonus']) - (report_data['deductions'] + report_data['advance_deduction']):,.2f}",
                    "",
                    "",
                ],
            ]

            # Define the table layout and styling
            payment_table_layout = Table(
                payment_table_data, colWidths=[150, 200, 150, 200]
            )
            payment_table_layout.setStyle(
                TableStyle(
                    [
                        # Title Row Styling
                        (
                            "SPAN",
                            (1, 0),
                            (2, 0),
                        ),  # Span the title row across multiple columns
                        ("ALIGN", (1, 0), (2, 0), "CENTER"),
                        ("FONTNAME", (1, 0), (2, 0), "Helvetica-Bold"),
                        ("FONTSIZE", (1, 0), (2, 0), 14),
                        ("TEXTCOLOR", (1, 0), (2, 0), colors.HexColor("#B6862D")),
                        ("BOTTOMPADDING", (1, 0), (2, 0), 12),
                        # Employee Details Header Styling
                        ("SPAN", (0, 1), (3, 1)),  # Span Employee Details header
                        ("BACKGROUND", (0, 1), (3, 1), colors.HexColor("#B6862D")),
                        ("TEXTCOLOR", (0, 1), (3, 1), colors.white),
                        ("ALIGN", (0, 1), (3, 1), "CENTER"),
                        ("FONTNAME", (0, 1), (3, 1), "Helvetica-Bold"),
                        ("FONTSIZE", (0, 1), (3, 1), 12),
                        ("BOTTOMPADDING", (0, 1), (3, 1), 8),
                        # Employee Details Rows Styling
                        ("BACKGROUND", (0, 2), (-1, 4), colors.HexColor("#F0F0F0")),
                        ("ALIGN", (0, 2), (-1, 4), "LEFT"),
                        ("FONTNAME", (0, 2), (-1, 4), "Helvetica"),
                        ("FONTSIZE", (0, 2), (-1, 4), 10),
                        ("LEFTPADDING", (0, 2), (-1, 4), 10),
                        # Earnings and Deductions Header Styling
                        ("BACKGROUND", (0, 5), (-1, 5), colors.HexColor("#B6862D")),
                        ("TEXTCOLOR", (0, 5), (-1, 5), colors.white),
                        ("ALIGN", (0, 5), (-1, 5), "CENTER"),
                        ("FONTNAME", (0, 5), (-1, 5), "Helvetica-Bold"),
                        ("FONTSIZE", (0, 5), (-1, 5), 12),
                        ("BOTTOMPADDING", (0, 5), (-1, 5), 8),
                        # Payment Details Rows Styling
                        ("ALIGN", (1, 6), (1, -1), "RIGHT"),
                        ("ALIGN", (3, 6), (3, -1), "RIGHT"),
                        ("FONTNAME", (0, 6), (-1, -1), "Helvetica"),
                        ("FONTSIZE", (0, 6), (-1, -1), 10),
                        ("GRID", (0, 2), (-1, -1), 0.5, colors.grey),
                    ]
                )
            )
            elements.extend(
                [payment_table_layout, Spacer(1, 12)]
            )  # Add table to the document

            # Payment Signature Section
            hr_name = self.env.user.name  # Fetch the current HR (user) name
            employee_name = report_data[
                "employee_name"
            ]  # Employee name from the report data

            signature_table = [
                [
                    f"Prepared by (HR): {hr_name}",
                    "______________________",
                    f"Received by (Employee): {employee_name}",
                    "______________________",
                ],
            ]
            signature_table_layout = Table(
                signature_table, colWidths=[200, 150, 200, 150]
            )
            signature_table_layout.setStyle(
                TableStyle(
                    [
                        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                        ("ALIGN", (1, 1), (-1, -1), "LEFT"),
                        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
                    ]
                )
            )
            elements.extend([signature_table_layout, Spacer(1, 24)])

            # Footer
            current_datetime = fields.Datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            footer = Paragraph(
                f"<b>Generated by:</b> {self.env.user.name} | <b>Date:</b> {current_datetime}",
                normal_style,
            )
            elements.append(footer)

            # Build PDF
            try:
                doc.build(elements)
            except Exception as e:
                _logger.error(f"Error building PDF: {e}")
                raise

            # Save PDF as attachment and provide download link
            output.seek(0)
            attachment = self.env["ir.attachment"].create(
                {
                    "name": "Payment_Slip.pdf",
                    "type": "binary",
                    "datas": base64.b64encode(output.read()),
                    "mimetype": "application/pdf",
                }
            )
            output.close()
            _logger.info(f"PDF successfully generated: Attachment ID {attachment.id}")

            return {
                "type": "ir.actions.act_url",
                "url": "/web/content/%s?download=true" % attachment.id,
                "target": "new",
            }

        return report_data

    def generate_balance_sheet_report_pdf(self, export_type="pdf"):
        """Generate and download the balance sheet report using the provided query."""
        _logger.info("Starting balance sheet report generation...")

        # Use the provided query to fetch balance sheet data
        query = """
        SELECT 
            ac.code, 
            ac.name,
            SUM(bl.dr_amount - bl.cr_amount) AS balance
        FROM 
            idil_chart_account ac
        JOIN 
            idil_transaction_bookingline bl
            ON ac.id = bl.account_number
        WHERE 
            ac."FinancialReporting" = 'BS'
            AND ac.header_name = 'Assets'
            AND bl.transaction_date <= '2024-12-14'
        GROUP BY 
            ac.code, ac.name
        UNION ALL
        SELECT
            'TOTAL' AS code,
            'Total Assets' AS name,
            SUM(bl.dr_amount - bl.cr_amount) AS balance
        FROM 
            idil_chart_account ac
        JOIN 
            idil_transaction_bookingline bl
            ON ac.id = bl.account_number
        WHERE 
            ac."FinancialReporting" = 'BS'
            AND ac.header_name = 'Assets'
            AND bl.transaction_date <= '2024-12-14'
        UNION ALL
        SELECT 
            ac.code, 
            ac.name,
            SUM(bl.dr_amount - bl.cr_amount) AS balance
        FROM 
            idil_chart_account ac
        JOIN 
            idil_transaction_bookingline bl
            ON ac.id = bl.account_number
        WHERE 
            ac."FinancialReporting" = 'BS'
            AND ac.header_name = 'Liabilities'
            AND bl.transaction_date <= '2024-12-14'
        GROUP BY 
            ac.code, ac.name
        UNION ALL
        SELECT
            'TOTAL' AS code,
            'Total Liabilities' AS name,
            SUM(bl.dr_amount - bl.cr_amount) AS balance
        FROM 
            idil_chart_account ac
        JOIN 
            idil_transaction_bookingline bl
            ON ac.id = bl.account_number
        WHERE 
            ac."FinancialReporting" = 'BS'
            AND ac.header_name = 'Liabilities'
            AND bl.transaction_date <= '2024-12-14'
        UNION ALL
        SELECT 
            ac.code, 
            ac.name,
            SUM(bl.dr_amount - bl.cr_amount) AS balance
        FROM 
            idil_chart_account ac
        JOIN 
            idil_transaction_bookingline bl
            ON ac.id = bl.account_number
        WHERE 
            ac."FinancialReporting" = 'BS'
            AND ac.header_name = 'Owner''s Equity'
            AND bl.transaction_date <= '2024-12-14'
        GROUP BY 
            ac.code, ac.name
        UNION ALL
        SELECT
            'TOTAL' AS code,
            'Total Equity' AS name,
            SUM(bl.dr_amount - bl.cr_amount) AS balance
        FROM 
            idil_chart_account ac
        JOIN 
            idil_transaction_bookingline bl
            ON ac.id = bl.account_number
        WHERE 
            ac."FinancialReporting" = 'BS'
            AND ac.header_name = 'Owner''s Equity'
            AND bl.transaction_date <= '2024-12-14'
        UNION ALL
        SELECT 
            ac.code, 
            ac.name,
            SUM(bl.dr_amount - bl.cr_amount) AS balance
        FROM 
            idil_chart_account ac
        JOIN 
            idil_transaction_bookingline bl
            ON ac.id = bl.account_number
        WHERE 
            ac."FinancialReporting" = 'PL'
            AND bl.transaction_date <= '2024-12-14'
        GROUP BY 
            ac.code, ac.name
        UNION ALL
        SELECT
            'TOTAL' AS code,
            'Total Profit/Loss' AS name,
            SUM(bl.dr_amount - bl.cr_amount) AS balance
        FROM 
            idil_chart_account ac
        JOIN 
            idil_transaction_bookingline bl
            ON ac.id = bl.account_number
        WHERE 
            ac."FinancialReporting" = 'PL'
            AND bl.transaction_date <= '2024-12-14';
        """

        _logger.info(f"Executing query: {query}")
        self.env.cr.execute(query)
        results = self.env.cr.fetchall()
        _logger.info(f"Query results: {results}")

        if not results:
            raise ValidationError("No financial records found for the balance sheet.")

        # Prepare data for the report
        report_data = {"assets": [], "liabilities": [], "equity": [], "profit_loss": []}

        # Classify results into appropriate categories
        for record in results:
            code, name, balance = record

            if name == "Total Assets" or name.startswith("Asset"):
                report_data["assets"].append((name, balance))
            elif name == "Total Liabilities" or name.startswith("Liability"):
                report_data["liabilities"].append((name, balance))
            elif name == "Total Equity" or name.startswith("Owner"):
                report_data["equity"].append((name, balance))
            elif name == "Total Profit/Loss" or name.startswith("Profit/Loss"):
                report_data["profit_loss"].append((name, balance))

        company = self.env.company  # Fetch active company details

        if export_type == "pdf":
            _logger.info("Generating PDF...")
            output = BytesIO()
            doc = SimpleDocTemplate(output, pagesize=landscape(letter))
            elements = []

            styles = getSampleStyleSheet()
            title_style = styles["Title"]
            normal_style = styles["Normal"]

            # Center alignment for the company information
            centered_style = styles["Title"].clone("CenteredStyle")
            centered_style.alignment = TA_CENTER
            centered_style.fontSize = 14
            centered_style.leading = 20

            normal_centered_style = styles["Normal"].clone("NormalCenteredStyle")
            normal_centered_style.alignment = TA_CENTER
            normal_centered_style.fontSize = 10
            normal_centered_style.leading = 12

            # Header with Company Name, Address, and Logo
            if company.logo:
                logo = Image(
                    io.BytesIO(base64.b64decode(company.logo)), width=60, height=60
                )
                logo.hAlign = "CENTER"  # Center-align the logo
                elements.append(logo)

            # Add company name and address
            elements.append(Paragraph(f"<b>{company.name}</b>", centered_style))
            elements.append(
                Paragraph(
                    f"{company.street}, {company.city}, {company.country_id.name}",
                    normal_centered_style,
                )
            )
            elements.append(
                Paragraph(
                    f"Phone: {company.phone} | Email: {company.email}",
                    normal_centered_style,
                )
            )
            elements.append(Spacer(1, 12))

            # Balance Sheet Table
            balance_sheet_table_data = [
                ["", "Balance Sheet", ""],  # Title row spanning multiple columns
                ["Assets", "", "", ""],  # Sub-header for Assets
            ]

            # Assets Rows
            for asset in report_data["assets"]:
                balance_sheet_table_data.append([asset[0], f"${asset[1]:,.2f}", "", ""])

            balance_sheet_table_data.append(
                ["Liabilities", "", "", ""]
            )  # Sub-header for Liabilities

            # Liabilities Rows
            for liability in report_data["liabilities"]:
                balance_sheet_table_data.append(
                    [liability[0], f"${liability[1]:,.2f}", "", ""]
                )

            balance_sheet_table_data.append(
                ["Equity", "", "", ""]
            )  # Sub-header for Equity

            # Equity Rows
            for equity in report_data["equity"]:
                balance_sheet_table_data.append(
                    [equity[0], f"${equity[1]:,.2f}", "", ""]
                )

            balance_sheet_table_data.append(
                ["Profit/Loss", "", "", ""]
            )  # Sub-header for Profit/Loss

            # Profit/Loss Rows
            for profit_loss in report_data["profit_loss"]:
                balance_sheet_table_data.append(
                    [profit_loss[0], f"${profit_loss[1]:,.2f}", "", ""]
                )

            # Define the table layout and styling
            balance_sheet_table_layout = Table(
                balance_sheet_table_data, colWidths=[250, 150, 150, 150]
            )
            balance_sheet_table_layout.setStyle(
                TableStyle(
                    [
                        # Title Row Styling
                        (
                            "SPAN",
                            (1, 0),
                            (2, 0),
                        ),  # Span the title row across multiple columns
                        ("ALIGN", (1, 0), (-1, -1), "CENTER"),
                        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                        ("BACKGROUND", (0, 0), (-1, 0), colors.darkblue),
                        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                        ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
                    ]
                )
            )

            elements.append(balance_sheet_table_layout)  # Add table to the document
            doc.build(elements)

            # Get PDF data
            pdf_data = output.getvalue()
            output.close()
            _logger.info(f"Document is being built with {len(elements)} elements.")
            _logger.info("PDF generation complete, preparing response...")
            return pdf_data
