import difflib
import hashlib
import html
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
import warnings
import zipfile
from bisect import bisect_right
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

warnings.filterwarnings("ignore")

VERSION = "1.0.0"

IMG_IN = (".png", ".jpg", ".jpeg", ".heic", ".heif", ".webp", ".tif", ".tiff", ".gif", ".bmp")
WORD_IN = (".doc", ".docx", ".docm", ".rtf", ".odt")
PPT_IN = (".pptx", ".ppt", ".pptm")

MODES = {
    "heic-png": ("Images", "HEIC photo to PNG", (".heic", ".heif")),
    "heic-jpg": ("Images", "HEIC photo to JPG", (".heic", ".heif")),
    "png-heic": ("Images", "PNG to HEIC (smaller files)", (".png",)),
    "jpg-heic": ("Images", "JPG to HEIC (smaller files)", (".jpg", ".jpeg")),
    "png-jpg": ("Images", "PNG to JPG (transparency becomes white)", (".png",)),
    "jpg-png": ("Images", "JPG to PNG", (".jpg", ".jpeg")),
    "webp-png": ("Images", "WebP to PNG", (".webp",)),
    "webp-jpg": ("Images", "WebP to JPG", (".webp",)),
    "img-pdf": ("Images", "several images into one PDF, one image per page", IMG_IN),
    "pdf-png": ("PDF", "PDF pages to a folder of PNG files [N pages]", (".pdf",)),
    "pdf-jpg": ("PDF", "PDF pages to a folder of JPG files [N pages]", (".pdf",)),
    "pdf-txt": ("PDF", "PDF to plain text", (".pdf",)),
    "pdf-word": ("PDF", "PDF to editable Word DOCX", (".pdf",)),
    "pdf-epub": ("PDF", "PDF to reflowable EPUB with chapters and images", (".pdf",)),
    "epub-pdf": ("Books", "EPUB book to PDF with bookmarks", (".epub",)),
    "word-pdf": ("Documents", "Word document to PDF", WORD_IN),
    "word-pages": ("Documents", "Word document to Pages", WORD_IN),
    "pages-pdf": ("Documents", "Pages document to PDF", (".pages",)),
    "pages-word": ("Documents", "Pages document to Word DOCX", (".pages",)),
    "numbers-xlsx": ("Spreadsheets", "Numbers to Excel XLSX", (".numbers",)),
    "numbers-pdf": ("Spreadsheets", "Numbers to PDF", (".numbers",)),
    "xlsx-numbers": ("Spreadsheets", "Excel or CSV to Numbers", (".xlsx", ".xls", ".csv")),
    "key-pdf": ("Presentations", "Keynote to PDF", (".key",)),
    "key-pptx": ("Presentations", "Keynote to PowerPoint", (".key",)),
    "pptx-key": ("Presentations", "PowerPoint to Keynote", PPT_IN),
    "pptx-pdf": ("Presentations", "PowerPoint to PDF", PPT_IN),
    "mov-mp4": ("Video", "MOV or M4V video to MP4", (".mov", ".m4v", ".mp4")),
}

PACKAGE_EXTS = (".key", ".pages", ".numbers")
PAGE_MODES = ("pdf-png", "pdf-jpg")
RENDER_DPI = 200
TTY = sys.stdout.isatty()


def color(text, code):
    return f"\033[{code}m{text}\033[0m" if TTY else text


class ConvertError(Exception):
    pass


class UsageError(Exception):
    pass


HELP = """convert {version} - file converter for macOS

Usage:
  convert <mode> <file> [file ...] [options]
  convert pdf-png <file.pdf> [N] [options]
  convert img-pdf <image> [image ...] [-o name] [options]

Modes:
{modes}

Options:
  -d, --dir <folder>   where to save results (default: current folder)
  -o, --output <name>  output file name, when there is a single result
  -f, --force          overwrite existing files instead of adding _1, _2 ...
  -h, --help           show this help
  -v, --version        show version
      --setup          reinstall Python dependencies

N (pdf-png and pdf-jpg only):
  how many pages to render, from 1 to the page count. Without N all pages
  are rendered. The result is a folder <name>_png or <name>_jpg.

Examples:
  convert heic-jpg IMG_0042.HEIC
  convert heic-png ~/Downloads/*.heic -d ~/Pictures/png
  convert png-heic screenshot.png
  convert img-pdf scan1.jpg scan2.jpg scan3.jpg -o passport
  convert pdf-png report.pdf 3
  convert pdf-txt paper.pdf
  convert pdf-epub book.pdf
  convert word-pdf "Contract.docx" -d ~/Documents
  convert numbers-xlsx budget.numbers
  convert key-pptx slides.key
  convert mov-mp4 screen_recording.mov

Engines:
  images            sips (built into macOS), img-pdf uses PyMuPDF
  pdf-png, pdf-jpg  PyMuPDF at {dpi} dpi
  pdf-txt           PyMuPDF text extraction in reading order
  pdf-word          pdf2docx: paragraphs, tables, images, fonts
  pdf-epub          layout analysis: paragraphs, headings, chapters from the
                    PDF outline, images, running headers and page numbers removed
  epub-pdf          MuPDF: book styles, images, table of contents as bookmarks
  word-pdf          Microsoft Word, then Pages, then LibreOffice
  pptx-pdf          Microsoft PowerPoint, then Keynote, then LibreOffice
  pages, numbers    Pages and Numbers
  key, pptx-key     Keynote
  mov-mp4           avconvert (built into macOS)
"""


def print_help():
    width = max(len(m) for m in MODES)
    lines, group = [], None
    for m, (g, d, _) in MODES.items():
        if g != group:
            if group is not None:
                lines.append("")
            lines.append(f"  {g}:")
            group = g
        lines.append(f"    {m.ljust(width)}   {d}")
    print(HELP.format(version=VERSION, modes="\n".join(lines), dpi=RENDER_DPI))


def info(msg):
    print(msg, flush=True)


def warn(msg):
    print(color("Warning: ", "33") + msg, file=sys.stderr, flush=True)


def show(p):
    p = Path(p)
    cwd, home = Path.cwd(), Path.home()
    if cwd != Path("/") and p.is_relative_to(cwd):
        return str(p.relative_to(cwd)) or "."
    if p.is_relative_to(home):
        return "~/" + str(p.relative_to(home))
    return str(p)


