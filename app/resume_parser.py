"""Bounded local text extraction; run as a child process for untrusted documents."""
import io
import json
import sys
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

MAX_BYTES = 2 * 1024 * 1024
MAX_TEXT = 30000


class ResumeParseError(ValueError):
    pass


def extract_text(filename, data):
    if not data or len(data) > MAX_BYTES:
        raise ResumeParseError("文件不能为空，最大 2 MiB。")
    suffix = Path(filename).suffix.lower()
    if suffix == ".txt":
        text = data.decode("utf-8-sig")
    elif suffix == ".docx":
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) > 200 or sum(item.file_size for item in entries) > 8 * MAX_BYTES:
                raise ResumeParseError("文档展开后过大，请精简或粘贴正文。")
            xml = archive.read("word/document.xml")
            if len(xml) > 2 * MAX_BYTES or b"<!DOCTYPE" in xml.upper() or b"<!ENTITY" in xml.upper():
                raise ResumeParseError("文档格式不支持，请导出为 PDF 或 TXT。")
            root = ET.fromstring(xml)
            ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
            text = "\n".join("".join(node.text or "" for node in para.iter(ns + "t")) for para in root.iter(ns + "p"))
    elif suffix == ".pdf":
        from pypdf import PdfReader
        if not data.startswith(b"%PDF-"):
            raise ResumeParseError("这不是有效 PDF。")
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise ResumeParseError("请上传未加密的 PDF。")
        if len(reader.pages) > 20:
            raise ResumeParseError("简历最多 20 页，请精简后再上传。")
        parts = []
        for page in reader.pages:
            stream = page.get_contents()
            if stream is not None and len(stream.get_data()) > 4 * MAX_BYTES:
                raise ResumeParseError("PDF 页面过于复杂，请导出简化版或粘贴正文。")
            parts.append(page.extract_text() or "")
            if sum(map(len, parts)) > MAX_TEXT:
                raise ResumeParseError("简历正文最多 30000 字，请精简后再上传。")
        text = "\n".join(parts)
    else:
        raise ResumeParseError("支持 PDF、DOCX 和 UTF-8 TXT；也可直接粘贴正文。")
    text = "\n".join(line.strip() for line in text.replace("\x00", "").splitlines()).strip()
    if len(text) < 20:
        raise ResumeParseError("未提取到足够文字。扫描版 PDF 请改用可选中文字的版本，或直接粘贴正文。")
    if len(text) > MAX_TEXT:
        raise ResumeParseError("简历正文最多 30000 字，请精简后再上传。")
    return text


if __name__ == "__main__":
    import logging
    import resource
    logging.disable(logging.CRITICAL)
    resource.setrlimit(resource.RLIMIT_CPU, (8, 8))
    if sys.platform != "darwin":
        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
    try:
        print(json.dumps({"text": extract_text(sys.argv[1], sys.stdin.buffer.read(MAX_BYTES + 1))}))
    except Exception as error:
        # Parser exceptions may contain document text. Only our fixed messages leave the child.
        message = str(error) if isinstance(error, ResumeParseError) else "无法读取此文档，请导出为 PDF/TXT 或直接粘贴正文。"
        print(json.dumps({"error": message}))
