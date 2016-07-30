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

from netforce import module
import requests
import json
import os
import pkg_resources
import tempfile
import subprocess
import time
from io import BytesIO
import zipfile
from lxml import etree
from pprint import pprint
from netforce import template
import os.path
from netforce import database
from netforce import utils
import re
import netforce
from netforce import config
from netforce.model import get_model
import base64

try:
    from PyPDF2 import PdfFileReader, PdfFileWriter
except:
    print("WARNING: PyPDF2 not found")
try:
    from PIL import Image
except:
    print("WARNING: failed to import PIL")
from copy import deepcopy
import shutil


def _extract_reports():
    print("_extract_reports")
    global _report_dir
    print("_report_dir", _report_dir)
    loaded_modules = module.get_loaded_modules()
    for m in loaded_modules:
        if pkg_resources.resource_exists(m, "reports"):
            for fname in pkg_resources.resource_listdir(m, "reports"):
                print("%s %s" % (m, fname))
                data = pkg_resources.resource_string(m, "reports/" + fname)
                path = os.path.join(_report_dir, fname)
                f = open(path, "wb")
                f.write(data)
                f.close()


def _extract_report_file(fname, report_dir):
    print("_extract_report_file", fname)
    found = False
    loaded_modules = module.get_loaded_modules()
    for m in reversed(loaded_modules):
        if pkg_resources.resource_exists(m, "reports/" + fname):
            found = True
            data = pkg_resources.resource_string(m, "reports/" + fname)
            path = os.path.join(report_dir, fname)
            f = open(path, "wb")
            f.write(data)
            f.close()
            break
    if not found:
        raise Exception("Report file not found: %s" % fname)


def _get_report_path(name, params={}):
    fname = name + ".jrxml"
    report_dir = tempfile.mkdtemp()
    print("report_dir", report_dir)
    try:
        _extract_report_file(fname, report_dir)
    except:
        res = get_model("report.template").search([["name", "=", name]])
        if not res:
            raise Exception("Report template not found: %s" % name)
        tmpl_id = res[0]
        tmpl = get_model("report.template").browse(tmpl_id)
        in_path = utils.get_file_path(tmpl.file)
        data = open(in_path, "rb").read()
        out_path = os.path.join(report_dir, fname)
        f = open(out_path, "wb")
        f.write(data)
        f.close()
    report_path = os.path.join(report_dir, fname)
    tree = etree.parse(report_path)
    for el in tree.iterfind(".//ns:imageExpression", {"ns": "http://jasperreports.sourceforge.net/jasperreports"}):
        expr = el.text
        m = re.match("^\"(.*)\"$", expr)
        if m:
            img_fname = m.group(1)
            img_path = utils.get_file_path(img_fname)
            if os.path.exists(img_path):
                img_path2 = os.path.join(report_dir, img_fname)
                shutil.copyfile(img_path, img_path2)
            else:
                _extract_report_file(img_fname, report_dir)
            el.text = '"' + os.path.join(report_dir, img_fname) + '"'
        p = re.match("^\{(.*)\}$", expr)
        if p: #for detect {settings.logo}
            img_fname_obj = p.group(1)
            char_replace = ["{","}"]
            for c in char_replace:
                img_fname_obj.replace(c,"")
            img_fname_objs = img_fname_obj.split(".")
            obj = []
            if isinstance(params,list):
                obj = params[0] #FIXME
            else:
                obj = params
            for k in img_fname_objs:
                obj = obj[k]
            if obj:
                img_fname = obj
                img_path = utils.get_file_path(img_fname)
                if os.path.exists(img_path):
                    img_path2 = os.path.join(report_dir, img_fname)
                    shutil.copyfile(img_path, img_path2)
                else:
                    _extract_report_file(img_fname, report_dir)
                el.text = '"' + os.path.join(report_dir, img_fname) + '"'
            else:
                el.text = ''
    report_xml = etree.tostring(tree, pretty_print=True).decode()
    f = open(report_path, "w")
    f.write(report_xml)
    f.close()
    return report_path


def data_get_path(data, path):
    field, _, path2 = path.partition(".")
    val = data.get(field)
    if not path2:
        return val
    if not val:
        return None
    return data_get_path(val, path2)


def data_set_path(data, path, val):
    field, _, path2 = path.partition(".")
    if not path2:
        data[field] = val
        return
    parent = data.setdefault(field, {})
    data_set_path(parent, path2, val)