def parse_args(argv):
    if not argv or argv[0] in ("-h", "--help", "help"):
        print_help()
        sys.exit(0)
    if argv[0] in ("-v", "--version"):
        print(f"convert {VERSION}")
        sys.exit(0)
    mode = argv[0].lower()
    if mode not in MODES:
        close = difflib.get_close_matches(mode, MODES.keys(), n=1)
        hint = f" Did you mean: {close[0]}?" if close else ""
        raise UsageError(f"unknown mode '{argv[0]}'.{hint}")
    files, pages, out_dir, name, force = [], None, None, None, False
    i = 1
    while i < len(argv):
        a = argv[i]
        if a in ("-h", "--help"):
            print_help()
            sys.exit(0)
        if a in ("-d", "--dir", "-o", "--output"):
            if i + 1 >= len(argv) or not argv[i + 1]:
                raise UsageError(f"{a} needs a value")
            if a in ("-d", "--dir"):
                out_dir = argv[i + 1]
            else:
                name = argv[i + 1]
            i += 2
            continue
        if a.startswith("--dir="):
            out_dir = a.split("=", 1)[1]
        elif a.startswith("--output="):
            name = a.split("=", 1)[1]
        elif a in ("-f", "--force"):
            force = True
        elif re.fullmatch(r"[+-]?\d+", a) and not Path(a).exists():
            if mode not in PAGE_MODES:
                raise UsageError("a page count is only allowed for pdf-png and pdf-jpg")
            if pages is not None:
                raise UsageError("the page count is given twice")
            pages = int(a)
            if pages < 1:
                raise UsageError("the page count must be at least 1")
        elif a.startswith("-") and not Path(a).exists():
            raise UsageError(f"unknown option '{a}'")
        else:
            files.append(a)
        i += 1
    if not files:
        raise UsageError(f"no input file given for mode {mode}")
    if name is not None:
        if mode != "img-pdf" and len(files) > 1:
            raise UsageError("-o can only be used with a single input file (or with img-pdf)")
        name = Path(name).name
        for ext in (".pdf", ".png", ".jpg", ".heic", ".txt", ".docx", ".epub", ".pages", ".xlsx", ".numbers", ".pptx", ".key", ".mp4"):
            if name.lower().endswith(ext):
                name = name[: -len(ext)]
        if not name or name in (".", ".."):
            raise UsageError("invalid output name")
    return mode, files, pages, out_dir, name, force


def prepare_out_dir(out_dir):
    if out_dir is None:
        return Path.cwd()
    p = Path(out_dir).expanduser()
    if not p.is_absolute():
        p = Path.cwd() / p
    if p.exists() and not p.is_dir():
        raise UsageError(f"'{out_dir}' exists and is not a folder")
    try:
        p.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise UsageError(f"cannot create folder '{out_dir}': {e.strerror}")
    if not os.access(p, os.W_OK):
        raise UsageError(f"no write permission for folder '{out_dir}'")
    return p


def check_source(path, mode):
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = Path.cwd() / p
    if not p.exists():
        raise ConvertError("file not found")
    exts = MODES[mode][2]
    if p.suffix.lower() not in exts:
        raise ConvertError(f"mode {mode} expects {' / '.join(exts)}, got '{p.suffix or 'no extension'}'")
    if p.is_dir() and p.suffix.lower() not in PACKAGE_EXTS:
        raise ConvertError("this is a folder, not a file")
    if not os.access(p, os.R_OK):
        raise ConvertError("no permission to read the file")
    return p.resolve()


def target(out_dir, stem, ext, force):
    p = out_dir / f"{stem}{ext}"
    if force or not p.exists():
        return p
    n = 1
    while True:
        p = out_dir / f"{stem}_{n}{ext}"
        if not p.exists():
            return p
        n += 1


def remove_path(p):
    if p.is_dir() and not p.is_symlink():
        shutil.rmtree(p)
    elif p.exists() or p.is_symlink():
        p.unlink()


def place(tmp, dst):
    if not tmp.exists() or (tmp.is_file() and tmp.stat().st_size == 0):
        raise ConvertError("the result was not created")
    if dst.exists():
        remove_path(dst)
    shutil.move(str(tmp), str(dst))
    return dst


def run(cmd, timeout=900):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise ConvertError(f"program not found: {cmd[0]}")
    except subprocess.TimeoutExpired:
        raise ConvertError(f"{Path(cmd[0]).name} did not finish in {timeout} s")
    return r


def load_fitz():
    try:
        import pymupdf
    except ImportError:
        raise ConvertError("PyMuPDF is not installed. Run: convert --setup")
    pymupdf.TOOLS.mupdf_display_errors(False)
    pymupdf.TOOLS.mupdf_display_warnings(False)
    return pymupdf


def open_pdf(src):
    fz = load_fitz()
    try:
        doc = fz.open(str(src))
    except Exception as e:
        raise ConvertError(f"cannot open PDF: {e}")
    if not doc.is_pdf:
        raise ConvertError("not a PDF file")
    if doc.needs_pass and not doc.authenticate(""):
        raise ConvertError("the PDF is password protected")
    if doc.page_count == 0:
        raise ConvertError("the PDF has no pages")
    return doc


def sips(src, dst_file, fmt, quality=None):
    cmd = ["sips", "-s", "format", fmt]
    if quality:
        cmd += ["-s", "formatOptions", str(quality)]
    cmd += [str(src), "--out", str(dst_file)]
    r = run(cmd, timeout=300)
    if r.returncode != 0 or not dst_file.exists():
        msg = (r.stderr or r.stdout).strip().splitlines()
        raise ConvertError("sips could not convert the file" + (f": {msg[-1]}" if msg else ""))


def flatten_alpha(path):
    fz = load_fitz()
    pix = fz.Pixmap(str(path))
    if not pix.alpha:
        return path
    doc = fz.open()
    page = doc.new_page(width=pix.width, height=pix.height)
    page.draw_rect(page.rect, color=None, fill=(1, 1, 1))
    page.insert_image(page.rect, pixmap=pix)
    out = path.with_name(path.stem + "_flat.png")
    page.get_pixmap(alpha=False).save(str(out))
    return out


