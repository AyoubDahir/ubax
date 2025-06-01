import base64
import calendar
import io

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

_logger = logging.getLogger(__name__)


class IdilEmployeeSalaryAdvance(models.Model):
    _name = 'idil.employee.salary.advance'
    _description = 'Employee Salary Advance'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'request_date desc'

    employee_id = fields.Many2one('idil.employee', string='Employee', required=True, tracking=True)
    account_id = fields.Many2one('idil.chart.account', string='Account', required=True)

    request_date = fields.Date(string='Request Date', default=fields.Date.context_today, required=True, tracking=True)
    advance_amount = fields.Monetary(string='Advance Amount', required=True, tracking=True)
    currency_id = fields.Many2one('res.currency', string='Currency', related='employee_id.currency_id', readonly=True,
                                  tracking=True)
    state = fields.Selection([
        ('draft', 'Draft'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
        ('deducted', 'Deducted')
    ], string='Status', default='draft', readonly=True, tracking=True)
    remarks = fields.Text(string='Remarks', tracking=True)
    create_uid = fields.Many2one('res.users', string='Created By', readonly=True, default=lambda self: self.env.user,
                                 tracking=True)
    employee_salary = fields.Monetary(
        string="Employee Salary",
        currency_field='currency_id',
        compute='_compute_employee_salary',
        store=True, tracking=True
    )
    remaining_salary = fields.Monetary(
        string="Remaining Salary",
        currency_field='currency_id',
        compute='_compute_remaining_salary',
        store=True, tracking=True
    )

    @api.depends('employee_id')
    def _compute_employee_salary(self):
        for record in self:
            record.employee_salary = record.employee_id.salary if record.employee_id else 0.0

    @api.depends('employee_salary', 'advance_amount', 'request_date')
    def _compute_remaining_salary(self):
        for record in self:
            if not record.request_date:
                record.remaining_salary = 0.0
            else:
                request_month = fields.Date.from_string(record.request_date).month
                request_year = fields.Date.from_string(record.request_date).year

                # Check if the salary for the selected month is already paid
                salary_paid = self.env['idil.employee.salary'].search_count([
                    ('employee_id', '=', record.employee_id.id),
                    ('salary_date', '>=', f'{request_year}-{request_month:02d}-01'),
                    ('salary_date', '<=',
                     f'{request_year}-{request_month:02d}-{calendar.monthrange(request_year, request_month)[1]}'),
                    ('is_paid', '=', True)
                ]) > 0

                if salary_paid:
                    record.remaining_salary = 0.0
                else:
                    # Fetch all advances for the same month and year
                    total_advance_taken = sum(
                        self.env['idil.employee.salary.advance'].search([
                            ('employee_id', '=', record.employee_id.id),
                            ('state', 'in', ['approved', 'deducted']),
                            ('request_date', '>=', f'{request_year}-{request_month:02d}-01'),
                            ('request_date', '<=',
                             f'{request_year}-{request_month:02d}-{calendar.monthrange(request_year, request_month)[1]}')
                        ]).mapped('advance_amount')
                    )
                    if total_advance_taken:
                        record.remaining_salary = record.employee_salary - total_advance_taken - record.advance_amount
                    else:
                        record.remaining_salary = record.employee_salary - record.advance_amount

    @api.constrains('advance_amount', 'remaining_salary', 'request_date')
    def _check_advance_amount(self):
        for record in self:
            if not record.request_date:
                raise ValidationError("Request date is required before requesting an advance.")
            if record.advance_amount <= 0:
                raise ValidationError("Advance amount must be greater than zero.")
            if record.remaining_salary <= 0:
                raise ValidationError(
                    "The advance amount exceeds the employee's remaining salary for the selected month."
                )

    def approve_advance(self):
        for record in self:
            if record.state != 'draft':
                raise ValidationError("Only advances in draft state can be approved.")

            # Prevent self-approval
            if record.create_uid == self.env.user:
                if not record.employee_id.maker_checker:  # Proper boolean check
                    raise ValidationError("You cannot approve an advance that you created.")

            # Fetch the account record for "Salary Advance Expense"
            salary_advance_expense_account = self.env['idil.chart.account'].search([
                ('name', '=', 'Salary Expense')  # Assuming the type is COGS, adjust based on your setup
            ], limit=1)

            if not salary_advance_expense_account:
                raise ValidationError("The Salary Advance Expense account is not configured in the chart of accounts.")

            # Fetch the trx source record for "Salary Advance Expense"
            salary_advance_expense_trx_source = self.env['idil.transaction.source'].search([
                ('name', '=', 'Salary Advance Expense')  # Assuming the type is COGS, adjust based on your setup
            ], limit=1)

            # Compute the balance of the credit account
            credit_account_balance = self.env['idil.transaction_bookingline'].search_read([
                ('account_number', '=', record.account_id.id)
            ], ['dr_amount', 'cr_amount'])

            # Calculate the net balance
            net_balance = sum(line['dr_amount'] for line in credit_account_balance) - sum(
                line['cr_amount'] for line in credit_account_balance)

            # Validate the balance
            if net_balance < record.advance_amount:
                raise ValidationError(
                    f"Insufficient balance in the account {record.account_id.name}. "
                    f"Available balance: {net_balance}, required: {record.advance_amount}."
                )

            # Create a Transaction Booking record
            transaction_booking = self.env['idil.transaction_booking'].create({
                'transaction_number': self.env['ir.sequence'].next_by_code('idil.transaction_booking') or '/',
                'reffno': record.id,  # Reference to salary advance request
                'employee_salary_advance_id': record.id,
                'employee_id': record.employee_id.id,
                'payment_method': 'cash',
                'payment_status': 'paid',
                'trx_source_id': salary_advance_expense_trx_source.id,
                'trx_date': record.request_date,
                'amount': record.advance_amount,
                'amount_paid': record.advance_amount,
                'remaining_amount': 0,
            })

            # Create a Transaction Booking Line for the debit entry (Salary Advance Expense)
            self.env['idil.transaction_bookingline'].create({
                'transaction_booking_id': transaction_booking.id,
                'employee_salary_advance_id': record.id,
                'description': 'Salary Advance Approved',
                'account_number': salary_advance_expense_account.id,
                'transaction_type': 'dr',
                'dr_amount': record.advance_amount,
                'cr_amount': 0,
                'transaction_date': record.request_date,
            })

            self.env['idil.transaction_bookingline'].create({
                'transaction_booking_id': transaction_booking.id,
                'employee_salary_advance_id': record.id,
                'description': 'Salary Advance Paid',
                'account_number': record.account_id.id,
                'transaction_type': 'cr',
                'cr_amount': record.advance_amount,
                'dr_amount': 0,
                'transaction_date': record.request_date,
            })

            record.state = 'approved'

    def reject_advance(self):
        for record in self:
            if record.state != 'draft':
                raise ValidationError("Only advances in draft state can be rejected.")
            record.state = 'rejected'

    def mark_as_deducted(self):
        for record in self:
            if record.state != 'approved':
                raise ValidationError("Only approved advances can be marked as deducted.")
            record.state = 'deducted'

    # Prevent deletion of deducted advances
    def unlink(self):
        for record in self:
            if record.state == 'deducted':
                raise ValidationError("You cannot delete an advance that has already been deducted.")
        return super(IdilEmployeeSalaryAdvance, self).unlink()

    def write(self, vals):
        result = super(IdilEmployeeSalaryAdvance, self).write(vals)

        for record in self:
            # Find the associated transaction booking record
            transaction_booking = self.env['idil.transaction_booking'].search(
                [('employee_salary_advance_id', '=', record.id)],
                limit=1
            )

            if transaction_booking:
                # Update the transaction booking record
                transaction_booking.write({
                    'trx_date': record.request_date,
                    'amount': record.advance_amount,
                    'amount_paid': record.advance_amount,
                    'remaining_amount': 0,
                })

        for record in self:
            # Directly update the booking lines where the transaction booking ID matches
            # and the salary advance ID matches
            booking_lines = self.env['idil.transaction_bookingline'].search([
                ('transaction_booking_id.employee_salary_advance_id', '=', record.id)
            ])

            for line in booking_lines:
                if line.transaction_type == 'dr':
                    line.write({
                        'dr_amount': record.advance_amount,
                        'transaction_date': record.request_date,
                    })
                elif line.transaction_type == 'cr':
                    line.write({
                        'cr_amount': record.advance_amount,
                        'transaction_date': record.request_date,
                    })

        return result

    def action_generate_salary_advance_slip_pdf(self):
        """Generate the payment slip for the selected employee."""
        for record in self:
            if not record.employee_id:
                raise ValidationError("Employee must be selected to generate the payment slip.")
            # Pass employee ID and record ID
            return self.generate_salary_advance_report_pdf(employee_id=record.employee_id.id, record_id=record.id)

    def generate_salary_advance_report_pdf(self, employee_id=None, record_id=None, export_type="pdf"):
        """Generate and download the latest salary advance report."""
        _logger.info("Starting salary advance report generation...")

        if not employee_id or not record_id:
            raise ValidationError("Employee ID and record ID are required to generate the report.")

        # Updated query to use record ID
        query = f"""
             SELECT
                e.name AS employee_name,
                esd.request_date,
                esd.advance_amount,
                e.staff_id, 
                private_phone,
                ed.name as department,
                ep.name as position
            FROM
                idil_employee_salary_advance esd
            INNER JOIN
                idil_employee e ON esd.employee_id = e.id
            INNER JOIN idil_employee_department ed ON e.department_id=ed.id
            INNER JOIN idil_employee_position ep on e.position_id =ep.id
            WHERE
                esd.employee_id = %s AND esd.id = %s
            ORDER BY
                esd.request_date DESC
            LIMIT 1;  -- Fetch only the latest advance
        """
        params = [employee_id, record_id]

        _logger.info(f"Executing query: {query} with params: {params}")
        self.env.cr.execute(query, tuple(params))
        results = self.env.cr.fetchall()
        _logger.info(f"Query results: {results}")

        if not results:
            raise ValidationError("No salary advance records found for the selected employee.")

        # Extract the first (latest) record
        record = results[0]
        report_data = {
            'employee_name': record[0],
            'request_date': record[1].strftime('%Y-%m-%d'),
            'advance_amount': record[2],

            'staff_id': record[3],
            'private_phone': record[4],
            'department': record[5],
            'position': record[6],

        }

        company = self.env.company  # Fetch active company details

        if export_type == "pdf":
            try:
                _logger.info("Generating PDF...")
                output = io.BytesIO()
                doc = SimpleDocTemplate(output, pagesize=landscape(letter))
                elements = []

                styles = getSampleStyleSheet()
                centered_style = styles['Title'].clone('CenteredStyle')
                centered_style.alignment = TA_CENTER
                centered_style.fontSize = 14
                centered_style.leading = 20

                normal_centered_style = styles['Normal'].clone('NormalCenteredStyle')
                normal_centered_style.alignment = TA_CENTER
                normal_centered_style.fontSize = 10
                normal_centered_style.leading = 12

                # Add Company Logo and Info
                if company.logo:
                    logo_data = base64.b64decode(company.logo)
                    logo = Image(io.BytesIO(logo_data), width=60, height=60)
                    logo.hAlign = 'CENTER'
                    elements.append(logo)

                elements.append(Paragraph(f"<b>{company.name}</b>", centered_style))
                address = f"{company.street}, {company.city}, {company.country_id.name}" if company.street else "No Address"
                elements.append(Paragraph(address, normal_centered_style))
                elements.append(
                    Paragraph(f"Phone: {company.phone or 'N/A'} | Email: {company.email or 'N/A'}",
                              normal_centered_style))
                elements.append(Spacer(1, 12))

                # Payment Table
                table_data = [
                    ["Employee Name", report_data['employee_name'], "staff_id", report_data['staff_id']],
                    ["private_phone", report_data['private_phone'], "Request Date", report_data['request_date']],
                    ["department", report_data['department'], "position", report_data['position']],
                    ["Advance Amount", f"${report_data['advance_amount']:,.2f}"],
                ]

                table = Table(table_data, colWidths=[150, 150])
                table.setStyle(TableStyle([
                    ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#B6862D")),
                    ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                    ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                    ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                    ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
                ]))
                elements.append(table)

                # Define the table layout and styling

                # Payment Signature Section
                hr_name = self.env.user.name  # Fetch the current HR (user) name
                employee_name = report_data['employee_name']  # Employee name from the report data

                signature_table = [
                    [f"Prepared by (HR): {hr_name}", "______________________",
                     f"Received by (Employee): {employee_name}",
                     "______________________"],
                ]
                signature_table_layout = Table(signature_table, colWidths=[200, 150, 200, 150])
                signature_table_layout.setStyle(TableStyle([
                    ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
                    ('ALIGN', (1, 1), (-1, -1), 'LEFT'),
                    ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
                ]))
                elements.extend([signature_table_layout, Spacer(1, 24)])

                # Return as binary content in Odoo
                doc.build(elements)  # Finalizes the PDF document
                output.seek(0)  # Ensure the pointer is at the start of the buffer
                pdf_content = output.read()  # Read the full PDF content

                attachment = self.env['ir.attachment'].create({
                    'name': 'Payment_Slip.pdf',
                    'type': 'binary',
                    'datas': base64.b64encode(pdf_content),  # Use pdf_content directly instead of output.read()
                    'res_model': self._name,
                    'res_id': self.id,
                    'mimetype': 'application/pdf',
                })

                return {
                    'type': 'ir.actions.act_url',
                    'url': f'/web/content/{attachment.id}?download=true',
                    'target': 'new',
                }

            except Exception as e:
                _logger.error(f"Error generating PDF: {e}")
                raise ValidationError("Failed to generate salary advance report. Please try again.")