def conv_jasper_data(data, report_path):  # XXX: improve this
    print("conv_jasper_data")
    print("ORIG_DATA:")
    pprint(data)
    jrxml = open(report_path).read()
    tree = etree.fromstring(jrxml)
    ns = "http://jasperreports.sourceforge.net/jasperreports"
    el = tree.findall(".//ns:queryString", namespaces={"ns": ns})
    if not el:
        raise Exception("Query string not found")
    query = el[0].text.strip()
    print("QUERY", query)
    fields = []
    for el in tree.findall(".//ns:field", namespaces={"ns": ns}):
        name = el.attrib["name"]
        fields.append(name)
    print("FIELDS", fields)
    out_data = {}
    for f in fields:
        val = data_get_path(data, f)
        data_set_path(out_data, f, val)
    items = data_get_path(data, query) or []
    out_items = []
    for item in items:
        out_item = {}
        for f in fields:
            val = data_get_path(item, f)
            if val is None:
                val = data_get_path(data, f)
            data_set_path(out_item, f, val)
        out_items.append(out_item)
    data_set_path(out_data, query, out_items)
    print("CONV_DATA:")
    pprint(out_data)
    return out_data


def get_report_jasper(report, data, params={}, format="pdf"):
    report_path = _get_report_path(report,data)
    data2 = conv_jasper_data(data, report_path)
    params = {
        "report": report_path,
        "format": format,
        "data": utils.json_dumps(data2),
    }
    url = "http://localhost:9990/"
    r = requests.post(url, data=params)
    report_dir = os.path.dirname(report_path)
    shutil.rmtree(report_dir)
    if r.status_code != 200:
        raise Exception("Failed to download report (%s)" % r.status_code)
    return r.content


def get_report_jasper_multi_page(report, datas, params={}, format="pdf"):
    print("get_report_jasper_multi_page")
    report_path = _get_report_path(report,datas)
    datas2 = [conv_jasper_data(data, report_path) for data in datas]
    params = {
        "report": report_path,
        "format": format,
        "multi_page": True,
    }
    for i, data in enumerate(datas2):
        params["data_%d" % i] = utils.json_dumps(data)
    print("params", params)
    url = "http://localhost:9990/"
    r = requests.post(url, data=params)
    report_dir = os.path.dirname(report_path)
    shutil.rmtree(report_dir)
    if r.status_code != 200:
        raise Exception("Failed to download report (%s)" % r.status_code)
    return r.content

report_css_path = None


def _get_report_css_path():
    global report_css_path
    if report_css_path:
        return report_css_path
    f = "static/css/report/netforce_ui.css"  # XXX
    data = pkg_resources.resource_string("netforce_ui", f)
    f = tempfile.NamedTemporaryFile(delete=False, suffix=".css")
    f.write(data)
    f.close()
    report_css_path = f.name
    return report_css_path


def html2pdf(html):
    try:
        css_path = _get_report_css_path()
        fd_in, path_in = tempfile.mkstemp(suffix=".html")
        fd_out, path_out = tempfile.mkstemp(suffix=".pdf")
        f_in = os.fdopen(fd_in, "w")
        f_out = os.fdopen(fd_out, "rb")
        f_in.write(html)
        f_in.flush()
        cmd = "wkhtmltopdf --user-style-sheet %s %s %s" % (css_path, path_in, path_out)
        print("CMD", cmd)
        res = os.system(cmd)
        data = f_out.read()
        if not data:
            raise Exception("Failed to generate report")
        return data
    finally:
        f_in.close()
        f_out.close()


def get_cell_col(addr):
    m = re.match("(\D+)(\d+)", addr)
    if not m:
        return None
    return m.group(1).upper()


def get_cell_row(addr):
    m = re.match("(\D+)(\d+)", addr)
    if not m:
        return None
    return m.group(2)