def image_convert(src, out_dir, stem, fmt, force):
    ext = {"jpeg": ".jpg", "png": ".png", "heic": ".heic"}[fmt]
    quality = {"jpeg": 92, "heic": 85}.get(fmt)
    dst = target(out_dir, stem, ext, force)
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td) / f"out{ext}"
        source = src
        if fmt == "jpeg" and src.suffix.lower() == ".png":
            source = flatten_alpha(Path(shutil.copy2(src, Path(td) / "in.png")))
        sips(source, tmp, fmt, quality)
        return place(tmp, dst)


def images_to_pdf(srcs, out_dir, stem, force):
    fz = load_fitz()
    out = fz.open()
    a4w, a4h = 595.28, 841.89
    with tempfile.TemporaryDirectory() as td:
        for i, src in enumerate(srcs, 1):
            if TTY:
                print(f"\r  image {i}/{len(srcs)}", end="", flush=True)
            path = src
            if src.suffix.lower() in (".heic", ".heif", ".webp", ".gif", ".bmp", ".tif", ".tiff"):
                path = Path(td) / f"img{i}.jpg"
                sips(src, path, "jpeg", 92)
            try:
                pix = fz.Pixmap(str(path))
            except Exception as e:
                raise ConvertError(f"cannot read image {src.name}: {e}")
            w, h = (a4w, a4h) if pix.height >= pix.width else (a4h, a4w)
            page = out.new_page(width=w, height=h)
            scale = min(w / pix.width, h / pix.height)
            iw, ih = pix.width * scale, pix.height * scale
            rect = fz.Rect((w - iw) / 2, (h - ih) / 2, (w + iw) / 2, (h + ih) / 2)
            page.insert_image(rect, filename=str(path))
        if TTY:
            print("\r" + " " * 40 + "\r", end="", flush=True)
        out.set_metadata({"title": stem, "creator": "convert"})
        dst = target(out_dir, stem, ".pdf", force)
        tmp = Path(td) / "out.pdf"
        out.save(str(tmp), garbage=3, deflate=True)
        place(tmp, dst)
    return dst, len(srcs)


def pdf_to_images(src, out_dir, stem, ext, pages, force):
    fz = load_fitz()
    doc = open_pdf(src)
    total = doc.page_count
    if pages is not None and pages > total:
        raise ConvertError(f"the document has only {total} pages, {pages} requested. Use a number from 1 to {total}")
    n = total if pages is None else pages
    folder = target(out_dir, f"{stem}_{ext}", "", force)
    created = not folder.exists()
    folder.mkdir(parents=True, exist_ok=True)
    width = max(2, len(str(n)))
    zoom = RENDER_DPI / 72
    i = 0
    try:
        for i in range(n):
            if TTY:
                print(f"\r  page {i + 1}/{n}", end="", flush=True)
            pix = doc[i].get_pixmap(matrix=fz.Matrix(zoom, zoom), alpha=False)
            name = folder / f"{stem}_{str(i + 1).zfill(width)}.{ext}"
            if ext == "jpg":
                pix.save(str(name), jpg_quality=92)
            else:
                pix.save(str(name))
    except KeyboardInterrupt:
        raise
    except Exception as e:
        if created:
            shutil.rmtree(folder, ignore_errors=True)
        raise ConvertError(f"failed to render page {i + 1}: {e}")
    finally:
        if TTY:
            print("\r" + " " * 40 + "\r", end="", flush=True)
    return folder, n


def pdf_to_text(src, out_dir, stem, force):
    doc = open_pdf(src)
    parts = []
    for page in doc:
        parts.append(page.get_text("text", sort=True).strip())
    text = "\n\n".join(p for p in parts if p)
    if len(text) < 20 * doc.page_count:
        warn("the PDF has almost no text layer (looks scanned). OCR is needed to get the text")
    if not text:
        raise ConvertError("no text found in the PDF")
    dst = target(out_dir, stem, ".txt", force)
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td) / "out.txt"
        tmp.write_text(text + "\n", encoding="utf-8")
        place(tmp, dst)
    return dst, doc.page_count


def clean_toc(toc, page_count):
    out, prev = [], 0
    for item in toc:
        lvl, title, page = item[0], str(item[1]).strip(), item[2]
        if not title or page < 1 or page > page_count:
            continue
        lvl = max(1, min(lvl, prev + 1))
        out.append([lvl, title, page])
        prev = lvl
    return out


def epub_to_pdf(src, dst_dir, stem, force):
    fz = load_fitz()
    fz.mupdf.fz_set_user_css("@page { margin: 36pt 34pt 40pt 34pt; } body { margin: 0; }")
    try:
        book = fz.open(str(src))
    except Exception as e:
        raise ConvertError(f"cannot open EPUB: {e}")
    if book.needs_pass:
        raise ConvertError("the EPUB is protected by DRM or a password")
    try:
        book.layout(width=420, height=595, fontsize=11)
        toc = book.get_toc()
        data = book.convert_to_pdf()
    except Exception as e:
        raise ConvertError(f"book layout failed: {e}")
    pdf = fz.open("pdf", data)
    if pdf.page_count == 0:
        raise ConvertError("the book is empty")
    toc = clean_toc(toc, pdf.page_count)
    if toc:
        try:
            pdf.set_toc(toc)
        except Exception:
            warn("could not transfer the table of contents")
    meta = {k: v for k, v in (book.metadata or {}).items() if v and k in ("title", "author", "subject", "keywords")}
    meta.setdefault("title", stem)
    meta["creator"] = "convert"
    meta["producer"] = "MuPDF"
    pdf.set_metadata(meta)
    dst = target(dst_dir, stem, ".pdf", force)
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td) / "out.pdf"
        pdf.save(str(tmp), garbage=3, deflate=True)
        place(tmp, dst)
    return dst, pdf.page_count


def pdf_to_word(src, dst_dir, stem, force):
    doc = open_pdf(src)
    pages = doc.page_count
    text = sum(len(p.get_text("text").strip()) for p in doc)
    doc.close()
    if text < 20 * pages:
        warn("the PDF has almost no text layer (looks scanned). Pages will be images in Word, OCR is needed for text")
    try:
        from pdf2docx import Converter
    except ImportError:
        raise ConvertError("pdf2docx is not installed. Run: convert --setup")
    logging.disable(logging.CRITICAL)
    dst = target(dst_dir, stem, ".docx", force)
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td) / "out.docx"
        try:
            cv = Converter(str(src))
            cv.convert(str(tmp), multi_processing=False)
            cv.close()
        except Exception as e:
            raise ConvertError(f"pdf2docx: {e}")
        place(tmp, dst)
    return dst


