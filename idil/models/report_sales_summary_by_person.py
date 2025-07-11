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


class SalesSummaryPersonReportWizard(models.TransientModel):
    _name = "idil.sales.summary.with.person"
    _description = "Sales Summary Report with Sales Person"

    start_date = fields.Date(string="Start Date", required=True)
    end_date = fields.Date(string="End Date", required=True)
    salesperson_id = fields.Many2one(
        "idil.sales.sales_personnel", string="Sales Person", required=True
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
    #             f"<b>Sales Person Name:</b> {self.salesperson_id.name}",
    #             left_align_style,
    #         ),
    #         Spacer(1, 12),
    #     ]

    #     # Calculate previous balance (before start_date)
    #     # Calculate true previous balance (total unpaid from orders before the report period)
    #     self.env.cr.execute(
    #         """
    #         SELECT
    #             SUM( ((COALESCE(sol.quantity, 0) - COALESCE(sol.discount_quantity, 0) - COALESCE(srl.returned_quantity, 0)) * COALESCE(sol.price_unit, 0))
    #              -(((COALESCE(sol.quantity, 0) - COALESCE(sol.discount_quantity, 0) - COALESCE(srl.returned_quantity, 0)) * COALESCE(sol.price_unit, 0)) * COALESCE(p.commission, 0))
    #                ) AS total_sales_minus_commission,
    #             SUM(COALESCE(src.paid_amount, 0)) AS total_paid_before
    #         FROM public.idil_sales_sales_personnel sp
    #         INNER JOIN public.idil_salesperson_place_order spo ON sp.id = spo.salesperson_id
    #         INNER JOIN idil_sale_order so ON so.sales_person_id = spo.salesperson_id AND so.salesperson_order_id = spo.id
    #         INNER JOIN idil_sale_order_line sol ON sol.order_id = so.id
    #         INNER JOIN my_product_product p ON sol.product_id = p.id
    #         LEFT JOIN public.idil_sale_return sr ON sp.id = sr.salesperson_id AND so.id = sr.sale_order_id
    #         LEFT JOIN public.idil_sale_return_line srl ON sr.id = srl.return_id AND p.id = srl.product_id
    #         LEFT JOIN idil_sales_receipt src ON so.id = src.sales_order_id AND sp.id = src.salesperson_id
    #         WHERE spo.state = 'confirmed'
    #         AND sp.id = %s
    #         AND DATE(so.order_date) < %s
    #     """,
    #         (self.salesperson_id.id, self.start_date),
    #     )
    #     result = self.env.cr.fetchone()
    #     previous_net = result[0] or 0.0
    #     previous_paid = result[1] or 0.0
    #     previous_balance = previous_net - previous_paid

    #     # Main data query
    #     self.env.cr.execute(
    #         """
    #         SELECT DATE(so.order_date),
    #                 p.name,
    #                 sol.quantity,
    #                 (((COALESCE(sol.quantity, 0)) - (COALESCE(srl.returned_quantity, 0)) )* (p.discount /100))

    #              ,
    #                COALESCE(srl.returned_quantity, 0),
    #                (COALESCE(sol.quantity, 0) - (((COALESCE(sol.quantity, 0)) - (COALESCE(srl.returned_quantity, 0)) )* (p.discount /100))  - COALESCE(srl.returned_quantity, 0)) AS net,
    #                COALESCE(sol.price_unit, 0),
    #                ((COALESCE(sol.quantity, 0) - (((COALESCE(sol.quantity, 0)) - (COALESCE(srl.returned_quantity, 0)) )* (p.discount /100)) - COALESCE(srl.returned_quantity, 0)) * COALESCE(sol.price_unit, 0)) AS lacag,
    #                (COALESCE(p.commission, 0) * 100),
    #                (((COALESCE(sol.quantity, 0) - (((COALESCE(sol.quantity, 0)) - (COALESCE(srl.returned_quantity, 0)) )* (p.discount /100)) - COALESCE(srl.returned_quantity, 0)) * COALESCE(sol.price_unit, 0)) * COALESCE(p.commission, 0)),
    #                DATE(src.receipt_date),
    #                COALESCE(src.paid_amount, 0)
    #         FROM public.idil_sales_sales_personnel sp
    #         INNER JOIN public.idil_salesperson_place_order spo ON sp.id = spo.salesperson_id
    #         INNER JOIN idil_sale_order so ON so.sales_person_id = spo.salesperson_id and so.salesperson_order_id= spo.id
    #         INNER JOIN idil_sale_order_line sol ON sol.order_id = so.id
    #         INNER JOIN my_product_product p ON sol.product_id = p.id
    #         LEFT JOIN public.idil_sale_return sr ON sp.id = sr.salesperson_id AND so.id = sr.sale_order_id
    #         LEFT JOIN public.idil_sale_return_line srl ON sr.id = srl.return_id AND p.id = srl.product_id
    #         LEFT JOIN idil_sales_receipt src ON so.id = src.sales_order_id AND sp.id = src.salesperson_id
    #         WHERE spo.state = 'confirmed'
    #           AND sp.id = %s
    #           AND DATE(so.order_date) BETWEEN %s AND %s
    #         ORDER BY DATE(so.order_date), p.name;
    #     """,
    #         (self.salesperson_id.id, self.start_date, self.end_date),
    #     )
    #     rows = self.env.cr.fetchall()

    #     grouped = defaultdict(list)
    #     paid_by_day = defaultdict(float)
    #     for row in rows:
    #         grouped[row[0]].append(row)
    #         if row[10] == row[0]:
    #             paid_by_day[row[0]] += row[11]

    #     headers = [
    #         "Date",
    #         "Product",
    #         "Cadad",
    #         "Celis Tos",
    #         "Celis",
    #         "Net",
    #         "Qiime",
    #         "Lacag",
    #         "Per %",
    #         "Commission",
    #         "Balance",
    #     ]
    #     data = [headers]
    #     total_lacag = total_commission = total_paid = total_balance = 0.0
    #     highlight_rows = []
    #     merged_rows = []

    #     for day in sorted(grouped.keys()):
    #         daily = grouped[day]
    #         subtotal_lacag = subtotal_commission = subtotal_balance = 0.0

    #         for row in daily:
    #             product, cadad, celis_tos, celis, net, qiime, lacag, per, comm, _, _ = (
    #                 row[1:]
    #             )
    #             balance = lacag - comm
    #             data.append(
    #                 [
    #                     day.strftime("%d/%m/%Y"),
    #                     product,
    #                     f"{cadad:.2f}",
    #                     f"{celis_tos:.2f}",
    #                     f"{celis:.2f}",
    #                     f"{net:.2f}",
    #                     f"{qiime:,.2f}",
    #                     f"{lacag:,.2f}",
    #                     f"{per:.2f}%",
    #                     f"{comm:,.2f}",
    #                     f"{balance:,.2f}",
    #                 ]
    #             )

    #             subtotal_lacag += lacag
    #             subtotal_commission += comm
    #             subtotal_balance += balance

    #         paid_today = paid_by_day.get(day, 0.0)

    #         for label, value in [
    #             (f"Subtotal {day.strftime('%d/%m/%Y')}", f"{subtotal_balance:,.2f}"),
    #             (f"Paid {day.strftime('%d/%m/%Y')}", f"{paid_today:,.2f}"),
    #             (
    #                 f"Day Total {day.strftime('%d/%m/%Y')}",
    #                 f"{(subtotal_balance - paid_today):,.2f}",
    #             ),
    #         ]:
    #             row = [""] * 11
    #             row[0] = label
    #             row[-1] = value
    #             data.append(row)
    #             idx = len(data) - 1
    #             highlight_rows.append(idx)
    #             merged_rows.append(idx)

    #         total_lacag += subtotal_lacag
    #         total_commission += subtotal_commission
    #         total_paid += paid_today
    #         total_balance += subtotal_balance - paid_today

    #     data.append(
    #         [
    #             "GRAND TOTAL",
    #             "",
    #             "",
    #             "",
    #             "",
    #             "",
    #             "",
    #             f"{total_lacag:,.2f}",
    #             "",
    #             f"{total_commission:,.2f}",
    #             f"{(total_balance):,.2f}",
    #         ]
    #     )
    #     highlight_rows.append(len(data) - 1)
    #     merged_rows.append(len(data) - 1)

    #     data.append(["", "", "", "", "", "", "", "", "", "", "--------------------"])
    #     highlight_rows.append(len(data) - 1)
    #     merged_rows.append(len(data) - 1)

    #     data.append(
    #         [
    #             "PREVIOUS BALANCE",
    #             "",
    #             "",
    #             "",
    #             "",
    #             "",
    #             "",
    #             "",
    #             "",
    #             "",
    #             f"{previous_balance:,.2f}",
    #         ]
    #     )
    #     highlight_rows.append(len(data) - 1)
    #     merged_rows.append(len(data) - 1)

    #     data.append(
    #         ["TOTAL PAID:", "", "", "", "", "", "", "", "", "", f"{total_paid:,.2f}"]
    #     )
    #     highlight_rows.append(len(data) - 1)
    #     merged_rows.append(len(data) - 1)

    #     final_balance_amount = previous_balance + total_balance
    #     data.append(
    #         [
    #             "FINAL BALANCE",
    #             "",
    #             "",
    #             "",
    #             "",
    #             "",
    #             "",
    #             "",
    #             "",
    #             "",
    #             f"{final_balance_amount:,.2f}",
    #         ]
    #     )
    #     final_balance_index = len(data) - 1
    #     highlight_rows.append(final_balance_index)
    #     merged_rows.append(final_balance_index)

    #     table = Table(data, colWidths=[70, 140, 50, 60, 50, 40, 60, 80, 50, 70, 100])
    #     style = TableStyle(
    #         [
    #             ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#B6862D")),
    #             ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
    #             ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
    #             ("ALIGN", (0, 0), (-1, -1), "CENTER"),
    #             ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
    #         ]
    #     )

    #     for row_idx in highlight_rows:
    #         style.add("FONTNAME", (0, row_idx), (-1, row_idx), "Helvetica-Bold")
    #     for row_idx in merged_rows:
    #         style.add("SPAN", (0, row_idx), (2, row_idx))

    #     style.add(
    #         "BACKGROUND",
    #         (0, final_balance_index),
    #         (-1, final_balance_index),
    #         colors.HexColor("#FFD700"),
    #     )
    #     table.setStyle(style)

    #     elements.append(Spacer(1, 20))
    #     elements.append(table)
    #     doc.build(elements)
    #     buffer.seek(0)
    #     pdf_data = buffer.read()

    #     attachment = self.env["ir.attachment"].create(
    #         {
    #             "name": "sales_summary.pdf",
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
                f"<b>Sales Person Name:</b> {self.salesperson_id.name}",
                left_align_style,
            ),
            Spacer(1, 12),
        ]

        # Opening balance from receipt where opening balance exists
        self.env.cr.execute(
            """
            SELECT remaining_amount
            FROM idil_sales_receipt
            WHERE salesperson_id = %s
            AND sales_opening_balance_id IS NOT NULL
            ORDER BY id ASC
            LIMIT 1
            """,
            (self.salesperson_id.id,),
        )
        opening_balance_result = self.env.cr.fetchone()
        opening_balance = opening_balance_result[0] if opening_balance_result else 0.0

        # Previous balance before start_date
        self.env.cr.execute(
            """
            SELECT
                SUM( ((COALESCE(sol.quantity, 0) - COALESCE(sol.discount_quantity, 0) - COALESCE(srl.returned_quantity, 0)) * COALESCE(sol.price_unit, 0))
                -(((COALESCE(sol.quantity, 0) - COALESCE(sol.discount_quantity, 0) - COALESCE(srl.returned_quantity, 0)) * COALESCE(sol.price_unit, 0)) * COALESCE(p.commission, 0))
                ) AS total_sales_minus_commission,
                SUM(COALESCE(src.paid_amount, 0)) AS total_paid_before
            FROM public.idil_sales_sales_personnel sp
            INNER JOIN public.idil_salesperson_place_order spo ON sp.id = spo.salesperson_id
            INNER JOIN idil_sale_order so ON so.sales_person_id = spo.salesperson_id AND so.salesperson_order_id = spo.id
            INNER JOIN idil_sale_order_line sol ON sol.order_id = so.id
            INNER JOIN my_product_product p ON sol.product_id = p.id
            LEFT JOIN public.idil_sale_return sr ON sp.id = sr.salesperson_id AND so.id = sr.sale_order_id
            LEFT JOIN public.idil_sale_return_line srl ON sr.id = srl.return_id AND p.id = srl.product_id
            LEFT JOIN idil_sales_receipt src ON so.id = src.sales_order_id AND sp.id = src.salesperson_id
            WHERE spo.state = 'confirmed'
            AND sp.id = %s
            AND DATE(so.order_date) < %s
        """,
            (self.salesperson_id.id, self.start_date),
        )
        result = self.env.cr.fetchone()
        previous_net = result[0] or 0.0
        previous_paid = result[1] or 0.0
        previous_balance = previous_net - previous_paid

        # Main data query
        self.env.cr.execute(
            """
            SELECT DATE(so.order_date), 
                    p.name, 
                    sol.quantity, 
                    (((COALESCE(sol.quantity, 0)) - (COALESCE(srl.returned_quantity, 0)) )* (p.discount /100)) 
                , 
                COALESCE(srl.returned_quantity, 0),
                (COALESCE(sol.quantity, 0) - (((COALESCE(sol.quantity, 0)) - (COALESCE(srl.returned_quantity, 0)) )* (p.discount /100))  - COALESCE(srl.returned_quantity, 0)) AS net,
                COALESCE(sol.price_unit, 0), 
                ((COALESCE(sol.quantity, 0) - (((COALESCE(sol.quantity, 0)) - (COALESCE(srl.returned_quantity, 0)) )* (p.discount /100)) - COALESCE(srl.returned_quantity, 0)) * COALESCE(sol.price_unit, 0)) AS lacag,
                (COALESCE(p.commission, 0) * 100),
                (((COALESCE(sol.quantity, 0) - (((COALESCE(sol.quantity, 0)) - (COALESCE(srl.returned_quantity, 0)) )* (p.discount /100)) - COALESCE(srl.returned_quantity, 0)) * COALESCE(sol.price_unit, 0)) * COALESCE(p.commission, 0)),
                DATE(src.receipt_date), 
                COALESCE(src.paid_amount, 0)
            FROM public.idil_sales_sales_personnel sp
            INNER JOIN public.idil_salesperson_place_order spo ON sp.id = spo.salesperson_id
            INNER JOIN idil_sale_order so ON so.sales_person_id = spo.salesperson_id and so.salesperson_order_id= spo.id
            INNER JOIN idil_sale_order_line sol ON sol.order_id = so.id 
            INNER JOIN my_product_product p ON sol.product_id = p.id
            LEFT JOIN public.idil_sale_return sr ON sp.id = sr.salesperson_id AND so.id = sr.sale_order_id 
            LEFT JOIN public.idil_sale_return_line srl ON sr.id = srl.return_id AND p.id = srl.product_id
            LEFT JOIN idil_sales_receipt src ON so.id = src.sales_order_id AND sp.id = src.salesperson_id
            WHERE spo.state = 'confirmed'
            AND sp.id = %s
            AND DATE(so.order_date) BETWEEN %s AND %s
            ORDER BY DATE(so.order_date), p.name;
        """,
            (self.salesperson_id.id, self.start_date, self.end_date),
        )
        rows = self.env.cr.fetchall()

        from collections import defaultdict

        grouped = defaultdict(list)
        paid_by_day = defaultdict(float)
        for row in rows:
            grouped[row[0]].append(row)
            if row[10] == row[0]:
                paid_by_day[row[0]] += row[11]

        headers = [
            "Date",
            "Product",
            "Cadad",
            "Celis Tos",
            "Celis",
            "Net",
            "Qiime",
            "Lacag",
            "Per %",
            "Commission",
            "Balance",
        ]
        data = [headers]
        total_lacag = total_commission = total_paid = total_balance = 0.0
        highlight_rows = []
        merged_rows = []

        for day in sorted(grouped.keys()):
            daily = grouped[day]
            subtotal_lacag = subtotal_commission = subtotal_balance = 0.0

            for row in daily:
                product, cadad, celis_tos, celis, net, qiime, lacag, per, comm, _, _ = (
                    row[1:]
                )
                balance = lacag - comm
                data.append(
                    [
                        day.strftime("%d/%m/%Y"),
                        product,
                        f"{cadad:.2f}",
                        f"{celis_tos:.2f}",
                        f"{celis:.2f}",
                        f"{net:.2f}",
                        f"{qiime:,.2f}",
                        f"{lacag:,.2f}",
                        f"{per:.2f}%",
                        f"{comm:,.2f}",
                        f"{balance:,.2f}",
                    ]
                )
                subtotal_lacag += lacag
                subtotal_commission += comm
                subtotal_balance += balance

            paid_today = paid_by_day.get(day, 0.0)
            for label, value in [
                (f"Subtotal {day.strftime('%d/%m/%Y')}", f"{subtotal_balance:,.2f}"),
                (f"Paid {day.strftime('%d/%m/%Y')}", f"{paid_today:,.2f}"),
                (
                    f"Day Total {day.strftime('%d/%m/%Y')}",
                    f"{(subtotal_balance - paid_today):,.2f}",
                ),
            ]:
                row = [""] * 11
                row[0] = label
                row[-1] = value
                data.append(row)
                idx = len(data) - 1
                highlight_rows.append(idx)
                merged_rows.append(idx)

            total_lacag += subtotal_lacag
            total_commission += subtotal_commission
            total_paid += paid_today
            total_balance += subtotal_balance - paid_today

        data.append(
            [
                "GRAND TOTAL",
                "",
                "",
                "",
                "",
                "",
                "",
                f"{total_lacag:,.2f}",
                "",
                f"{total_commission:,.2f}",
                f"{total_balance:,.2f}",
            ]
        )
        highlight_rows.append(len(data) - 1)
        merged_rows.append(len(data) - 1)

        data.append(["", "", "", "", "", "", "", "", "", "", "--------------------"])
        highlight_rows.append(len(data) - 1)
        merged_rows.append(len(data) - 1)

        # ✅ Add Opening Balance Row
        data.append(
            [
                "OPENING BALANCE",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                f"{opening_balance:,.2f}",
            ]
        )
        highlight_rows.append(len(data) - 1)
        merged_rows.append(len(data) - 1)

        data.append(
            [
                "PREVIOUS BALANCE",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                f"{previous_balance:,.2f}",
            ]
        )
        highlight_rows.append(len(data) - 1)
        merged_rows.append(len(data) - 1)

        data.append(
            ["TOTAL PAID:", "", "", "", "", "", "", "", "", "", f"{total_paid:,.2f}"]
        )
        highlight_rows.append(len(data) - 1)
        merged_rows.append(len(data) - 1)

        final_balance_amount = opening_balance + previous_balance + total_balance
        data.append(
            [
                "FINAL BALANCE",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                f"{final_balance_amount:,.2f}",
            ]
        )
        final_balance_index = len(data) - 1
        highlight_rows.append(final_balance_index)
        merged_rows.append(final_balance_index)

        table = Table(data, colWidths=[70, 140, 50, 60, 50, 40, 60, 80, 50, 70, 100])
        style = TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#B6862D")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ]
        )
        for row_idx in highlight_rows:
            style.add("FONTNAME", (0, row_idx), (-1, row_idx), "Helvetica-Bold")
        for row_idx in merged_rows:
            style.add("SPAN", (0, row_idx), (2, row_idx))
        style.add(
            "BACKGROUND",
            (0, final_balance_index),
            (-1, final_balance_index),
            colors.HexColor("#FFD700"),
        )
        table.setStyle(style)

        elements.append(Spacer(1, 20))
        elements.append(table)
        doc.build(elements)
        buffer.seek(0)
        pdf_data = buffer.read()

        attachment = self.env["ir.attachment"].create(
            {
                "name": "sales_summary.pdf",
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