def report_render_xls(tmpl_name, data, fast_render=False):
    #print("report_render_xls", tmpl_name, data)
    tmpl_data = _get_report_template(tmpl_name, "xlsx")
    tmpl_f = BytesIO(tmpl_data)
    zf_in = zipfile.ZipFile(tmpl_f)
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    nsd = "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing"
    nsr = "http://schemas.openxmlformats.org/package/2006/relationships"
    nsd2 = "http://schemas.openxmlformats.org/drawingml/2006/main"
    nsr2 = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

    f = zf_in.open("xl/sharedStrings.xml")
    tree = etree.parse(f)
    f.close()
    strings = []
    for el in tree.findall(".//ns:si", namespaces={"ns": ns}):
        strings.append(el[0].text)
    #print("strings", strings)

    f = zf_in.open("xl/styles.xml")
    style_tree = etree.parse(f)
    f.close()
    el_fmt = etree.Element("numFmt")
    el_fmt.attrib["formatCode"] = "#,##0.00_);\(#,##0.00\)"
    el_fmt.attrib["numFmtId"] = "999"  # XXX
    el_p = style_tree.find(".//ns:numFmts", namespaces={"ns": ns})
    if el_p:
        el_p.append(el_fmt)
        el_p.attrib["count"] = str(int(el_p.attrib["count"]) + 1)
    else:
        el_p = etree.Element("numFmts")
        style_tree.getroot().insert(0, el_p)
        el_p.append(el_fmt)
        el_p.attrib["count"] = "1"

    currency_styles = {}

    def _make_currency_style(old_style):
        if old_style in currency_styles:
            return currency_styles[old_style]
        el_p = style_tree.find(".//ns:cellXfs", namespaces={"ns": ns})
        if el_p is None:
            raise Exception("Cell styles not found!")
        if old_style:
            el_old = el_p[old_style]
            el_new = deepcopy(el_old)
        else:
            el_new = etree.Element("xf")
            el_new.attrib["xfId"] = "0"  # XXX
        el_new.attrib["numFmtId"] = "999"
        el_p.append(el_new)
        new_style = int(el_p.attrib["count"])
        currency_styles[old_style] = new_style
        el_p.attrib["count"] = str(new_style + 1)
        return new_style

    drawing_no = 0
    images = {}
    while True:
        drawing_no += 1
        drawing_path = "xl/drawings/drawing%s.xml" % drawing_no
        try:
            f = zf_in.open(drawing_path)
        except:
            break
        print("processing drawing %s" % drawing_no)
        tree = etree.parse(f)
        f.close()
        el = tree.find(".//ns:cNvPr", namespaces={"ns": nsd})
        expr = el.attrib.get("name")
        if not expr:
            continue
        fname = template.render_template(expr, data).strip()
        print("drawing fname", fname)
        if not fname:
            continue

        f = zf_in.open("xl/drawings/_rels/drawing%s.xml.rels" % drawing_no)
        rel_tree = etree.parse(f)
        f.close()
        rels = {}
        for el in rel_tree.findall(".//ns:Relationship", namespaces={"ns": nsr}):
            rels[el.attrib.get("Id")] = el.attrib.get("Target")

        el_blip = tree.find(".//ns:blip", namespaces={"ns": nsd2})
        embed = el_blip.attrib["{%s}embed" % nsr2]
        zip_path = rels[embed].replace("..", "xl")
        print("zip_path", zip_path)

        img_data = open(fname, "rb").read()
        images[zip_path] = img_data

    sheet_no = 0
    sheet_out = {}
    while True:
        sheet_no += 1
        sheet_path = "xl/worksheets/sheet%s.xml" % sheet_no
        try:
            f = zf_in.open(sheet_path)
        except:
            break
        print("processing sheet %s" % sheet_no)
        print("converting to inline strings...")
        tree = etree.parse(f)
        f.close()
        for el in tree.findall(".//ns:c[@t='s']", namespaces={"ns": ns}):
            i = int(el[0].text)
            s = strings[i]
            if not s:
                continue
            if s.find("{{currency") != -1:
                el.attrib["t"] = "n"
                if "s" in el.attrib:
                    old_style = int(el.attrib["s"])
                else:
                    old_style = None
                new_style = _make_currency_style(old_style)
                el.attrib["s"] = str(new_style)

                def _repl(m):
                    return "{{currency %s nogroup=1}}" % m.group(1)
                el[0].text = re.sub(r"{{currency (.*?)}}", _repl, s)
            else:
                el.remove(el[0])
                el.attrib["t"] = "inlineStr"
                el_t = etree.Element("t")
                el_t.text = s
                el_is = etree.Element("is")
                el_is.append(el_t)
                el.append(el_is)

        #print("*** SHEET_SRC *********************")
        # print(etree.tostring(tree,pretty_print=True).decode()[:10000])

        print("renaming template expressions...")
        for el in tree.findall(".//t"):
            s = el.text
            if s.find("{{#") == -1 and s.find("{{/") == -1:
                continue
            s = s.replace("\u201c", "\"")  # XXX
            s = s.replace("\u201d", "\"")
            # print("XXX",s)
            col = el.getparent().getparent()
            row = col.getparent()
            p = row.getparent()
            if get_cell_col(col.attrib["r"]) == "A":
                i = p.index(row)
                p.remove(row)
                if i == 0:
                    p.text = (p.text or "") + "\n" + s + "\n"
                else:
                    p[i - 1].tail = (p[i - 1].tail or "") + "\n" + s + "\n"
            else:
                if s.find("{{#") != -1 and s.find("{{/") == -1:
                    col_no = get_cell_col(col.attrib["r"])
                    m = re.search("{{#each (.*?)}}", s)
                    if not m:
                        raise Exception("Invalid 'each' expression")
                    list_name = m.group(1)
                    i = row.index(col)
                    if i == 0:
                        row.text = "\n{{#first %s}}\n" % list_name
                    else:
                        row[i - 1].tail = "\n{{#first %s}}\n" % list_name
                    for col2 in row:
                        if get_cell_col(col2.attrib["r"]) >= col_no:
                            row.remove(col2)
                    next_row = row.getnext()
                    for next_col in next_row:
                        if get_cell_col(next_col.attrib["r"]) >= col_no:
                            col2 = deepcopy(next_col)
                            row.append(col2)
                    row[-1].tail = "\n{{/first}}\n"
                    row.tail = "\n{{#after_first %s}}\n" % list_name
                elif s.find("{{#") == -1 and s.find("{{/") != -1:
                    i = p.index(row)
                    p[i - 1].tail = "\n{{/after_first}}\n"
                    p.remove(row)

        sheet_tmpl = etree.tostring(tree, pretty_print=True).decode()
        sheet_tmpl = sheet_tmpl.replace("&#8220;", "\"")
        sheet_tmpl = sheet_tmpl.replace("&#8221;", "\"")
        #print("*** SHEET_TMPL *********************")
        # print(sheet_tmpl[:10000])
        print("template size: %s chars" % len(sheet_tmpl))
        print("number of pieces: %d" % len(sheet_tmpl.split("{{")))
        print("rendering template...")
        if fast_render:  # FIXME; remove need for this!!! (make new compiler faster)
            print(">>> FAST RENDER!!!")
            sheet_xml = template.render_template_old(sheet_tmpl, data)
        else:
            sheet_xml = template.render_template(sheet_tmpl, data)
        #print("*** SHEET_XML1 **********************")
        # print(sheet_xml[:10000])

        print("parsing rendered tree...")
        tree = etree.fromstring(sheet_xml)

        print("renaming cells...")
        # rename rows/cells
        row_no = 1
        cell_rename = {}
        for el_row in tree.findall(".//ns:row", namespaces={"ns": ns}):
            el_row.attrib["r"] = str(row_no)
            for el_c in el_row:
                old_r = el_c.attrib["r"]
                new_r = get_cell_col(old_r) + str(row_no)
                el_c.attrib["r"] = new_r
                cell_rename[old_r] = new_r
            row_no += 1

        # rename merged cells
        for el in tree.findall(".//ns:mergeCell", namespaces={"ns": ns}):
            old_ref = el.attrib["ref"]
            c1, c2 = old_ref.split(":")
            if c1 in cell_rename and c2 in cell_rename:
                new_ref = cell_rename[c1] + ":" + cell_rename[c2]
                el.attrib["ref"] = new_ref
            else:
                el.getparent().remove(el)
        el = tree.find(".//ns:mergeCells", namespaces={"ns": ns})
        if el is not None:
            if len(el) > 0:
                el.attrib["count"] = str(len(el))
            else:
                el.getparent().remove(el)

        # fix OO bug for column widths
        for el in tree.findall(".//ns:col", namespaces={"ns": ns}):
            if el.attrib.get("width") is not None:
                el.attrib["customWidth"] = "1"

        print("hiding columns...")
        # hide columns
        del_cols = []
        for el in tree.xpath(".//ns:t[text()='[[HIDE_COL]]']", namespaces={"ns": ns}):
            c = el.getparent().getparent()
            r = c.attrib.get("r")
            if not r:
                continue
            col = get_cell_col(r)
            i = ord(col) - ord("A") + 1
            del_cols.append(i)
        del_cols = sorted(list(set(del_cols)))
        if del_cols:
            print("DEL_COLS", del_cols)
            cols_el = tree.find(".//ns:cols", namespaces={"ns": ns})
            for col_el in cols_el:  # XXX: need to be careful if max>min (multi cols in 1 col tag)
                cmin = int(col_el.attrib.get("min", "1"))
                cmax = int(col_el.attrib.get("max", "999"))
                hide = False
                for i in del_cols:
                    if i >= cmin and i <= cmax:
                        hide = True
                        break
                if hide:
                    col_el.attrib["hidden"] = "true"
                    col_el.attrib["width"] = "0"

        print("writing sheet out...")
        sheet_out[sheet_path] = etree.tostring(tree, pretty_print=True).decode()
        #print("*** SHEET_XML2 **********************")
        # print(sheet_xml[:10000])

    print("updating zip file...")
    f_out = BytesIO()
    zf_out = zipfile.ZipFile(f_out, "w")
    for name in zf_in.namelist():
        if name in sheet_out:
            zf_out.writestr(name, sheet_out[name])
        elif name in images:
            zf_out.writestr(name, images[name])
        elif name == "xl/styles.xml":
            styles_xml = etree.tostring(style_tree, pretty_print=True).decode()
            zf_out.writestr("xl/styles.xml", styles_xml)
        else:
            data = zf_in.read(name)
            zf_out.writestr(name, data)
    zf_out.close()
    zf_in.close()
    data = f_out.getvalue()
    f_out.close()
    return data