def video_to_mp4(src, out_dir, stem, force):
    dst = target(out_dir, stem, ".mp4", force)
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td) / "out.mp4"
        r = run(["avconvert", "--preset", "PresetHighestQuality", "--source", str(src), "--output", str(tmp)], timeout=7200)
        if r.returncode != 0 or not tmp.exists():
            msg = (r.stderr or r.stdout).strip().splitlines()
            raise ConvertError("avconvert could not convert the video" + (f": {msg[-1]}" if msg else ""))
        place(tmp, dst)
    return dst


def app_path(name):
    for base in (Path("/Applications"), Path.home() / "Applications", Path("/System/Applications")):
        p = base / f"{name}.app"
        if p.exists():
            return p
    return None


def soffice_path():
    p = shutil.which("soffice") or shutil.which("libreoffice")
    if p:
        return p
    p = Path("/Applications/LibreOffice.app/Contents/MacOS/soffice")
    return str(p) if p.exists() else None


def osa(script, *args):
    r = run(["osascript", "-e", script, *map(str, args)], timeout=900)
    if r.returncode != 0:
        msg = r.stderr.strip()
        if r.returncode < 0:
            raise ConvertError("osascript was interrupted")
        if "-1743" in msg or "Not authorized" in msg:
            raise ConvertError("no permission to control the app. Open System Settings -> Privacy & Security -> Automation and allow your terminal")
        if "-1712" in msg:
            raise ConvertError("the app did not respond in time (a dialog may be open in it)")
        if "-128" in msg:
            raise ConvertError("the operation was canceled in the app (a dialog may be open in it)")
        msg = re.sub(r"^\d+:\d+: execution error: ", "", msg)
        raise ConvertError(msg or "AppleScript error")
    return r.stdout.strip()


OSA_TEMPLATE = """on run argv
set inPath to item 1 of argv
set outPath to item 2 of argv
set fileName to item 3 of argv
set inFile to POSIX file inPath
set outFile to POSIX file outPath
set wasRunning to application "{app}" is running
if not wasRunning then
tell application "{app}" to launch
repeat 40 times
if application "{app}" is running then exit repeat
delay 0.25
end repeat
delay 1.5
end if
set d to missing value
try
tell application "{app}"
with timeout of 600 seconds
{body}
{close}
set d to missing value
end timeout
end tell
on error errMsg number errNum
if d is not missing value then
try
tell application "{app}" to {close}
end try
end if
my finish(wasRunning)
error errMsg number errNum
end try
my finish(wasRunning)
end run

on finish(wasRunning)
if not wasRunning then
try
tell application "{app}"
if (count of {docs}) is 0 then quit
end tell
end try
end if
end finish"""

IWORK_CLOSE = "close d saving no"

SCRIPTS = {
    "word-pdf": ("Microsoft Word", "documents", "close d saving no", """open inFile
try
set d to first document whose name is fileName
on error
set d to active document
end try
save as d file name outPath file format format PDF"""),
    "ppt-pdf": ("Microsoft PowerPoint", "presentations", "close d", """open inFile
set d to active presentation
save d in (outFile as text) as save as PDF"""),
    "pages-pdf": ("Pages", "documents", IWORK_CLOSE, """set d to open inFile
export d to outFile as PDF with properties {image quality:Best}"""),
    "pages-docx": ("Pages", "documents", IWORK_CLOSE, """set d to open inFile
export d to outFile as Microsoft Word"""),
    "pages-save": ("Pages", "documents", IWORK_CLOSE, """set d to open inFile
save d in outFile"""),
    "numbers-xlsx": ("Numbers", "documents", IWORK_CLOSE, """set d to open inFile
export d to outFile as Microsoft Excel"""),
    "numbers-pdf": ("Numbers", "documents", IWORK_CLOSE, """set d to open inFile
export d to outFile as PDF with properties {image quality:Best}"""),
    "numbers-save": ("Numbers", "documents", IWORK_CLOSE, """set d to open inFile
save d in outFile"""),
    "key-pdf": ("Keynote", "documents", IWORK_CLOSE, """set d to open inFile
export d to outFile as PDF with properties {PDF image quality:Best, skipped slides:false}"""),
    "key-pptx": ("Keynote", "documents", IWORK_CLOSE, """set d to open inFile
export d to outFile as Microsoft PowerPoint"""),
    "key-save": ("Keynote", "documents", IWORK_CLOSE, """set d to open inFile
save d in outFile"""),
}

CONTAINERS = {
    "Microsoft Word": "com.microsoft.Word",
    "Microsoft PowerPoint": "com.microsoft.Powerpoint",
    "Pages": "com.apple.iWork.Pages",
    "Numbers": "com.apple.iWork.Numbers",
    "Keynote": "com.apple.iWork.Keynote",
}


def via_app(kind, src, out_ext):
    app, docs, close, body = SCRIPTS[kind]
    if not app_path(app):
        raise ConvertError(f"{app} is not installed")
    work = None
    base = Path.home() / "Library/Containers" / CONTAINERS[app] / "Data/tmp"
    try:
        if base.parent.exists():
            work = base / f"convert_{uuid.uuid4().hex[:10]}"
            work.mkdir(parents=True, exist_ok=True)
    except OSError:
        work = None
    if work is None:
        work = Path(tempfile.mkdtemp(prefix="convert_"))
    inp = work / src.name
    try:
        if src.is_dir():
            shutil.copytree(src, inp)
        else:
            shutil.copy2(src, inp)
    except OSError as e:
        shutil.rmtree(work, ignore_errors=True)
        raise ConvertError(f"cannot prepare the file: {e.strerror}")
    out = work / f"{src.stem}{out_ext}"
    script = OSA_TEMPLATE.replace("{app}", app).replace("{docs}", docs).replace("{close}", close).replace("{body}", body)
    return work, inp, out, script


def convert_with_app(kind, src, dst):
    work, inp, out, script = via_app(kind, src, dst.suffix)
    try:
        try:
            osa(script, inp, out, inp.name)
        except ConvertError as e:
            if "-609" not in str(e):
                raise
            time.sleep(2)
            osa(script, inp, out, inp.name)
        if not out.exists():
            raise ConvertError(f"{SCRIPTS[kind][0]} did not create the file")
        place(out, dst)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return dst


