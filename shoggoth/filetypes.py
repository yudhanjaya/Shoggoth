"""What Shoggoth can do with a file someone attaches in a chat: extension -> handler.

The handlers:
  text       read directly (text, code, CSV, JSON, Markdown, HTML and the like)
  image      passed to a vision model as image content
  pdf-text   read with `pdftotext -layout` when poppler is installed
  pdf-scan   pages rendered to images with `pdftoppm`, for a vision model
  sheet      needs a tool that isn't installed yet; a Pi package provides it later
  docx       the same, for Word documents

An attachment is written as it came and never run. The handler only says how the chat will read it.
"""
import os
import re
import shutil

import store

MAX_BYTES = 50 * 1024 * 1024        # one attachment
MAX_NAME = 120                      # bytes in a file name

TEXT = {
    "txt", "text", "md", "markdown", "rst", "org", "csv", "tsv", "json", "jsonl", "ndjson", "yaml", "yml", "toml",
    "ini", "cfg", "conf", "log", "html", "htm", "xml", "css", "scss", "less", "js", "mjs", "cjs", "jsx", "ts", "tsx",
    "vue", "svelte", "py", "rb", "go", "rs", "java", "kt", "kts", "c", "h", "cc", "cpp", "hpp", "cs", "php", "sh",
    "bash", "zsh", "fish", "sql", "r", "lua", "pl", "swift", "scala", "tex", "diff", "patch", "env",
}
IMAGE = {"png", "jpg", "jpeg", "gif", "webp", "bmp", "tif", "tiff", "avif", "heic"}
PDF = {"pdf"}
SHEET = {"xlsx", "xls", "ods"}
DOCX = {"docx", "doc"}

HANDLERS = {
    "text": {"label": "Read as text"},
    "image": {"label": "Image"},
    "pdf-text": {"label": "PDF, read as text", "tool": "pdftotext", "missing": "PDF, needs pdftotext"},
    "pdf-scan": {"label": "PDF, pages as images", "tool": "pdftoppm", "missing": "PDF, needs pdftoppm"},
    "sheet": {"label": "Spreadsheet, needs a tool that isn't installed"},
    "docx": {"label": "Word document, needs a tool that isn't installed"},
    "unsupported": {"label": "Shoggoth can't open this file type yet"},
}
NEEDS_A_PACKAGE = {"sheet", "docx", "unsupported"}

BAD_CHARS = re.compile("[\u0000-\u001F\u007F-\u009F\u200E\u200F\u202A-\u202E\u2066-\u2069]")
SAFE_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")


def installed(tool):
    """Whether a command-line tool is on PATH. It is the only thing that makes pdf-text or pdf-scan possible."""
    return bool(shutil.which(tool))


def extension(name):
    """The lower-case extension without its dot, or "" when the name has none (README, Makefile, .env)."""
    base = os.path.basename(str(name or "").replace("\\", "/"))
    i = base.rfind(".")
    return base[i + 1:].lower() if i > 0 else ""


def handler_for(name):
    """The handler Shoggoth will use for a file with this name."""
    ext = extension(name)
    if ext in IMAGE:
        return "image"
    if ext in PDF:
        if installed("pdftotext"):
            return "pdf-text"
        return "pdf-scan" if installed("pdftoppm") else "pdf-text"
    if ext in SHEET:
        return "sheet"
    if ext in DOCX:
        return "docx"
    if not ext or ext in TEXT:
        return "text"
    return "unsupported"


def describe(name):
    """{handler, label, available, needs, ext}: the handler, the words the composer shows, and whether it can be used now."""
    h = handler_for(name)
    info = HANDLERS[h]
    tool = info.get("tool")
    available = h not in NEEDS_A_PACKAGE and (not tool or installed(tool))
    label = info["label"]
    if not available and info.get("missing"):
        label = info["missing"]
    return {"handler": h, "label": label, "available": available, "needs": tool, "ext": extension(name)}


def clean_name(name):
    """A file name Shoggoth will write, or ValueError. Folder parts are refused rather than trimmed, so nothing lands outside the
    attachments folder; control and direction-changing characters are refused because they make a name read as another."""
    if not isinstance(name, str):
        raise ValueError("Give the file a name.")
    name = name.strip()
    if not name:
        raise ValueError("Give the file a name.")
    if "/" in name or "\\" in name:
        raise ValueError("A file's name can't include a folder.")
    if name in (".", ".."):
        raise ValueError("That isn't a file name.")
    if BAD_CHARS.search(name):
        raise ValueError("That file's name has characters Shoggoth won't write.")
    if len(name.encode("utf8")) > MAX_NAME:
        raise ValueError("That file's name is longer than %d bytes." % MAX_NAME)
    return name


def save(folder, name, data):
    """Write one attachment into folder (made if needed). A name already in use gets -1, -2 and so on. Returns the full path.

    The bytes are written as they came. Nothing in them is read, parsed or run."""
    name = clean_name(name)
    data = bytes(data or b"")
    if len(data) > MAX_BYTES:
        raise ValueError("That file is %d MB. Attach one up to %d MB." % (_mb(len(data)), _mb(MAX_BYTES)))
    os.makedirs(folder, exist_ok=True)
    base, ext = os.path.splitext(name)
    for i in range(1000):
        path = os.path.join(folder, name if i == 0 else "%s-%d%s" % (base, i, ext))
        try:
            with open(path, "xb") as f:
                f.write(data)
            return path
        except FileExistsError:
            continue
    raise ValueError("There are too many files called %s in this chat." % name)


def save_for_chat(task, name, data):
    """Save one attachment for a chat: under attachments/ in the chat's own folder, or in Shoggoth's per-chat scratch folder when
    the chat has no folder of its own. Returns {path, handler, label} for the composer."""
    if not task or not task.get("id"):
        raise ValueError("Choose a chat first.")
    if task.get("kind") == "job":
        raise ValueError("Attachments go in a chat, not a background job.")
    sid = str(task.get("id"))
    if not SAFE_ID.fullmatch(sid):
        raise ValueError("That chat id isn't one Shoggoth made.")
    d = describe(name)
    folder = os.path.join(store.data_dir(), "cowork", "scratch", sid, "attachments") if task.get("home") \
        else os.path.join(task["cwd"], "attachments")
    return {"path": save(folder, name, data), "handler": d["handler"], "label": d["label"]}


def _mb(n):
    return max(1, (n + 1024 * 1024 - 1) // (1024 * 1024))