def report_render_doc(tmpl_name, data):  # Do they delete the newline thing
    print("report_render_doc", tmpl_name, data)
    tmpl_data = _get_report_template(tmpl_name, "docx")
    tmpl_f = BytesIO(tmpl_data)
    zf = zipfile.ZipFile(tmpl_f)

    ns = "http://schemas.openxmlformats.org/package/2006/relationships"
    f = zf.open("word/_rels/document.xml.rels")
    tree = etree.parse(f)
    rels = {}
    for el in tree.findall(".//ns:Relationship", namespaces={"ns": ns}):
        rels[el.attrib.get("Id")] = el.attrib.get("Target")
    print("rels", rels)

    ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    f = zf.open("word/document.xml")
    tree = etree.parse(f)
    print("*** DOC_SRC *********************")
    print(etree.tostring(tree, pretty_print=True).decode())

    for el in tree.iterfind(".//w:p", namespaces={"w": ns}):
        vals = []
        start = False
        parent = el
        children = el.findall(".//w:r", namespaces={"w": ns})
        vals = []
        for child in children:
            grandchild = child.findall(".//w:t", namespaces={"w": ns})
            if grandchild and grandchild[0].text:
                text = grandchild[0].text
                if text and (text.find("{{") != -1 or text.find("}}") != -1 or start):
                    vals.append((text, child, grandchild[0]))
                    start = False if text.find("}}") != -1 and not (text.find("{{") != -1) else True
        text = "".join([text for text, c, g in vals])
        for i, val in enumerate(vals):
            if i == 0:
                val[2].text = text
            else:
                parent.remove(val[1])

    images = {}
    change_list = []
    change_list.append("word/document.xml")
    for el in tree.iterfind(".//w:tc", namespaces={"w": ns}):
        expr = el.xpath("string()")
        if expr and expr.find("{{image") != -1:
            expr = expr.replace("\u201c", "\"")  # XXX
            expr = expr.replace("\u201d", "\"")
            m = re.search("{{image \"(.*?)\"", expr)
            if not m:
                raise Exception("Failed to parse image expression: %s" % expr)
            n = m.group(1)
            v = data.get(n)
            if v:
                res = el.findall(".//w:drawing", namespaces={"w": ns})
                if not res:
                    raise Exception("Failed to replace image")
                el_drawing = res[0]
                el_blip = el_drawing.find(
                    ".//a:blip", namespaces={"a": "http://schemas.openxmlformats.org/drawingml/2006/main"})
                embed = el_blip.attrib["{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed"]
                zip_path = rels[embed]
                change_list.append("word/" + zip_path)
                images[zip_path] = v
                el_extent = el_drawing.find(
                    ".//wp:extent", namespaces={"wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"})
                cx = int(el_extent.attrib["cx"])
                cy = int(el_extent.attrib["cy"])
                img_path = utils.get_file_path(v)
                img = Image.open(img_path)
                w, h = img.size
                scale = min(float(cx) / w, float(cy) / h)
                cx2 = int(round(w * scale))
                cy2 = int(round(h * scale))
                el_extent.attrib["cx"] = str(cx2)
                el_extent.attrib["cy"] = str(cy2)
                el_ext = el_drawing.find(
                    ".//a:ext", namespaces={"a": "http://schemas.openxmlformats.org/drawingml/2006/main"})
                el_ext.attrib["cx"] = str(cx2)
                el_ext.attrib["cy"] = str(cy2)
            wts = el.findall(".//w:t", namespaces={"w": ns})
            for wt in wts:
                if wt.text and wt.text.find("{{image") != -1:
                    parent = wt.getparent()
                    grandparent = parent.getparent()
                    grandparent.remove(parent)

    for el in tree.iterfind(".//w:t", namespaces={"w": ns}):
        expr = el.text
        if expr.find("{{#") != -1 or expr.find("{{/") != -1:
            el_tr = el.getparent()
            while not el_tr.tag.endswith("}tr"):
                el_tr = el_tr.getparent()
            p = el_tr.getparent()
            # XXX
            if p:
                i = p.index(el_tr)
                p.remove(el_tr)
                p[i - 1].tail = expr

    doc_tmpl = etree.tostring(tree, pretty_print=True, encoding="unicode")
    doc_tmpl = doc_tmpl.replace("&#8220;", "\"")
    doc_tmpl = doc_tmpl.replace("&#8221;", "\"")
    doc_tmpl = doc_tmpl.replace("\u201c", "\"")
    doc_tmpl = doc_tmpl.replace("\u201d", "\"")
    print("*** DOC_TMPL *********************")
    for i, l in enumerate(doc_tmpl.split("\n")):
        print(i + 1, l)
    doc_xml = template.render_template(doc_tmpl, data)
    f_out = BytesIO()
    zf_out = zipfile.ZipFile(f_out, "w")
    for name in zf.namelist():
        print("XXX", name)
        if name in change_list:
            pass
        else:
            data = zf.read(name)
            zf_out.writestr(name, data)
    for zip_path, v in images.items():
        img_path = utils.get_file_path(v)
        img_data = open(img_path, "rb").read()
        zf_out.writestr("word/" + zip_path, img_data)
    zf_out.writestr("word/document.xml", doc_xml)
    zf.close()
    zf_out.close()
    data = f_out.getvalue()
    f_out.close()
    return data


