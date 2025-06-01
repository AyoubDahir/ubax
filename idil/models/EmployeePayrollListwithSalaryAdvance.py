import io
import base64
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
from reportlab.lib.pagesizes import landscape, letter
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.enums import TA_CENTER
from odoo import models, fields, api, _
import logging

_logger = logging.getLogger(__name__)


class HRMSalaryListReportWizard(models.TransientModel):
    _name = 'report.hrm.salary.list.report'
    _description = 'HRM Salary List Report Wizard'

    start_date = fields.Date(string="Start Date", required=True)
    end_date = fields.Date(string="End Date", required=True)

    def generate_salary_list_report_pdf(self, export_type="pdf"):
        _logger.info("Starting salary list report generation...")

        # Use the values from the wizard fields
        start_date = self.start_date
        end_date = self.end_date

        where_clauses = ["es.request_date BETWEEN %s AND %s"]
        params = [start_date, end_date]

        where_clause = " AND ".join(where_clauses)

        query = f"""      
                
                SELECT 
                e.staff_id,
                e.name,
                ep.name AS position_name,
                SUM(e.salary) AS salary,
                SUM(e.bonus) AS bonus,
                COALESCE(SUM(es.advance_amount), 0) AS advance_salary, -- Default to 0 if no advances
                ((SUM(e.salary) + SUM(e.bonus)) - COALESCE(SUM(es.advance_amount), 0)) AS net_salary ,
                e.private_phone
            FROM 
                idil_employee e
            INNER JOIN 
                idil_employee_position ep ON e.position_id = ep.id
            LEFT JOIN 
                idil_employee_salary_advance es ON e.id = es.employee_id
                AND {where_clause} -- Apply the date filter here
            GROUP BY   
                ep.name, e.name, e.staff_id,e.private_phone
            ORDER BY 
                e.staff_id;

                   """

        _logger.info(f"Executing query: {query} with params: {params}")
        self.env.cr.execute(query, tuple(params))
        results = self.env.cr.fetchall()
        _logger.info(f"Query results: {results}")

        report_data = [
            {
                'staff_id': row[0],
                'staff_name': row[1],
                'job_position': row[2],
                'basic_salary': row[3] if row[3] is not None else 0.00,
                'allowances': row[4] if row[4] is not None else 0.00,
                'advance': row[5] if row[5] is not None else 0.00,
                'net_salary': row[6] if row[6] is not None else 0.00,
                'phone': row[7] if row[7] is not None else 0.00,

            } for row in results
        ]
        company = self.env.company  # Fetch active company details

        if export_type == "pdf":
            _logger.info("Generating PDF...")
            output = io.BytesIO()
            doc = SimpleDocTemplate(output, pagesize=landscape(letter))
            elements = []

            # ------------------------------------------------------------------
            styles = getSampleStyleSheet()
            title_style = styles['Title']
            normal_style = styles['Normal']

            centered_style = styles['Title'].clone('CenteredStyle')
            centered_style.alignment = TA_CENTER
            centered_style.fontSize = 14
            centered_style.leading = 20

            normal_centered_style = styles['Normal'].clone('NormalCenteredStyle')
            normal_centered_style.alignment = TA_CENTER
            normal_centered_style.fontSize = 10
            normal_centered_style.leading = 12

            if company.logo:
                logo = Image(io.BytesIO(base64.b64decode(company.logo)), width=60, height=60)
                logo.hAlign = 'CENTER'
                elements.append(logo)

            elements.append(Paragraph(f"<b>{company.name}</b>", centered_style))
            elements.append(
                Paragraph(f"{company.street}, {company.city}, {company.country_id.name}", normal_centered_style))
            elements.append(Paragraph(f"Phone: {company.phone} | Email: {company.email}", normal_centered_style))
            elements.append(Spacer(1, 12))

            title = Paragraph("<b>Employee Payroll List with Salary Advance</b>", styles['Title'])
            elements.append(title)
            elements.append(Spacer(1, 12))

            subtitle = Paragraph(
                f"<b>Start Date:</b> {start_date.strftime('%m/%d/%Y')} &nbsp;&nbsp;&nbsp; "
                f"<b>End Date:</b> {end_date.strftime('%m/%d/%Y')}", styles['Normal']
            )
            elements.append(subtitle)
            elements.append(Spacer(1, 12))

            data = [
                ['Staff ID', 'Staff Nme', 'Phone', 'Department', 'Basic Salary', 'Allowances',
                 'Advance', 'Net Salary']]
            for record in report_data:
                data.append([

                    record['staff_id'],
                    record['staff_name'],
                    record['phone'],
                    record['job_position'],
                    f"${record['basic_salary']:,.2f}",

                    f"${record['allowances']:,.2f}",
                    f"${record['advance']:,.2f}",
                    f"${record['net_salary']:,.2f}",
                ])

            total_basic_salary = sum(r['basic_salary'] for r in report_data if 'basic_salary' in r)
            total_allowances = sum(r['allowances'] for r in report_data if 'allowances' in r)
            advance = sum(r['advance'] for r in report_data if 'advance' in r)

            total_net_salary = sum(r['net_salary'] for r in report_data if 'net_salary' in r)

            data.append(
                ["Total", '', '', '', f"${total_basic_salary:,.2f}", f"${total_allowances:,.2f}", f"${advance:,.2f}",
                 f"${total_net_salary:,.2f}"])

            table = Table(data, colWidths=[50, 200, 100, 130, 80, 80, 60, 80])
            # table.setStyle(TableStyle([
            #     ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#B6862D")),
            #     ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            #     ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            # ]))
            # elements.append(table)

            # Add table style
            table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#B6862D")),  # Header row background
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),  # Header row text color
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),  # Center align all cells
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),  # Header row font
                ('FONTSIZE', (0, 0), (-1, 0), 12),  # Header row font size
                ('BOTTOMPADDING', (0, 0), (-1, 0), 12),  # Header row padding
                ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),  # Make totals row bold
                ('TEXTCOLOR', (0, -1), (-1, -1), colors.black),  # Totals row text color
                ('BACKGROUND', (0, -1), (-1, -1), colors.lightgrey),  # Totals row background
                ('GRID', (0, 0), (-1, -1), 0.5, colors.black),  # Add gridlines
            ]))

            # Add the table to the elements
            elements.append(table)

            current_datetime = fields.Datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            footer = Paragraph(f"<b>Generated by:</b> {self.env.user.name} | <b>Date:</b> {current_datetime}",
                               styles['Normal'])
            elements.append(Spacer(1, 12))
            elements.append(footer)

            try:
                doc.build(elements)
            except Exception as e:
                _logger.error(f"Error building PDF: {e}")
                raise

            output.seek(0)
            attachment = self.env['ir.attachment'].create({
                'name': 'HRM_Salary_List_Report.pdf',
                'type': 'binary',
                'datas': base64.b64encode(output.read()),
                'mimetype': 'application/pdf',
            })
            output.close()
            _logger.info(f"PDF successfully generated: Attachment ID {attachment.id}")

            return {
                'type': 'ir.actions.act_url',
                'url': '/web/content/%s?download=true' % attachment.id,
                'target': 'new',
            }

        return report_data
