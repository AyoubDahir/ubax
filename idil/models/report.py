from odoo import models


class JournalEntryReport(models.AbstractModel):
    _name = 'report.idil_module.journal_entry_report_template'

    def _get_report_values(self, docids, data=None):
        docs = self.env['idil.transaction_booking'].browse(docids)
        return {
            'doc_ids': docids,
            'doc_model': 'idil.transaction_booking',
            'docs': docs,
            'data': data,
        }
