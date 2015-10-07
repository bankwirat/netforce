# Copyright (c) 2012-2015 Netforce Co. Ltd.
# 
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
# 
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
# 
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
# IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM,
# DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR
# OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE
# OR OTHER DEALINGS IN THE SOFTWARE.

from netforce.model import Model, fields, get_model
from datetime import *
from dateutil.relativedelta import *
from netforce.access import get_active_company
from netforce.database import get_connection
from pprint import pprint


def get_totals(date_from, date_to, product_id=None, show_pending=False, lot_id=None, categ_id=None):
    db = get_connection()
    q = "SELECT " \
        " m.product_id,m.location_from_id,m.location_to_id,m.uom_id, " \
        " SUM(m.qty) AS total_qty,SUM(m.unit_price*m.qty) AS total_amt,SUM(m.qty2) AS total_qty2 " \
        " FROM stock_move m " \
        " JOIN product p ON m.product_id=p.id WHERE true"
    q_args = []
    if date_from:
        q += " AND m.date>=%s"
        q_args.append(date_from + " 00:00:00")
    if date_to:
        q += " AND m.date<=%s"
        q_args.append(date_to + " 23:59:59")
    if product_id:
        q += " AND m.product_id=%s"
        q_args.append(product_id)
    if categ_id:
        q += " AND p.categ_id=%s"
        q_args.append(categ_id)
    if lot_id:
        q += " AND m.lot_id=%s"
        q_args.append(lot_id)
    if show_pending:
        q += " AND m.state IN ('pending','done')"
    else:
        q += " AND m.state='done'"
    q += " GROUP BY m.product_id,m.location_from_id,m.location_to_id,m.uom_id"
    res = db.query(q, *q_args)
    totals = {}
    for r in res:
        prod = get_model("product").browse(r.product_id)
        uom = get_model("uom").browse(r.uom_id)
        qty = r.total_qty * uom.ratio / prod.uom_id.ratio
        amt = r.total_amt or 0
        qty2 = r.total_qty2 or 0
        k = (r.product_id, r.location_from_id, r.location_to_id)
        tot = totals.setdefault(k, [0, 0, 0])
        tot[0] += qty
        tot[1] += amt
        tot[2] += qty2
    return totals