def _get_report_template(name, report_type):
    db = database.get_connection()
    res = db.get("SELECT file FROM report_template WHERE name=%s AND format=%s", name, report_type)
    if res:
        path = utils.get_file_path(res.file)
        data = open(path, "rb").read()
        return data
    loaded_modules = module.get_loaded_modules()
    for m in reversed(loaded_modules):
        f = "reports/" + name + "." + report_type
        if not pkg_resources.resource_exists(m, f):
            continue
        data = pkg_resources.resource_string(m, f)
        return data
    raise Exception("Report not found: %s" % name)


def convert_to_pdf(data, data_fmt):
    print("convert_to_pdf", data_fmt)
    fd_in, path_in = tempfile.mkstemp(suffix="." + data_fmt)
    f_in = os.fdopen(fd_in, "wb")
    f_in.write(data)
    f_in.flush()
    path_out = os.path.splitext(path_in)[0] + ".pdf"
    res = os.system("jodconverter %s %s" % (path_in, path_out))
    print(res)
    f_in.close()
    out_data = open(path_out, "rb").read()
    #remove tmp file
    for f in [path_in, path_out]:
        os.remove(f)
    return out_data


def merge_pdf(pdfs):
    if not pdfs:
        return None
    if len(pdfs) == 1:
        return pdfs[0]
    out_pdf = PdfFileWriter()
    for data in pdfs:
        in_f = BytesIO(data)
        in_pdf = PdfFileReader(in_f)
        num_pages = in_pdf.getNumPages()
        for i in range(num_pages):
            out_pdf.addPage(in_pdf.getPage(i))
    out_f = BytesIO()
    out_pdf.write(out_f)
    return out_f.getvalue()


