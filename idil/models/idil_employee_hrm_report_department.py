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


class HRMSalaryDepartmentReportWizard(models.TransientModel):
    _name = 'report.hrm.salary.department.report'
    _description = 'HRM Salary Department Report Wizard'

    start_date = fields.Date(string="Start Date", required=True)
    end_date = fields.Date(string="End Date", required=True)

    def generate_salary_department_report_pdf(self, export_type="pdf"):
        _logger.info("Starting salary department report generation...")

        # Use the values from the wizard fields
        start_date = self.start_date
        end_date = self.end_date

        where_clauses = ["es.salary_date BETWEEN %s AND %s"]
        params = [start_date, end_date]

        where_clause = " AND ".join(where_clauses)

        query = f"""      
                select 
                      ep.name, 
                      sum(e.salary) as salary, 
                      sum(e.bonus) as bonus,
                      sum(es.deductions) as deductions,
                      sum(es.advance_deduction) as advance_salary,
                      sum(es.total_salary) as net_salary
                from idil_employee e
                inner join idil_employee_department ep
                on e.department_id = ep.id
                inner join idil_employee_salary es
                on e.id=es.employee_id
                where
                    {where_clause}
                 group by   
                 ep.name
                    ORDER BY 
                    ep.name;
                   """

        _logger.info(f"Executing query: {query} with params: {params}")
        self.env.cr.execute(query, tuple(params))
        results = self.env.cr.fetchall()
        _logger.info(f"Query results: {results}")

        report_data = [
            {
                'job_position': row[0],
                'basic_salary': row[1] if row[1] is not None else 0.00,
                'allowances': row[2] if row[2] is not None else 0.00,
                'deductions': row[3] if row[3] is not None else 0.00,
                'advance': row[4] if row[4] is not None else 0.00,
                'net_salary': row[5] if row[5] is not None else 0.00,
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

            title = Paragraph("<b>Salary Summary By Department Report</b>", styles['Title'])
            elements.append(title)
            elements.append(Spacer(1, 12))

            subtitle = Paragraph(
                f"<b>Start Date:</b> {start_date.strftime('%m/%d/%Y')} &nbsp;&nbsp;&nbsp; "
                f"<b>End Date:</b> {end_date.strftime('%m/%d/%Y')}", styles['Normal']
            )
            elements.append(subtitle)
            elements.append(Spacer(1, 12))

            data = [
                ['Department', 'Basic Salary', 'Allowances', 'Deductions',
                 'Advance', 'Net Salary']]
            for record in report_data:
                data.append([

                    record['job_position'],
                    f"${record['basic_salary']:,.2f}",
                    f"${record['allowances']:,.2f}",
                    f"${record['deductions']:,.2f}",
                    f"${record['advance']:,.2f}",
                    f"${record['net_salary']:,.2f}",
                ])

            total_basic_salary = sum(r['basic_salary'] for r in report_data if 'basic_salary' in r)
            total_allowances = sum(r['allowances'] for r in report_data if 'allowances' in r)
            total_deductions = sum(r['deductions'] for r in report_data if 'deductions' in r)
            advance = sum(r['advance'] for r in report_data if 'advance' in r)

            total_net_salary = sum(r['net_salary'] for r in report_data if 'net_salary' in r)

            data.append(["Total", f"${total_basic_salary:,.2f}", f"${total_allowances:,.2f}",
                         f"${total_deductions:,.2f}", f"${advance:,.2f}", f"${total_net_salary:,.2f}"])

            table = Table(data, colWidths=[100, 100, 100, 100, 100, 100])
            table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#B6862D")),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ]))
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
                'name': 'HRM_Salary_department_Report.pdf',
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