def convert_with_soffice(src, dst):
    exe = soffice_path()
    if not exe:
        raise ConvertError("LibreOffice is not installed")
    with tempfile.TemporaryDirectory() as td:
        r = run([exe, "--headless", "--norestore", "--convert-to", "pdf", "--outdir", td, str(src)])
        tmp = Path(td) / f"{src.stem}.pdf"
        if r.returncode != 0 or not tmp.exists():
            raise ConvertError("LibreOffice could not convert the file")
        place(tmp, dst)
    return dst


def chain(src, dst, steps):
    errors = []
    for name, fn in steps:
        try:
            fn(src, dst)
            return dst, name
        except ConvertError as e:
            errors.append(f"{name}: {e}")
    raise ConvertError("no app could convert the file:\n    " + "\n    ".join(errors))


TERMINAL = tuple('.!?:;")\u00bb\u201d\u2026')
BULLET_RE = re.compile(r"^\s*([\u2022\u25cf\u25aa\u25e6\u2013\u2014*\-]|\d{1,3}[.)]|[a-zа-я][.)])\s+")
HYPH_END = re.compile(r"\w[-\u00ad]$")
HYPH_HTML = re.compile(r"[-\u00ad]((?:</[a-z]+>)*)$")
BAD_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]")
PAGE_WORDS = re.compile(r"(страница|стр\.?|page|p\.|из|of|/|-|\u2013|\u2014|#|\s)")


def esc(t):
    return html.escape(BAD_XML.sub("", t), quote=True)


def norm_key(t):
    t = re.sub(r"\d+", "#", t.strip().lower())
    return re.sub(r"\s+", " ", t)


def is_page_number(key):
    if not PAGE_WORDS.sub("", key):
        return "#" in key
    return bool(re.fullmatch(r"[ivxlc]{1,6}", key))


def span_style(s):
    f = s["font"].lower()
    bold = bool(s["flags"] & 16) or any(w in f for w in ("bold", "black", "heavy", "semibold", "demi"))
    ital = bool(s["flags"] & 2) or "italic" in f or "oblique" in f
    sup = bool(s["flags"] & 1)
    return bold, ital, sup


def wrap_span(t, st):
    e = esc(t)
    if not t.strip():
        return e
    bold, ital, sup = st
    if sup:
        e = f"<sup>{e}</sup>"
    if ital:
        e = f"<i>{e}</i>"
    if bold:
        e = f"<b>{e}</b>"
    return e


def line_html(spans):
    out, cur, buf = [], None, ""
    for s in spans:
        st = span_style(s)
        if st != cur and buf:
            out.append(wrap_span(buf, cur))
            buf = ""
        cur = st
        buf += s["text"]
    if buf:
        out.append(wrap_span(buf, cur))
    return "".join(out)


def join_text(acc_t, acc_h, t, h):
    t, h = t.strip(), h.strip()
    if not acc_t:
        return t, h
    if HYPH_END.search(acc_t) and t[:1].islower():
        return acc_t[:-1] + t, HYPH_HTML.sub(r"\1", acc_h) + h
    return acc_t + " " + t, acc_h + " " + h


def size_key(s):
    return round(s * 2) / 2


def dominant_size(spans):
    c = Counter()
    for s in spans:
        c[size_key(s["size"])] += len(s["text"].strip())
    return c.most_common(1)[0][0] if c else 0


def in_margin(bbox, h):
    return bbox[3] < h * 0.09 or bbox[1] > h * 0.91


def norm_image(fz, data, ext):
    try:
        pix = fz.Pixmap(data)
        if pix.colorspace is None or pix.colorspace.n not in (1, 3):
            pix = fz.Pixmap(fz.csRGB, pix)
            data = None
        if ext.lower() in ("jpg", "jpeg") and not pix.alpha:
            return (data or pix.tobytes("jpg", jpg_quality=90)), "jpg"
        return pix.tobytes("png"), "png"
    except Exception:
        return None, None


