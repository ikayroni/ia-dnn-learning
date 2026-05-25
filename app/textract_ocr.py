from __future__ import annotations

import time
import uuid
from typing import Optional

import boto3
from botocore.exceptions import ClientError

from app.config import settings
from app.pdf_extractor import ExtractedDocument
from app.pdf_utils import prepare_pdf_for_textract, safe_pdf_filename


def _s3_client():
    return boto3.client("s3", region_name=settings.aws_region)


def _textract_client():
    return boto3.client("textract", region_name=settings.aws_region)


def _require_bucket() -> str:
    if not settings.s3_bucket.strip():
        raise RuntimeError(
            "Defina S3_BUCKET no .env. Textract assíncrono exige PDF no S3. "
            "Crie um bucket na mesma região do AWS_REGION."
        )
    return settings.s3_bucket.strip()


def upload_pdf_for_ocr(data: bytes, filename: str) -> str:
    bucket = _require_bucket()
    safe_name = safe_pdf_filename(filename)
    key = f"{settings.s3_prefix.rstrip('/')}/{uuid.uuid4().hex}_{safe_name}"
    _s3_client().put_object(
        Bucket=bucket,
        Key=key,
        Body=data,
        ContentType="application/pdf",
    )
    return key


def start_document_text_detection(s3_key: str) -> str:
    bucket = _require_bucket()
    try:
        resp = _textract_client().start_document_text_detection(
            DocumentLocation={"S3Object": {"Bucket": bucket, "Name": s3_key}}
        )
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        msg = e.response.get("Error", {}).get("Message", str(e))
        if code in ("AccessDenied", "AccessDeniedException"):
            raise RuntimeError(
                "Sem permissão para Textract/S3. Use credenciais IAM com "
                "textract:StartDocumentTextDetection, textract:GetDocumentTextDetection, "
                "s3:PutObject e s3:GetObject no bucket configurado."
            ) from e
        if "get object metadata" in msg.lower() or "invalid s3" in msg.lower():
            raise RuntimeError(
                f"Erro S3/Textract: {msg}. "
                f"Confira AWS_REGION ({settings.aws_region}) = região do bucket; "
                f"policy do bucket para textract.amazonaws.com (README)."
            ) from e
        raise RuntimeError(f"Erro ao iniciar OCR Textract: {msg}") from e
    return resp["JobId"]


def wait_for_textract_job(
    job_id: str,
    *,
    poll_seconds: Optional[int] = None,
    on_status=None,
) -> None:
    poll = poll_seconds or settings.textract_poll_seconds
    client = _textract_client()
    while True:
        resp = client.get_document_text_detection(JobId=job_id, MaxResults=1)
        status = resp["JobStatus"]
        if on_status:
            on_status(status)
        if status == "SUCCEEDED":
            return
        if status == "FAILED":
            msg = resp.get("StatusMessage", "OCR falhou no Textract.")
            if "INVALID_DOCUMENT_TYPE" in msg:
                raise RuntimeError(
                    f"{msg} — O Textract rejeitou o formato do PDF. "
                    "O sistema tentará reprocessar em modo compatível automaticamente "
                    "na próxima execução; se persistir, exporte o PDF novamente "
                    "(Imprimir → PDF) ou teste com menos páginas."
                )
            raise RuntimeError(msg)
        time.sleep(poll)


def fetch_textract_text(job_id: str) -> ExtractedDocument:
    client = _textract_client()
    pages_lines: dict[int, list[str]] = {}
    next_token = None

    while True:
        kwargs = {"JobId": job_id}
        if next_token:
            kwargs["NextToken"] = next_token
        resp = client.get_document_text_detection(**kwargs)

        for block in resp.get("Blocks", []):
            if block.get("BlockType") == "LINE" and block.get("Text"):
                page = int(block.get("Page", 1))
                pages_lines.setdefault(page, []).append(block["Text"])

        next_token = resp.get("NextToken")
        if not next_token:
            break

    if not pages_lines:
        raise ValueError("Textract não retornou texto. Verifique se o PDF tem páginas legíveis.")

    parts: list[str] = []
    markers: list[tuple[int, int]] = []
    offset = 0
    for page_num in sorted(pages_lines.keys()):
        page_text = "\n".join(pages_lines[page_num]).strip()
        markers.append((offset, page_num))
        if page_text:
            parts.append(page_text)
            offset += len(page_text) + 2

    full_text = "\n\n".join(parts)
    return ExtractedDocument(
        text=full_text,
        page_count=len(pages_lines),
        page_markers=markers,
    )


def _run_once(pdf_bytes: bytes, filename: str, on_status=None) -> tuple[ExtractedDocument, str, str]:
    """Uma tentativa: upload + Textract. Retorna doc, s3_key, textract_job_id."""
    if on_status:
        on_status("uploading_s3")
    s3_key = upload_pdf_for_ocr(pdf_bytes, filename)

    if on_status:
        on_status("starting_textract")
    textract_job_id = start_document_text_detection(s3_key)

    if on_status:
        on_status("waiting_textract")

    def _status(s):
        if on_status:
            on_status(f"textract_{s.lower()}")

    wait_for_textract_job(textract_job_id, on_status=_status)

    if on_status:
        on_status("fetching_results")
    doc = fetch_textract_text(textract_job_id)
    return doc, s3_key, textract_job_id


def run_textract_ocr_pipeline(
    pdf_bytes: bytes,
    filename: str,
    *,
    on_status=None,
) -> ExtractedDocument:
    """
    Upload S3 → Textract assíncrono → texto por página.
    Tenta PDF normalizado; se INVALID_DOCUMENT_TYPE, repete rasterizando páginas.
    """
    import fitz

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    page_count = len(doc)
    doc.close()

    prepared, mode = prepare_pdf_for_textract(pdf_bytes, force_rasterize=False)
    if on_status:
        on_status(f"prepare_{mode}")

    try:
        doc, _, textract_id = _run_once(prepared, filename, on_status=on_status)
        return doc, textract_id
    except RuntimeError as e:
        err = str(e)
        if "INVALID_DOCUMENT_TYPE" not in err and "UnsupportedDocument" not in err:
            raise
        if page_count > 80:
            raise RuntimeError(
                f"{err} Para PDFs com mais de 80 páginas, use pagina_inicio/pagina_fim "
                "para testar um trecho menor antes do livro inteiro."
            ) from e
        if on_status:
            on_status("retry_rasterize")
        prepared2, _ = prepare_pdf_for_textract(pdf_bytes, force_rasterize=True)
        doc, _, textract_id = _run_once(prepared2, filename, on_status=on_status)
        return doc, textract_id