def check_hbs_expression(e):
    # TODO: replace by better check
    if e.find("{{") == -1:
        return False
    if e.find("}}") == -1:
        return False
    if e.find("{{image") != -1:
        raise Exception("{{image ...}} expressions are deprecated, please replace them in template.")
    e2 = e.replace("{{{", "").replace("}}}", "")
    e2 = e2.replace("{{", "").replace("}}", "")
    for c in ("{", "}", "<", ">", "\n"):
        if c in e2:
            return False
    return True


def get_next_text_pos(pos):
    el, where = pos
    if where == "text":
        if len(el) > 0:
            return el[0], "text"
        return el, "tail"
    elif where == "tail":
        if el.getnext() is not None:
            return el.getnext(), "text"
        el = el.getparent()
        if el is None:
            return None
        return el, "tail"
    else:
        raise Exception("Invalid position")


def get_pos_text(pos):
    el, where = pos
    if where == "text":
        return el.text
    elif where == "tail":
        return el.tail
    else:
        raise Exception("Invalid position")


def set_pos_text(pos, t):
    el, where = pos
    if where == "text":
        el.text = t
    elif where == "tail":
        el.tail = t
    else:
        raise Exception("Invalid position")


def get_text_chunks(tree):
    p = (tree.getroot(), "text")
    chunks = []
    while True:
        t = get_pos_text(p)
        if t:
            chunks.append((p, t))
        p = get_next_text_pos(p)
        if not p:
            break
    return chunks


def get_common_parent(el1, el2):
    parents1 = set()
    p = el1
    while p is not None:
        parents1.add(p)
        p = p.getparent()
    p = el2
    while p is not None:
        if p in parents1:
            return p
        p = p.getparent()
    return None


def check_el_contains(p, c):
    e = c
    while e is not None:
        if e == p:
            return True
        e = e.getparent()
    return False


def remove_keep_tail(p, i):
    if p[i].tail:
        if i > 0:
            p[i - 1].tail = (p[i - 1].tail or "") + p[i].tail
        else:
            p.text = (p.text or "") + p[i].tail
    p.remove(p[i])