def pdf_to_epub(src, dst_dir, stem, force):
    fz = load_fitz()
    doc = open_pdf(src)
    n = doc.page_count
    flags_text = fz.TEXT_PRESERVE_WHITESPACE | fz.TEXT_MEDIABOX_CLIP
    flags_img = flags_text | fz.TEXT_PRESERVE_IMAGES

    sizes, margin, cand = Counter(), Counter(), []
    lang_c, lang_l = 0, 0
    for page in doc:
        h = page.rect.height
        keys = set()
        for b in page.get_text("dict", flags=flags_text)["blocks"]:
            if b["type"] != 0:
                continue
            spans_all, chars = [], 0
            for l in b["lines"]:
                t = "".join(s["text"] for s in l["spans"]).strip()
                if not t:
                    continue
                spans_all += l["spans"]
                chars += len(t)
                for s in l["spans"]:
                    sizes[size_key(s["size"])] += len(s["text"].strip())
                if in_margin(l["bbox"], h):
                    keys.add(norm_key(t))
                if len(t) > 3:
                    lang_c += len(re.findall(r"[а-яёА-ЯЁ]", t))
                    lang_l += len(re.findall(r"[a-zA-Z]", t))
            if spans_all and chars <= 250 and len(b["lines"]) <= 4:
                cand.append(dominant_size(spans_all))
        for k in keys:
            margin[k] += 1

    total_chars = sum(sizes.values())
    body = sizes.most_common(1)[0][0] if sizes else 11
    thresh = max(3, int(n * 0.3)) if n >= 4 else n + 1
    repeated = {k for k, c in margin.items() if c >= thresh}
    hsizes = Counter(s for s in cand if s >= body * 1.15)
    common = sorted([s for s, c in hsizes.items() if c >= 2], reverse=True)[:3]
    if not common:
        common = sorted(hsizes.keys(), reverse=True)[:3]
    lang = "ru" if lang_c >= lang_l else "en"

    def heading_level(size):
        for i, cs in enumerate(common):
            if size >= cs - 0.25:
                return i + 1
        return min(len(common) + 1, 3)

    elems, images, img_pages = [], {}, {}
    scanned_pages = 0

    def add_para(text, h, pno, small):
        if not text:
            return
        if elems:
            last = elems[-1]
            if (last["kind"] == "p" and not small and not last["small"] and pno - last["page"] <= 1
                    and not last["text"].rstrip().endswith(TERMINAL) and text[:1].islower()):
                last["text"], last["html"] = join_text(last["text"], last["html"], text, h)
                return
        elems.append({"kind": "p", "text": text, "html": h, "page": pno, "small": small})

    def add_heading(text, lvl, pno):
        if elems:
            last = elems[-1]
            if last["kind"] == "h" and last["level"] == lvl and last["page"] == pno and len(last["text"]) + len(text) < 250:
                last["text"] += " " + text
                return
        elems.append({"kind": "h", "text": text, "level": lvl, "page": pno})

    def add_image(data, ext, pno, cls="img"):
        key = hashlib.md5(data).hexdigest()
        if key not in images:
            nd, ne = norm_image(fz, data, ext)
            if not nd:
                return
            images[key] = (nd, ne)
        img_pages.setdefault(key, set()).add(pno)
        elems.append({"kind": "img", "key": key, "page": pno, "cls": cls})

    for pno, page in enumerate(doc):
        if TTY:
            print(f"\r  analyzing page {pno + 1}/{n}", end="", flush=True)
        w, h = page.rect.width, page.rect.height
        blocks = page.get_text("dict", flags=flags_img)["blocks"]
        text_len = sum(len(s["text"].strip()) for b in blocks if b["type"] == 0 for l in b["lines"] for s in l["spans"])
        if text_len < 20:
            if any(b["type"] == 1 for b in blocks) or page.get_drawings():
                pix = page.get_pixmap(matrix=fz.Matrix(1.6, 1.6), alpha=False)
                add_image(pix.tobytes("jpg", jpg_quality=82), "jpg", pno, "page")
                scanned_pages += 1
            continue
        for b in blocks:
            if b["type"] == 1:
                x0, y0, x1, y1 = b["bbox"]
                if x1 - x0 < 40 or y1 - y0 < 40:
                    continue
                if (x1 - x0) * (y1 - y0) > w * h * 0.85:
                    continue
                if b.get("image"):
                    add_image(b["image"], b.get("ext", "png"), pno)
                continue
            lines = []
            for l in b["lines"]:
                spans = [s for s in l["spans"] if s["text"]]
                t = "".join(s["text"] for s in spans)
                if not t.strip():
                    continue
                if in_margin(l["bbox"], h):
                    k = norm_key(t)
                    if k in repeated or is_page_number(k):
                        continue
                lines.append((l["bbox"], t, spans))
            if not lines:
                continue
            all_spans = [s for _, _, sp in lines for s in sp]
            size = dominant_size(all_spans)
            full = " ".join(t.strip() for _, t, _ in lines)
            visible = [s for s in all_spans if s["text"].strip()]
            all_bold = bool(visible) and all(span_style(s)[0] for s in visible)
            if size >= body * 1.15 and len(full) <= 250 and len(lines) <= 4:
                add_heading(full, heading_level(size), pno)
                continue
            if (all_bold and len(full) <= 120 and len(lines) <= 2 and size >= body * 0.95
                    and not full.rstrip().endswith((".", ",", ";", ":")) and not BULLET_RE.match(full)):
                add_heading(full, min(len(common) + 1, 3), pno)
                continue
            bx0 = min(bb[0] for bb, _, _ in lines)
            bx1 = max(bb[2] for bb, _, _ in lines)
            bw = max(bx1 - bx0, 1)
            paras, cur, prev = [], [], None
            for bb, t, spans in lines:
                if prev is not None:
                    pbb, pt = prev
                    brk = False
                    if abs(bb[1] - pbb[1]) >= 2:
                        if bb[1] - pbb[3] > body * 0.8:
                            brk = True
                        elif BULLET_RE.match(t):
                            brk = True
                        elif bb[0] - bx0 > body * 0.9 and pbb[0] - bx0 < body * 0.5:
                            brk = True
                        elif pt.rstrip().endswith(TERMINAL) and pbb[2] < bx1 - bw * 0.2:
                            brk = True
                    if brk and cur:
                        paras.append(cur)
                        cur = []
                cur.append((t, spans))
                prev = (bb, t)
            if cur:
                paras.append(cur)
            for para in paras:
                pt, ph = "", ""
                for t, spans in para:
                    pt, ph = join_text(pt, ph, t, line_html(spans))
                psize = dominant_size([s for _, sp in para for s in sp])
                add_para(pt, ph, pno, psize < body * 0.85)
    if TTY:
        print("\r" + " " * 40 + "\r", end="", flush=True)

    deco = {k for k, ps in img_pages.items() if len(ps) > 3 and len(ps) > n * 0.3}
    elems = [e for e in elems if not (e["kind"] == "img" and e["key"] in deco)]
    if not elems:
        raise ConvertError("could not extract any content from the PDF")
    if scanned_pages == n:
        warn("the PDF has no text layer (scanned). Pages were stored as images, OCR is needed to get text")
    elif total_chars < 50:
        warn("the PDF contains very little text")

    meta = doc.metadata or {}
    title = (meta.get("title") or "").strip()
    if not title or re.match(r"(?i)^(microsoft word|untitled|без названия)", title) or title.lower().endswith((".doc", ".docx", ".pdf")):
        title = stem
    author = (meta.get("author") or "").strip()

    def first_idx(page0, title_text=None):
        cands = [i for i, e in enumerate(elems) if e["page"] >= page0]
        if not cands:
            return None
        if title_text:
            want = norm_key(title_text)[:40]
            for i in cands[:80]:
                e = elems[i]
                if e["page"] > page0 + 1:
                    break
                if e["kind"] == "h" and (norm_key(e["text"]).startswith(want[:20]) or want.startswith(norm_key(e["text"])[:20])):
                    return i
        return cands[0]

    nav, splits = [], set()
    toc = clean_toc(doc.get_toc(), n)
    if toc:
        for lvl, t, page in toc:
            idx = first_idx(page - 1, t)
            if idx is None:
                continue
            if lvl <= 3:
                nav.append((lvl, t, idx))
            if lvl == 1:
                splits.add(idx)
    else:
        heads = [(i, e) for i, e in enumerate(elems) if e["kind"] == "h"]
        if heads:
            levels = Counter(e["level"] for _, e in heads)
            top = min(levels)
            split_lvl = next((l for l in sorted(levels) if levels[l] >= 2), top)
            for i, e in heads:
                if e["level"] == split_lvl:
                    splits.add(i)
                if e["level"] <= split_lvl + 1:
                    nav.append((max(1, e["level"] - top + 1), e["text"], i))
        else:
            step = 20
            for a in range(0, n, step):
                idx = first_idx(a)
                if idx is None or (idx in splits):
                    continue
                splits.add(idx)
                nav.append((1, f"Pages {a + 1}-{min(a + step, n)}", idx))
    if not nav:
        nav = [(1, title, 0)]

    starts = sorted(splits | {0})
    chunks = [(starts[i], starts[i + 1] if i + 1 < len(starts) else len(elems)) for i in range(len(starts))]
    chunks = [c for c in chunks if c[1] > c[0]]
    starts = [c[0] for c in chunks]
    anchored = {idx for _, _, idx in nav}
    img_names = {}

    def chap_of(idx):
        return max(0, bisect_right(starts, idx) - 1)

    def href(idx):
        return f"text/ch{chap_of(idx) + 1:03d}.xhtml#e{idx}"

    def render(i, e):
        a = f' id="e{i}"' if i in anchored else ""
        if e["kind"] == "h":
            return f"<h{e['level']}{a}>{esc(e['text'])}</h{e['level']}>"
        if e["kind"] == "img":
            if e["key"] not in img_names:
                img_names[e["key"]] = f"img{len(img_names) + 1:04d}.{images[e['key']][1]}"
            return f'<div class="{e["cls"]}"{a}><img src="../images/{img_names[e["key"]]}" alt=""/></div>'
        cls = ' class="small"' if e["small"] else ""
        return f"<p{a}{cls}>{BAD_XML.sub('', e['html'])}</p>"

    def xhtml(t, body_html):
        return ('<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
                f'<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="{lang}" lang="{lang}">\n'
                f'<head>\n<meta charset="utf-8"/>\n<title>{esc(t)}</title>\n'
                '<link rel="stylesheet" type="text/css" href="../style.css"/>\n</head>\n'
                f"<body>\n{body_html}\n</body>\n</html>\n")

    chapters = []
    for ci, (a, b) in enumerate(chunks):
        heads = [elems[i]["text"] for i in range(a, b) if elems[i]["kind"] == "h"]
        body_html = "\n".join(render(i, elems[i]) for i in range(a, b))
        chapters.append((f"ch{ci + 1:03d}.xhtml", xhtml(heads[0] if heads else title, body_html)))

    tree = []
    root = {"c": tree}
    stack = [(0, root)]
    prev = 0
    for lvl, t, idx in nav:
        lvl = max(1, min(lvl, prev + 1))
        prev = lvl
        node = {"t": t, "h": href(idx), "c": []}
        while stack[-1][0] >= lvl:
            stack.pop()
        stack[-1][1]["c"].append(node)
        stack.append((lvl, node))

    def nav_ol(nodes, ind):
        pad = "  " * ind
        out = [f"{pad}<ol>"]
        for nd in nodes:
            inner = f'<a href="{esc(nd["h"])}">{esc(nd["t"])}</a>'
            if nd["c"]:
                out.append(f"{pad}  <li>{inner}\n{nav_ol(nd['c'], ind + 2)}\n{pad}  </li>")
            else:
                out.append(f"{pad}  <li>{inner}</li>")
        out.append(f"{pad}</ol>")
        return "\n".join(out)

    counter = [0]

    def ncx_points(nodes, ind):
        out = []
        for nd in nodes:
            counter[0] += 1
            pad = "  " * ind
            out.append(f'{pad}<navPoint id="np{counter[0]}" playOrder="{counter[0]}">'
                       f'<navLabel><text>{esc(nd["t"])}</text></navLabel><content src="{esc(nd["h"])}"/>')
            out += ncx_points(nd["c"], ind + 1)
            out.append(f"{pad}</navPoint>")
        return out

    nav_html = ('<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
                f'<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="{lang}" lang="{lang}">\n'
                f'<head><meta charset="utf-8"/><title>{esc(title)}</title></head>\n<body>\n'
                f'<nav epub:type="toc" id="toc"><h1>Contents</h1>\n{nav_ol(tree, 0)}\n</nav>\n</body>\n</html>\n')
    book_id = f"urn:uuid:{uuid.uuid4()}"
    ncx = ('<?xml version="1.0" encoding="utf-8"?>\n<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">\n'
           f'<head><meta name="dtb:uid" content="{book_id}"/></head>\n<docTitle><text>{esc(title)}</text></docTitle>\n<navMap>\n'
           + "\n".join(ncx_points(tree, 1)) + "\n</navMap>\n</ncx>\n")

    cover = doc[0].get_pixmap(matrix=fz.Matrix(1.5, 1.5), alpha=False).tobytes("jpg", jpg_quality=85)
    modified = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    mime = {"jpg": "image/jpeg", "png": "image/png"}
    manifest = ['<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
                '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
                '<item id="css" href="style.css" media-type="text/css"/>',
                '<item id="cover-img" href="images/cover.jpg" media-type="image/jpeg" properties="cover-image"/>']
    spine = []
    for i, (name, _) in enumerate(chapters):
        manifest.append(f'<item id="c{i + 1}" href="text/{name}" media-type="application/xhtml+xml"/>')
        spine.append(f'<itemref idref="c{i + 1}"/>')
    for key, name in img_names.items():
        manifest.append(f'<item id="i{name.split(".")[0]}" href="images/{name}" media-type="{mime[images[key][1]]}"/>')
    creator = f"\n<dc:creator>{esc(author)}</dc:creator>" if author else ""
    opf = ('<?xml version="1.0" encoding="utf-8"?>\n'
           f'<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid" xml:lang="{lang}">\n'
           '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
           f'<dc:identifier id="bookid">{book_id}</dc:identifier>\n<dc:title>{esc(title)}</dc:title>\n'
           f'<dc:language>{lang}</dc:language>{creator}\n<meta property="dcterms:modified">{modified}</meta>\n'
           '<meta name="cover" content="cover-img"/>\n</metadata>\n<manifest>\n' + "\n".join(manifest)
           + '\n</manifest>\n<spine toc="ncx">\n' + "\n".join(spine) + "\n</spine>\n</package>\n")
    css = """body { margin: 0 4%; line-height: 1.45; }
p { margin: 0 0 0.35em 0; text-indent: 1.2em; text-align: justify; }
p.small { font-size: 0.85em; text-indent: 0; }
h1, h2, h3 { text-align: left; line-height: 1.25; margin: 1.2em 0 0.6em 0; page-break-after: avoid; }
h1 { font-size: 1.6em; }
h2 { font-size: 1.3em; }
h3 { font-size: 1.1em; }
div.img { text-align: center; margin: 0.8em 0; }
div.img img { max-width: 100%; height: auto; }
div.page { text-align: center; margin: 0; page-break-after: always; }
div.page img { max-width: 100%; max-height: 100%; }
"""
    container = ('<?xml version="1.0" encoding="utf-8"?>\n'
                 '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
                 '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>\n'
                 "</container>\n")

    dst = target(dst_dir, stem, ".epub", force)
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td) / "out.epub"
        with zipfile.ZipFile(tmp, "w") as z:
            z.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip", compress_type=zipfile.ZIP_STORED)
            zw = lambda name, data: z.writestr(name, data, compress_type=zipfile.ZIP_DEFLATED)
            zw("META-INF/container.xml", container)
            zw("OEBPS/content.opf", opf)
            zw("OEBPS/nav.xhtml", nav_html)
            zw("OEBPS/toc.ncx", ncx)
            zw("OEBPS/style.css", css)
            zw("OEBPS/images/cover.jpg", cover)
            for name, data in chapters:
                zw(f"OEBPS/text/{name}", data)
            for key, name in img_names.items():
                zw(f"OEBPS/images/{name}", images[key][0])
        place(tmp, dst)
    return dst, len(chapters), len(img_names)


