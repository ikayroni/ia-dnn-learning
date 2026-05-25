from __future__ import annotations

from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import BASE_DIR, settings
from app.generator import (
    discover_topics_from_ocr_job,
    generate_from_ocr_job,
    generate_from_pdf_bytes,
    generate_from_text,
)
from app.ocr_jobs import get_job, start_ocr_job
from app.schemas import (
    GerarResponse,
    GerarTextoRequest,
    OcrJobCreated,
    OcrJobStatus,
    TemasResponse,
)
from app.storage import (
    delete_documento,
    export_geracao_csv,
    get_documento,
    get_geracao_with_questoes,
    list_documentos,
)
from fastapi.responses import PlainTextResponse, Response

app = FastAPI(
    title="Gerador de Questões",
    description="Gera questões a partir de texto ou PDF (OCR Textract para scans)",
    version="0.2.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

MAX_BYTES = settings.max_pdf_upload_mb * 1024 * 1024

STATIC_DIR = BASE_DIR / "static"
if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def frontend():
    index = STATIC_DIR / "index.html"
    if index.is_file():
        return FileResponse(index)
    return {"message": "Frontend em static/index.html não encontrado."}


async def _read_pdf_upload(arquivo: UploadFile) -> bytes:
    if not arquivo.filename or not arquivo.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Envie um arquivo .pdf")
    data = await arquivo.read()
    if len(data) > MAX_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"PDF maior que {settings.max_pdf_upload_mb} MB",
        )
    if len(data) == 0:
        raise HTTPException(status_code=400, detail="Arquivo vazio")
    return data


def _parse_tipos(tipos: str) -> list[str]:
    tipo_list = [t.strip() for t in tipos.split(",") if t.strip()]
    valid = {"multipla_escolha", "verdadeiro_falso", "dissertativa"}
    for t in tipo_list:
        if t not in valid:
            raise HTTPException(status_code=400, detail=f"Tipo inválido: {t}")
    return tipo_list


def _parse_keywords(palavras_chave: Optional[str]) -> Optional[list[str]]:
    if not palavras_chave or not palavras_chave.strip():
        return None
    return [k.strip() for k in palavras_chave.split(",") if k.strip()]


def _bucket_region() -> Optional[str]:
    if not settings.s3_bucket.strip():
        return None
    try:
        import boto3

        s3 = boto3.client("s3", region_name=settings.aws_region)
        loc = s3.get_bucket_location(Bucket=settings.s3_bucket.strip())
        # us-east-1 retorna LocationConstraint None
        return loc.get("LocationConstraint") or "us-east-1"
    except Exception:
        return None


@app.get("/health")
def health():
    bucket = settings.s3_bucket.strip()
    bucket_region = _bucket_region() if bucket else None
    regions_ok = (
        bucket_region is None
        or bucket_region == settings.aws_region
    )
    return {
        "status": "ok" if regions_ok else "config_error",
        "aws_region": settings.aws_region,
        "s3_bucket": bucket or None,
        "s3_bucket_region": bucket_region,
        "regions_match": regions_ok,
        "s3_bucket_configured": bool(bucket),
        "max_pdf_mb": settings.max_pdf_upload_mb,
        "hint": (
            None
            if regions_ok
            else (
                f"AWS_REGION ({settings.aws_region}) difere do bucket ({bucket_region}). "
                "Ajuste o .env e reinicie: Ctrl+C → python run.py"
            )
        ),
    }


