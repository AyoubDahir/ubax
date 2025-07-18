from odoo import models, fields
import base64
import io
from collections import defaultdict
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    Image,
)
from reportlab.lib.pagesizes import landscape, letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors


class CustomerSalesReportWizard(models.TransientModel):
    _name = "idil.customer.sales.report"
    _description = "Customer Sales Report"

    start_date = fields.Date(string="Start Date", required=True)
    end_date = fields.Date(string="End Date", required=True)
    customer_id = fields.Many2one(
        "idil.customer.registration", string="Customer Name", required=True
    )

    # def generate_pdf_report(self):
    #     company = self.env.company
    #     buffer = io.BytesIO()
    #     doc = SimpleDocTemplate(
    #         buffer,
    #         pagesize=landscape(letter),
    #         rightMargin=30,
    #         leftMargin=30,
    #         topMargin=40,
    #         bottomMargin=30,
    #     )
    #     elements = []

    #     styles = getSampleStyleSheet()
    #     header_style = ParagraphStyle(
    #         name="Header",
    #         parent=styles["Title"],
    #         fontSize=18,
    #         textColor=colors.HexColor("#B6862D"),
    #         alignment=1,
    #     )
    #     subtitle_style = ParagraphStyle(
    #         name="Subtitle", parent=styles["Normal"], fontSize=12, alignment=1
    #     )
    #     left_align_style = ParagraphStyle(
    #         name="LeftAlign", parent=styles["Normal"], fontSize=12, alignment=0
    #     )

    #     logo = (
    #         Image(io.BytesIO(base64.b64decode(company.logo)), width=120, height=60)
    #         if company.logo
    #         else Paragraph("<b>No Logo Available</b>", header_style)
    #     )
    #     elements += [
    #         logo,
    #         Spacer(1, 12),
    #         Paragraph(f"<b>{company.name.upper()}</b>", header_style),
    #         Spacer(1, 6),
    #         Paragraph(
    #             f"{company.partner_id.city or ''}, {company.partner_id.country_id.name or ''}<br/>Phone: {company.partner_id.phone or 'N/A'}<br/>Email: {company.partner_id.email or 'N/A'}<br/>Web: {company.website or 'N/A'}",
    #             subtitle_style,
    #         ),
    #         Spacer(1, 20),
    #         Paragraph(
    #             f"<b>Date from:</b> {self.start_date.strftime('%d/%m/%Y')}<br/><b>Date to:</b> {self.end_date.strftime('%d/%m/%Y')}",
    #             left_align_style,
    #         ),
    #         Spacer(1, 12),
    #         Paragraph(
    #             f"<b>Customer:</b> {self.customer_id.name or 'N/A'} &nbsp;&nbsp;&nbsp; ",
    #             left_align_style,
    #         ),
    #         Spacer(1, 12),
    #     ]

    #     # ==== Calculate Previous Balance Before Start Date ====
    #     self.env.cr.execute(
    #         """
    #         SELECT
    #             COALESCE(SUM(tbl.dr_amount), 0) AS total_dr,
    #             COALESCE(SUM(tbl.cr_amount), 0) AS total_cr
    #         FROM idil_customer_registration c
    #         INNER JOIN idil_transaction_booking tb ON c.id = tb.customer_id
    #         INNER JOIN idil_transaction_bookingline tbl
    #             ON tb.id = tbl.transaction_booking_id
    #             AND tbl.account_number = c.account_receivable_id
    #         WHERE c.id = %s
    #         AND DATE(tbl.transaction_date) < %s
    #         """,
    #         (self.customer_id.id, self.start_date),
    #     )
    #     prev_result = self.env.cr.fetchone()
    #     previous_debit = prev_result[0]
    #     previous_credit = prev_result[1]
    #     previous_balance = previous_debit - previous_credit

    #     # Query: get transactions for this customer
    #     self.env.cr.execute(
    #         """
    #         SELECT
    #             tbl.transaction_date,
    #             tb.transaction_number,
    #             tb.reffno,
    #             tbl.account_display,
    #             tbl.description,
    #             tbl.dr_amount,
    #             tbl.cr_amount
    #         FROM idil_customer_registration c
    #         INNER JOIN idil_transaction_booking tb ON c.id = tb.customer_id
    #         INNER JOIN idil_transaction_bookingline tbl
    #             ON tb.id = tbl.transaction_booking_id
    #             AND tbl.account_number = c.account_receivable_id
    #         WHERE c.id = %s
    #         AND DATE(tbl.transaction_date) BETWEEN %s AND %s
    #         ORDER BY tbl.transaction_date ASC;
    #         """,
    #         (self.customer_id.id, self.start_date, self.end_date),
    #     )
    #     rows = self.env.cr.fetchall()

    #     headers = [
    #         "Date",
    #         "TRS No",
    #         "Ref No",
    #         "Description",
    #         "Debit",
    #         "Credit",
    #         "Balance",
    #     ]
    #     data = [headers]

    #     # Add Previous Balance as first row
    #     data.append(
    #         [
    #             "",
    #             "",
    #             "",
    #             "Previous Balance",
    #             f"{previous_debit:,.2f}",
    #             f"{previous_credit:,.2f}",
    #             f"{previous_balance:,.2f}",
    #         ]
    #     )

    #     balance = previous_balance
    #     total_debit = total_credit = 0.0

    #     for row in rows:
    #         (trans_date, trans_no, ref_no, acc_disp, desc, dr, cr) = row
    #         balance += dr - cr
    #         total_debit += dr
    #         total_credit += cr
    #         data.append(
    #             [
    #                 trans_date.strftime("%d/%m/%Y") if trans_date else "",
    #                 trans_no or "",
    #                 ref_no or "",
    #                 desc or "",
    #                 f"{dr:,.2f}" if dr else "",
    #                 f"{cr:,.2f}" if cr else "",
    #                 f"{balance:,.2f}",
    #             ]
    #         )

    #     # Summary Row
    #     data.append(
    #         [
    #             "",
    #             "",
    #             "",
    #             "TOTAL",
    #             f"{total_debit:,.2f}",
    #             f"{total_credit:,.2f}",
    #             f"{balance:,.2f}",
    #         ]
    #     )

    #     # Column widths (unchanged)
    #     col_widths = [60, 50, 110, 270, 90, 90, 90]

    #     table = Table(data, colWidths=col_widths)
    #     style = TableStyle(
    #         [
    #             ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#B6862D")),
    #             ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
    #             ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
    #             ("ALIGN", (0, 0), (-1, -1), "CENTER"),
    #             ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
    #             ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
    #             ("BACKGROUND", (0, -1), (-1, -1), colors.lightgrey),
    #         ]
    #     )
    #     table.setStyle(style)

    #     elements.append(Spacer(1, 20))
    #     elements.append(table)
    #     doc.build(elements)
    #     buffer.seek(0)
    #     pdf_data = buffer.read()

    #     attachment = self.env["ir.attachment"].create(
    #         {
    #             "name": "customer_sales_report.pdf",
    #             "type": "binary",
    #             "datas": base64.b64encode(pdf_data),
    #             "mimetype": "application/pdf",
    #         }
    #     )

    #     return {
    #         "type": "ir.actions.act_url",
    #         "url": f"/web/content/{attachment.id}?download=true",
    #         "target": "new",
    #     }
    def generate_pdf_report(self):
        company = self.env.company
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=landscape(letter),
            rightMargin=30,
            leftMargin=30,
            topMargin=40,
            bottomMargin=30,
        )
        elements = []

        styles = getSampleStyleSheet()
        header_style = ParagraphStyle(
            name="Header",
            parent=styles["Title"],
            fontSize=18,
            textColor=colors.HexColor("#B6862D"),
            alignment=1,
        )
        subtitle_style = ParagraphStyle(
            name="Subtitle", parent=styles["Normal"], fontSize=12, alignment=1
        )
        left_align_style = ParagraphStyle(
            name="LeftAlign", parent=styles["Normal"], fontSize=12, alignment=0
        )

        logo = (
            Image(io.BytesIO(base64.b64decode(company.logo)), width=120, height=60)
            if company.logo
            else Paragraph("<b>No Logo Available</b>", header_style)
        )
        elements += [
            logo,
            Spacer(1, 12),
            Paragraph(f"<b>{company.name.upper()}</b>", header_style),
            Spacer(1, 6),
            Paragraph(
                f"{company.partner_id.city or ''}, {company.partner_id.country_id.name or ''}<br/>Phone: {company.partner_id.phone or 'N/A'}<br/>Email: {company.partner_id.email or 'N/A'}<br/>Web: {company.website or 'N/A'}",
                subtitle_style,
            ),
            Spacer(1, 20),
            Paragraph(
                f"<b>Date from:</b> {self.start_date.strftime('%d/%m/%Y')}<br/><b>Date to:</b> {self.end_date.strftime('%d/%m/%Y')}",
                left_align_style,
            ),
            Spacer(1, 12),
            Paragraph(
                f"<b>Customer:</b> {self.customer_id.name or 'N/A'} &nbsp;&nbsp;&nbsp; ",
                left_align_style,
            ),
            Spacer(1, 12),
        ]

        # ==== Calculate Previous Balance Before Start Date ====
        self.env.cr.execute(
            """
            SELECT
                COALESCE(SUM(tbl.dr_amount), 0) AS total_dr,
                COALESCE(SUM(tbl.cr_amount), 0) AS total_cr
            FROM idil_customer_registration c
            INNER JOIN idil_transaction_booking tb ON c.id = tb.customer_id
            INNER JOIN idil_transaction_bookingline tbl
                ON tb.id = tbl.transaction_booking_id
                AND tbl.account_number = c.account_receivable_id
            WHERE c.id = %s
            AND tbl.transaction_date < %s
            """,
            (self.customer_id.id, self.start_date),
        )
        prev_result = self.env.cr.fetchone()
        previous_debit = prev_result[0]
        previous_credit = prev_result[1]
        previous_balance = previous_debit - previous_credit

        # ==== Use your exact requested query here with filtering ====
        # Add filters to both parts of the UNION for customer and date range

        query = f"""
            SELECT  
                c.name, 
                c.phone, 
               
                ts.name as method,
                tb.reffno,
                tbl.account_display,
                tbl.description,
                tbl.transaction_type,
                tbl.dr_amount, 
                tbl.cr_amount,
                tbl.id AS line_id,
                tbl.transaction_date
            FROM idil_customer_registration c
            INNER JOIN idil_transaction_booking tb ON c.id = tb.customer_id
            INNER JOIN idil_transaction_bookingline tbl 
                ON tb.id = tbl.transaction_booking_id 
                AND tbl.account_number = c.account_receivable_id
            inner join idil_transaction_source ts
                on tb.trx_source_id = ts.id

            WHERE c.id = %s
            AND tbl.transaction_date BETWEEN %s AND %s

            UNION ALL

            SELECT  
                c.name, 
                c.phone, 
           
                ts.name as method,
                tb.reffno,
                tbl.account_display,
                tbl.description,
                tbl.transaction_type,
                tbl.dr_amount AS dr_amount,
                tbl.dr_amount AS cr_amount,
                tbl.id AS line_id,
                tbl.transaction_date
            FROM idil_customer_registration c
            INNER JOIN idil_transaction_booking tb ON c.id = tb.customer_id
            INNER JOIN idil_transaction_bookingline tbl 
                ON tb.id = tbl.transaction_booking_id
            INNER JOIN idil_chart_account acc 
                ON tbl.account_number = acc.id
            LEFT JOIN idil_customer_sale_order so 
                ON tb.cusotmer_sale_order_id = so.id
            inner join idil_transaction_source ts
                on tb.trx_source_id = ts.id

            WHERE 
                so.customer_id = c.id
                AND so.payment_method IN ('cash', 'bank_transfer')
                AND acc.account_type IN ('cash', 'bank_transfer')
                AND c.id = %s
                AND tbl.transaction_date BETWEEN %s AND %s

            ORDER BY line_id ASC
        """

        params = (
            self.customer_id.id,
            self.start_date,
            self.end_date,
            self.customer_id.id,
            self.start_date,
            self.end_date,
        )

        self.env.cr.execute(query, params)
        rows = self.env.cr.fetchall()

        headers = [
            "Date",
            "Method",
            "Ref No",
            "Description",
            "Debit",
            "Credit",
            "Balance",
        ]
        data = [headers]

        # Add Previous Balance as first row
        data.append(
            [
                "",
                "",
                "",
                "Previous Balance",
                f"{previous_debit:,.2f}",
                f"{previous_credit:,.2f}",
                f"{previous_balance:,.2f}",
            ]
        )

        balance = previous_balance
        total_debit = total_credit = 0.0

        for row in rows:
            (
                customer_name,
                phone,
                method,
                ref_no,
                acc_disp,
                desc,
                trans_type,
                dr,
                cr,
                line_id,
                trans_date,
            ) = row
            balance += dr - cr
            total_debit += dr
            total_credit += cr
            data.append(
                [
                    trans_date.strftime("%d/%m/%Y") if trans_date else "",
                    method or "",
                    ref_no or "",
                    desc or "",
                    f"{dr:,.2f}" if dr else "",
                    f"{cr:,.2f}" if cr else "",
                    f"{balance:,.2f}",
                ]
            )

        # Summary Row
        data.append(
            [
                "",
                "",
                "",
                "TOTAL",
                f"{total_debit:,.2f}",
                f"{total_credit:,.2f}",
                f"{balance:,.2f}",
            ]
        )

        col_widths = [60, 120, 90, 240, 90, 90, 90]

        table = Table(data, colWidths=col_widths)
        style = TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#B6862D")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                ("BACKGROUND", (0, -1), (-1, -1), colors.lightgrey),
            ]
        )
        table.setStyle(style)

        elements.append(Spacer(1, 20))
        elements.append(table)
        doc.build(elements)
        buffer.seek(0)
        pdf_data = buffer.read()

        attachment = self.env["ir.attachment"].create(
            {
                "name": f"customer_sales_report_{self.customer_id.name}.pdf",
                "type": "binary",
                "datas": base64.b64encode(pdf_data),
                "mimetype": "application/pdf",
            }
        )

        return {
            "type": "ir.actions.act_url",
            "url": f"/web/content/{attachment.id}?download=true",
            "target": "new",
        }