def report_render_odt(tmpl_name, data):
    print("REPORT_RENDER_ODT", tmpl_name, data)
    try:
        tmpl_data = _get_report_template(tmpl_name, "odt")
    except:
        tmpl_data = _get_report_template(tmpl_name, "odt2")  # XXX
    tmpl_f = BytesIO(tmpl_data)
    zf = zipfile.ZipFile(tmpl_f)

    f_out = BytesIO()
    zf_out = zipfile.ZipFile(f_out, "w")
    img_no = 1
    add_files = []
    for tmpl_fname in ("content.xml", "styles.xml"):
        getns = {
            "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
            "draw": "urn:oasis:names:tc:opendocument:xmlns:drawing:1.0",
            "svg-com": "urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0",
            "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0"
        }
        ns_manifest = "urn:oasis:names:tc:opendocument:xmlns:manifest:1.0"
        ns_svg = "urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0"
        f = zf.open(tmpl_fname)
        tree = etree.parse(f)
        open("/tmp/odt_template_orig.xml", "w").write(etree.tostring(tree, pretty_print=True).decode())

        chunks = get_text_chunks(tree)
        print("#" * 80)
        print("CHUNKS", [c[1] for c in chunks])
        found_expr = False
        expr = None
        expr_pos = []
        for i, (p, t) in enumerate(chunks):
            if i + 1 < len(chunks):
                next_t = chunks[i + 1][1]
            else:
                next_t = None
            if not found_expr:
                if t.find("{{") != -1 or next_t and t[-1] == "{" and next_t[0] == "{":
                    found_expr = True
                    expr = ""
                    expr_pos = []
            if found_expr:
                expr += t
                expr_pos.append(p)
                if expr.find("}}") != -1:
                    for p in expr_pos:
                        set_pos_text(p, "")
                    set_pos_text(expr_pos[0], expr)
                    print("EXPR", expr)
                    if not check_hbs_expression(expr):
                        raise Exception("Invalid expression: '%s'" % expr)
                    found_expr = False

        open("/tmp/odt_template_join.xml", "w").write(etree.tostring(tree, pretty_print=True).decode())

        chunks = get_text_chunks(tree)
        blocks = []
        level = 0
        for p, t in chunks:
            for expr in re.findall("{{[#/].*?}}", t):
                if expr[2] == "#":
                    blocks.append((p, expr, level))
                    level += 1
                elif expr[2] == "/":
                    level -= 1
                    blocks.append((p, expr, level))
        print("#" * 80)
        print("BLOCKS", [(b[1], b[2]) for b in blocks])
        pairs = []
        for i, (p, t, level) in enumerate(blocks):
            if t[2] == "#":
                found = False
                for p2, t2, level2 in blocks[i + 1:]:
                    if level2 == level:
                        pairs.append((p, t, p2, t2))
                        found = True
                        break
                if not found:
                    raise Exception("No closing expression found for %s" % t)
        print("PAIRS", [(t, t2) for p, t, p2, t2 in pairs])
        for p, t, p2, t2 in pairs:
            if p[0] == p2[0]:
                continue
            parent = get_common_parent(p[0], p2[0])
            start = stop = None
            for i, c in enumerate(parent):
                if check_el_contains(c, p[0]):
                    start = i
                if check_el_contains(c, p2[0]):
                    stop = i
            print("relocate pair: '%s' '%s'" % (t, t2))
            print("  parent=%s start=%s stop=%s" % (parent, start, stop))
            if stop > start + 1:
                remove_keep_tail(parent, start)
                remove_keep_tail(parent, stop - 1)
                if start > 0:
                    parent[start - 1].tail = (parent[start - 1].tail or "") + t
                else:
                    parent.text = (parent.text or "") + t
                parent[stop - 2].tail = t2 + (parent[stop - 2].tail or "")

        open("/tmp/odt_template_block.xml", "w").write(etree.tostring(tree, pretty_print=True).decode())

        def _repl(m):
            var = m.group(1)
            return "{{{odt_linebreak %s}}}" % var
        textp = tree.findall(".//*")
        for textel in textp:
            if not textel.text:
                continue
            if textel.text.find("{{") == -1:
                continue
            t = re.sub("{{\s*(\w+)\s*}}", _repl, textel.text)
            if t != textel.text:
                textel.text = t

        doc_tmpl = etree.tostring(tree, pretty_print=True, encoding="unicode")
        # XXX sometimes they're found as "”" instead of &#8221;
        doc_tmpl = doc_tmpl.replace("“", "\"")
        doc_tmpl = doc_tmpl.replace("”", "\"")
        doc_tmpl = doc_tmpl.replace("&#8220;", "\"")
        doc_tmpl = doc_tmpl.replace("&#8221;", "\"")

        open("/tmp/odt_template_use.xml", "w").write(doc_tmpl)
        odt_xml = template.render_template(doc_tmpl, data)  # XXX
        open("/tmp/odt_render_out.xml", "w").write(odt_xml)

        tree = etree.fromstring(odt_xml)
        for frame_el in tree.findall(".//draw:frame", namespaces={"draw": getns["draw"]}):
            title_el = frame_el.find("svg:title", namespaces={"svg": ns_svg})
            if title_el is not None:
                fname = title_el.text
            else:
                fname = None
            if not fname:
                continue
            img_path = utils.get_file_path(fname)
            if not os.path.exists(img_path):  # XXX
                continue
            new_zip_path = "Pictures/_img%d.png" % img_no
            img_no += 1
            image_el = frame_el[0]
            image_el.attrib['{http://www.w3.org/1999/xlink}href'] = new_zip_path
            add_files.append((new_zip_path, img_path, "image/png"))
            cx = frame_el.attrib["{%s}width" % getns["svg-com"]]
            cy = frame_el.attrib["{%s}height" % getns["svg-com"]]
            cx_unit = cx[-2] + cx[-1]
            cx = cx[:-2]
            cy_unit = cy[-2] + cy[-1]
            cy = cy[:-2]
            img = Image.open(img_path)
            w, h = img.size
            scale = min(float(cx) / w, float(cy) / h)
            cx2 = w * scale
            cy2 = h * scale
            frame_el.attrib["{%s}width" % getns["svg-com"]] = str(cx2) + cx_unit
            frame_el.attrib["{%s}height" % getns["svg-com"]] = str(cy2) + cy_unit

        odt_xml = etree.tostring(tree, pretty_print=True, encoding="unicode")
        zf_out.writestr(tmpl_fname, odt_xml)

    print("add_files", add_files)

    f = zf.open("META-INF/manifest.xml")
    tree = etree.parse(f)
    root = tree.getroot()
    for zip_path, img_path, media_type in add_files:
        el = etree.Element("{%s}file-entry" % ns_manifest)
        el.attrib["{%s}full-path" % ns_manifest] = zip_path
        el.attrib["{%s}media-type" % ns_manifest] = media_type
        root.append(el)
    manif_xml = etree.tostring(tree, pretty_print=True, encoding="unicode")
    zf_out.writestr("META-INF/manifest.xml", manif_xml)

    out_names = set(zf_out.namelist())
    for name in zf.namelist():
        if name in out_names:
            continue
        data = zf.read(name)
        zf_out.writestr(name, data)
    for zip_path, img_path, media_type in add_files:
        img_data = open(img_path, "rb").read()
        zf_out.writestr(zip_path, img_data)
    zf.close()
    zf_out.close()
    data = f_out.getvalue()
    f_out.close()
    return data