APP_MODES = {
    "word-pages": ("pages-save", ".pages"),
    "pages-pdf": ("pages-pdf", ".pdf"),
    "pages-word": ("pages-docx", ".docx"),
    "numbers-xlsx": ("numbers-xlsx", ".xlsx"),
    "numbers-pdf": ("numbers-pdf", ".pdf"),
    "xlsx-numbers": ("numbers-save", ".numbers"),
    "key-pdf": ("key-pdf", ".pdf"),
    "key-pptx": ("key-pptx", ".pptx"),
    "pptx-key": ("key-save", ".key"),
}

SIPS_MODES = {
    "heic-png": "png", "heic-jpg": "jpeg", "png-heic": "heic", "jpg-heic": "heic",
    "png-jpg": "jpeg", "jpg-png": "png", "webp-png": "png", "webp-jpg": "jpeg",
}


def convert_one(mode, src, out_dir, stem, pages, force):
    if mode in SIPS_MODES:
        return image_convert(src, out_dir, stem, SIPS_MODES[mode], force)
    if mode in PAGE_MODES:
        folder, n = pdf_to_images(src, out_dir, stem, mode.split("-")[1], pages, force)
        return f"{show(folder)}/ ({n} pages)"
    if mode == "pdf-txt":
        dst, n = pdf_to_text(src, out_dir, stem, force)
        return f"{show(dst)} ({n} pages)"
    if mode == "epub-pdf":
        dst, n = epub_to_pdf(src, out_dir, stem, force)
        return f"{show(dst)} ({n} pages)"
    if mode == "pdf-epub":
        dst, ch, im = pdf_to_epub(src, out_dir, stem, force)
        return f"{show(dst)} (chapters: {ch}, images: {im})"
    if mode == "pdf-word":
        return pdf_to_word(src, out_dir, stem, force)
    if mode == "mov-mp4":
        return video_to_mp4(src, out_dir, stem, force)
    if mode == "word-pdf":
        dst, via = chain(src, target(out_dir, stem, ".pdf", force), [
            ("Microsoft Word", lambda s, d: convert_with_app("word-pdf", s, d)),
            ("Pages", lambda s, d: convert_with_app("pages-pdf", s, d)),
            ("LibreOffice", convert_with_soffice),
        ])
        return f"{show(dst)} (via {via})"
    if mode == "pptx-pdf":
        dst, via = chain(src, target(out_dir, stem, ".pdf", force), [
            ("Microsoft PowerPoint", lambda s, d: convert_with_app("ppt-pdf", s, d)),
            ("Keynote", lambda s, d: convert_with_app("key-pdf", s, d)),
            ("LibreOffice", convert_with_soffice),
        ])
        return f"{show(dst)} (via {via})"
    if mode in APP_MODES:
        kind, ext = APP_MODES[mode]
        return convert_with_app(kind, src, target(out_dir, stem, ext, force))
    raise ConvertError(f"mode {mode} is not implemented")