@app.post("/ocr/pdf", response_model=OcrJobCreated)
async def iniciar_ocr_pdf(
    arquivo: UploadFile = File(..., description="PDF escaneado ou digital"),
    pagina_inicio: Optional[int] = Form(
        default=None,
        ge=1,
        description="Página inicial (1-based). Use para testar sem processar as 1800 páginas.",
    ),
    pagina_fim: Optional[int] = Form(
        default=None,
        ge=1,
        description="Página final (inclusiva)",
    ),
):
    """
    OCR assíncrono com Amazon Textract (ideal para livros longos).

    1. Envie o PDF aqui → recebe `job_id`
    2. Consulte GET /ocr/jobs/{job_id} até `status=succeeded`
    3. Gere questões com POST /gerar/ocr-job/{job_id}
    """
    data = await _read_pdf_upload(arquivo)

    try:
        job_id, reused = start_ocr_job(
            data,
            arquivo.filename or "documento.pdf",
            pagina_inicio=pagina_inicio,
            pagina_fim=pagina_fim,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e

    job = get_job(job_id)
    if reused:
        msg = "PDF idêntico já tem OCR — reaproveitando (sem custo Textract)."
        status = job.get("status", "succeeded") if job else "succeeded"
    else:
        msg = (
            "OCR iniciado em background. Livros grandes podem levar 30–90 minutos. "
            "Use pagina_inicio/pagina_fim para testar com poucas páginas."
        )
        status = "pending"
    return OcrJobCreated(
        job_id=job_id,
        status=status,
        message=msg,
        pagina_inicio=pagina_inicio,
        pagina_fim=pagina_fim,
        total_paginas_arquivo=job.get("total_paginas_arquivo") if job else None,
        poll_url=f"/ocr/jobs/{job_id}",
    )


@app.get("/ocr/jobs/{job_id}", response_model=OcrJobStatus)
def status_ocr(job_id: str):
    data = get_job(job_id)
    if not data:
        raise HTTPException(status_code=404, detail="Job não encontrado")
    gerar_url = None
    if data.get("status") == "succeeded":
        gerar_url = f"/gerar/ocr-job/{job_id}"
    return OcrJobStatus(
        job_id=data["job_id"],
        status=data["status"],
        phase=data.get("phase"),
        filename=data.get("filename"),
        pagina_inicio=data.get("pagina_inicio"),
        pagina_fim=data.get("pagina_fim"),
        total_paginas_arquivo=data.get("total_paginas_arquivo"),
        paginas_ocr=data.get("paginas_ocr"),
        caracteres=data.get("caracteres"),
        error=data.get("error"),
        created_at=data.get("created_at"),
        updated_at=data.get("updated_at"),
        gerar_questoes_url=gerar_url,
    )


@app.post("/gerar/texto", response_model=GerarResponse)
def gerar_texto(body: GerarTextoRequest):
    try:
        questoes, meta = generate_from_text(
            body.texto,
            num_questoes_por_chunk=body.num_questoes_por_chunk,
            tipos=list(body.tipos),
            dificuldade=body.dificuldade,
            max_chunks=body.max_chunks,
            tema=body.tema,
            palavras_chave=body.palavras_chave,
            pagina_inicio=body.pagina_inicio,
            pagina_fim=body.pagina_fim,
            instrucoes_extras=body.instrucoes_extras,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e

    return GerarResponse(questoes=questoes, meta=meta)


@app.post("/gerar/pdf", response_model=GerarResponse)
async def gerar_pdf(
    arquivo: UploadFile = File(..., description="Arquivo PDF"),
    num_questoes_por_chunk: int = Form(default=2, ge=1, le=10),
    tipos: str = Form(default="multipla_escolha"),
    dificuldade: Optional[str] = Form(default=None),
    max_chunks: Optional[int] = Form(default=None, ge=1, le=50),
    tema: Optional[str] = Form(default=None),
    palavras_chave: Optional[str] = Form(default=None, description="Lista separada por vírgula"),
    pagina_inicio: Optional[int] = Form(default=None, ge=1),
    pagina_fim: Optional[int] = Form(default=None, ge=1),
    instrucoes_extras: Optional[str] = Form(default=None),
):
    data = await _read_pdf_upload(arquivo)
    tipo_list = _parse_tipos(tipos)
    keywords = _parse_keywords(palavras_chave)

    try:
        questoes, meta = generate_from_pdf_bytes(
            data,
            num_questoes_por_chunk=num_questoes_por_chunk,
            tipos=tipo_list,
            dificuldade=dificuldade,
            max_chunks=max_chunks,
            tema=tema,
            palavras_chave=keywords,
            pagina_inicio=pagina_inicio,
            pagina_fim=pagina_fim,
            instrucoes_extras=instrucoes_extras,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e

    return GerarResponse(questoes=questoes, meta=meta)


@app.post("/gerar/ocr-job/{job_id}", response_model=GerarResponse)
def gerar_de_ocr_job(
    job_id: str,
    num_questoes_por_chunk: int = 2,
    tipos: str = "multipla_escolha",
    dificuldade: Optional[str] = None,
    max_chunks: Optional[int] = None,
    tema: Optional[str] = None,
    palavras_chave: Optional[str] = None,
    pagina_inicio: Optional[int] = None,
    pagina_fim: Optional[int] = None,
    instrucoes_extras: Optional[str] = None,
):
    """Gera questões a partir do texto salvo por um job OCR concluído."""
    tipo_list = _parse_tipos(tipos)
    keywords = _parse_keywords(palavras_chave)
    try:
        questoes, meta = generate_from_ocr_job(
            job_id,
            num_questoes_por_chunk=num_questoes_por_chunk,
            tipos=tipo_list,
            dificuldade=dificuldade,
            max_chunks=max_chunks,
            tema=tema,
            palavras_chave=keywords,
            pagina_inicio=pagina_inicio,
            pagina_fim=pagina_fim,
            instrucoes_extras=instrucoes_extras,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Job OCR não encontrado") from None
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e

    return GerarResponse(questoes=questoes, meta=meta)


@app.get("/historico/documentos")
def historico_documentos(limit: int = 50):
    if limit < 1 or limit > 200:
        raise HTTPException(status_code=400, detail="limit entre 1 e 200")
    return {"documentos": list_documentos(limit=limit)}


@app.get("/historico/documentos/{documento_id}")
def historico_documento_detalhe(documento_id: int):
    doc = get_documento(documento_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Documento não encontrado")
    return doc


@app.delete("/historico/documentos/{documento_id}")
def historico_documento_delete(documento_id: int):
    ok = delete_documento(documento_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Documento não encontrado")
    return {"deleted": True}


@app.get("/historico/geracoes/{geracao_id}")
def historico_geracao(geracao_id: int):
    g = get_geracao_with_questoes(geracao_id)
    if not g:
        raise HTTPException(status_code=404, detail="Geração não encontrada")
    return g


@app.get("/historico/geracoes/{geracao_id}/csv", response_class=PlainTextResponse)
def historico_geracao_csv(geracao_id: int):
    csv_text = export_geracao_csv(geracao_id)
    if csv_text is None:
        raise HTTPException(status_code=404, detail="Geração não encontrada")
    return Response(
        content=csv_text,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="questoes_{geracao_id}.csv"'},
    )


@app.get("/temas/ocr-job/{job_id}", response_model=TemasResponse)
def descobrir_temas_ocr(job_id: str, max_topics: int = 10):
    """Pede ao LLM os principais temas do material OCR para o usuário escolher."""
    if max_topics < 3 or max_topics > 20:
        raise HTTPException(status_code=400, detail="max_topics entre 3 e 20")
    try:
        info = discover_topics_from_ocr_job(job_id, max_topics=max_topics)
    except KeyError:
        raise HTTPException(status_code=404, detail="Job OCR não encontrado") from None
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    return TemasResponse(**info)