def report_render_ods(tmpl_name, data):
    print("REPORT_RENDER_ODS", tmpl_name, data)
    tmpl_data = _get_report_template(tmpl_name, "ods")
    return tmpl_data  # TODO: fill template


def report_render_to_file(model, ids, method="get_report_data", template=None, template_format=None, out_format="pdf", context={}):
    print("report_render_to_file", model, ids, method, template, template_format, out_format)
    m = get_model(model)
    f = getattr(m, method, None)
    if not f:
        raise Exception("Invalid method %s of %s" % (method, model))
    if template_format in ("odt", "jrxml", "docx"):  # XXX: deprecated
        ctx = {
            "ids": ids,
            "refer_id": ids[0],
        }
        in_data = f(context=ctx)
    else:
        in_data = f(ids, context=context)
    if template_format == "odt":  # XXX: deprecated
        out_data = report_render_odt(template, in_data)
        if out_format == "pdf":
            out_data = convert_to_pdf(out_data, "odt")
    elif template_format == "odt2":
        out_data = report_render_odt(template, in_data)
        if out_format == "pdf":
            out_data = convert_to_pdf(out_data, "odt")
    elif template_format == "docx":  # XXX: deprecated
        out_data = report_render_doc(template, in_data)
        if out_format == "pdf":
            out_data = convert_to_pdf(out_data, "docx")
    elif template_format == "jrxml":  # XXX: deprecated
        out_data = get_report(template, in_data, format=out_format)
    elif template_format == "jrxml2":
        out_data = get_report(template, in_data, format=out_format)
    else:
        raise Exception("Invalid template format: %s" % template_format)
    fname = "%s.%s" % (template, out_format)
    rand = base64.urlsafe_b64encode(os.urandom(8)).decode()
    res = os.path.splitext(fname)
    fname2 = res[0] + "," + rand + res[1]
    dbname = database.get_active_db()
    fdir = os.path.join("static", "db", dbname, "files")
    if not os.path.exists(fdir):
        os.makedirs(fdir)
    path = os.path.join(fdir, fname2)
    f = open(path, "wb")
    f.write(out_data)
    f.close()
    return fname2