def fail(label, e):
    print(color(f"  error ({label}): ", "31") + str(e), file=sys.stderr)


def main(argv):
    try:
        mode, files, pages, out_dir, name, force = parse_args(argv)
        out = prepare_out_dir(out_dir)
    except UsageError as e:
        print(color("Error: ", "31") + str(e), file=sys.stderr)
        print("Help: convert -h", file=sys.stderr)
        return 2
    if mode == "img-pdf":
        srcs, bad = [], 0
        for f in files:
            try:
                srcs.append(check_source(f, mode))
            except ConvertError as e:
                fail(f, e)
                bad += 1
        if not srcs:
            return 1
        stem = name or (srcs[0].stem if len(srcs) == 1 else f"{srcs[0].stem}_combined")
        try:
            info(f"{len(srcs)} image(s) -> pdf ...")
            dst, n = images_to_pdf(srcs, out, stem, force)
            info(color("  done: ", "32") + f"{show(dst)} ({n} pages)")
        except ConvertError as e:
            fail("img-pdf", e)
            return 1
        except KeyboardInterrupt:
            print("\nInterrupted", file=sys.stderr)
            return 130
        return 0 if bad == 0 else 1
    ok, failed = 0, 0
    total = len(files)
    for i, f in enumerate(files, 1):
        prefix = f"[{i}/{total}] " if total > 1 else ""
        try:
            src = check_source(f, mode)
            info(f"{prefix}{show(src)} -> {mode.split('-')[1]} ...")
            res = convert_one(mode, src, out, name or src.stem, pages, force)
            info(color("  done: ", "32") + (res if isinstance(res, str) else show(res)))
            ok += 1
        except ConvertError as e:
            fail(f, e)
            failed += 1
        except KeyboardInterrupt:
            print("\nInterrupted", file=sys.stderr)
            return 130
        except Exception as e:
            fail(f, f"unexpected {type(e).__name__}: {e}")
            failed += 1
    if total > 1:
        info(f"Total: {ok} converted, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