class ReportStockCard(Model):
    _name = "report.stock.card"
    _transient = True
    _fields = {
        "date_from": fields.Date("From", required=True),
        "date_to": fields.Date("To", required=True),
        "product_id": fields.Many2One("product", "Product", on_delete="cascade"),
        "categ_id": fields.Many2One("product.categ", "Product Category", on_delete="cascade"),
        "location_id": fields.Many2One("stock.location", "Location", on_delete="cascade"),
        "uom_id": fields.Many2One("uom", "UoM"),
        "lot_id": fields.Many2One("stock.lot", "Lot / Serial Number"),
        "invoice_id": fields.Many2One("account.invoice", "Invoice"),
        "show_pending": fields.Boolean("Show Pending"),
        "show_qty2": fields.Boolean("Show Secondary Qty"),
        "hide_zero": fields.Boolean("Hide Zero Lines"),
    }
    _defaults = {
        "date_from": lambda *a: date.today().strftime("%Y-%m-01"),
        "date_to": lambda *a: (date.today() + relativedelta(day=31)).strftime("%Y-%m-%d"),
    }

    # TODO: add uom_id support again
    def get_report_data(self, ids, context={}):
        company_id = get_active_company()
        comp = get_model("company").browse(company_id)
        if ids:
            params = self.read(ids, load_m2o=False)[0]
        else:
            params = self.default_get(load_m2o=False, context=context)
        settings = get_model("settings").browse(1)
        company_address = settings.default_address_id.address_text

        location_id = params.get("location_id")
        product_id = params.get("product_id")
        categ_id = params.get("categ_id")
        uom_id = params.get("uom_id")
        date_from = params.get("date_from")
        if not date_from:
            return
        date_to = params.get("date_to")
        if not date_to:
            return
        lot_id = params.get("lot_id")
        invoice_id = params.get("invoice_id")
        show_pending = params.get("show_pending")
        hide_zero = params.get("hide_zero")

        date_from_prev = (datetime.strptime(date_from, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
        open_totals = get_totals(
            None, date_from_prev, product_id=product_id, show_pending=show_pending, lot_id=lot_id, categ_id=categ_id)

        def _get_open_balance(prod_id, loc_id):
            bal_qty = 0
            bal_amt = 0
            bal_qty2 = 0
            for (prod_id_, loc_from_id, loc_to_id), (qty, amt, qty2) in open_totals.items():
                if prod_id_ != prod_id:
                    continue
                if loc_to_id == loc_id and loc_from_id != loc_id:
                    bal_qty += qty
                    bal_amt += amt
                    bal_qty2 += qty2
                elif loc_from_id == loc_id and loc_to_id != loc_id:
                    bal_qty -= qty
                    bal_amt -= amt
                    bal_qty2 -= qty2
            return bal_qty, bal_amt, bal_qty2

        prod_locs = {}

        db = get_connection()
        q = "SELECT m.id,m.date,m.ref,m.related_id,m.lot_id,l.number AS lot_num,m.invoice_id,i.number AS invoice_num,m.product_id,m.location_from_id,m.location_to_id,m.qty,m.uom_id,m.unit_price,m.qty2 FROM stock_move m LEFT JOIN stock_lot l ON l.id=m.lot_id LEFT JOIN account_invoice i ON i.id=m.invoice_id LEFT JOIN product p on m.product_id=p.id WHERE m.date>=%s AND m.date<=%s"
        args = [date_from + " 00:00:00", date_to + " 23:59:59"]
        if product_id:
            q += " AND m.product_id=%s"
            args.append(product_id)
        if categ_id:
            q += " AND p.categ_id=%s"
            args.append(categ_id)
        if location_id:
            q += " AND (m.location_from_id=%s OR m.location_to_id=%s)"
            args += [location_id, location_id]
        if lot_id:
            q += " AND m.lot_id=%s"
            args.append(lot_id)
        if invoice_id:
            q += " AND m.invoice_id=%s"
            args.append(invoice_id)
        if show_pending:
            q += " AND m.state IN ('pending','done')"
        else:
            q += " AND m.state='done'"
        q += " ORDER BY m.date"
        res = db.query(q, *args)
        for r in res:
            prod_locs.setdefault((r.product_id, r.location_from_id), []).append(r)
            prod_locs.setdefault((r.product_id, r.location_to_id), []).append(r)

        loc_ids=[]
        for prod_id, loc_id in prod_locs:
            loc_ids.append(loc_id)
        perm_loc_ids=get_model("stock.location").search([["id","in",loc_ids]])

        groups = []
        for (prod_id, loc_id), moves in prod_locs.items():
            if loc_id not in perm_loc_ids:
                continue
            prod = get_model("product").browse(prod_id)
            loc = get_model("stock.location").browse(loc_id)
            if location_id:
                if loc.id != location_id:
                    continue
            else:
                if loc.type != "internal":
                    continue
            bal_qty, bal_amt, bal_qty2 = _get_open_balance(prod_id, loc_id)
            #import pdb; pdb.set_trace()
            bal_price = bal_qty and bal_amt / bal_qty or 0
            lines = []
            line = {
                "date": date_from,
                "bal_qty": bal_qty,
                "bal_cost_amount": bal_amt,
                "bal_cost_price": bal_price,
                "bal_qty2": bal_qty2,
            }
            lines.append(line)
            for r in moves:
                ref = r.ref
                if r.related_id:  # XXX: remove this!
                    related_attr = r.related_id.split(",")
                    if r.related_id and len(related_attr) == 2:
                        try:
                            obj_id = get_model(related_attr[0]).search([["id", "=", int(related_attr[1])]])
                            if obj_id:
                                related = get_model(related_attr[0]).browse(int(related_attr[1]))
                                if related:
                                    ref = related.number
                        except Exception as e:
                            import traceback
                            traceback.print_exc()
                            get_model("log").log("Invalid model", str(e))
                uom = get_model("uom").browse(r.uom_id)
                qty = r.qty * uom.ratio / prod.uom_id.ratio
                price = (r.unit_price or 0) / (uom.ratio / prod.uom_id.ratio)
                amt = (r.unit_price or 0) * r.qty
                qty2 = r.qty2 or 0
                if hide_zero and not qty and not qty2:
                    continue
                line = {
                    "id": r.id,
                    "date": r.date,
                    "ref": ref,
                    "lot_id": r.lot_id,
                    "lot_num": r.lot_num,
                    "invoice_id": r.invoice_id,
                    "invoice_num": r.invoice_num,
                }
                if r.location_to_id == loc_id and r.location_from_id == loc_id:
                    continue
                elif r.location_to_id == loc_id:
                    bal_qty += qty
                    bal_amt += amt
                    bal_qty2 += qty2
                    line.update({
                        "in_qty": qty,
                        "in_unit_price": price,
                        "in_amount": amt,
                        "in_qty2": qty2,
                    })
                elif r.location_from_id == loc_id:
                    bal_qty -= qty
                    bal_amt -= amt
                    bal_qty2 -= qty2
                    line.update({
                        "out_qty": qty,
                        "out_unit_price": price,
                        "out_amount": amt,
                        "out_qty2": qty2,
                    })
                bal_price = bal_qty and bal_amt / bal_qty or 0
                line.update({
                    "bal_qty": bal_qty,
                    "bal_cost_amount": bal_amt,
                    "bal_cost_price": bal_price,
                    "bal_qty2": bal_qty2,
                })
                res=db.query("select l.id as landed_id,l.date as landed_date,l.number as landed_num,coalesce(a.est_ship,0)+coalesce(a.est_duty,0)+coalesce(a.act_ship,0)+coalesce(a.act_duty,0) as amount from landed_cost_alloc a join landed_cost l on l.id=a.landed_id where a.move_id=%s and l.state='posted'",r.id) # FIXME: too slow!
                line["landed_costs"]=[]
                if res:
                    amt=0
                    for r in res:
                        vals={
                            "landed_id": r.landed_id,
                            "ref": r.landed_num,
                            "date": r.landed_date,
                            "amount": r.amount,
                        }
                        amt+=r.amount
                        line["landed_costs"].append(vals)
                    line["in_amount"]-=amt
                    line["in_unit_price"]=line["in_amount"]/line["in_qty"] if line["in_qty"] else 0
                lines.append(line)
            group = {
                "product_id": prod.id,
                "location_id": loc.id,
                "product_name": prod.name_get()[0][1],
                "location_name": loc.name_get()[0][1],
                "lines": lines,
                "total_in_qty": sum(l.get("in_qty", 0) for l in lines),
                "total_in_amount": sum(l.get("in_amount", 0) for l in lines),
                "total_in_qty2": sum(l.get("in_qty2", 0) for l in lines),
                "total_out_qty": sum(l.get("out_qty", 0) for l in lines),
                "total_out_amount": sum(l.get("out_amount", 0) for l in lines),
                "total_out_qty2": sum(l.get("out_qty2", 0) for l in lines),
            }
            groups.append(group)

        groups.sort(key=lambda x: (x["product_name"], x["location_name"]))
        data = {
            "company_name": comp.name,
            "tax_no": settings.tax_no,
            "address": company_address,
            "date_from": date_from,
            "date_to": date_to,
            "groups": groups,
            "show_qty2": params.get("show_qty2"),
        }
        pprint(data)
        return data

ReportStockCard.register()
