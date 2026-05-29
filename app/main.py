from __future__ import annotations

from typing import Optional

import app as app_pkg

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import BASE_DIR, settings
from app.generator import (
    discover_topics_from_ocr_job,
    generate_from_documento_id,
    generate_from_ocr_job,
    generate_from_pdf_bytes,
    generate_from_text,
)
from app.ocr_jobs import (
    cleanup_orphan_jobs,
    get_job,
    has_active_thread,
    list_jobs,
    request_cancel,
    start_ocr_job,
)
from app.schemas import (
    AtividadeStatusUpdate,
    EtapaStatusUpdate,
    GerarResponse,
    GerarTextoRequest,
    OcrJobCreated,
    OcrJobStatus,
    OcrJobsList,
    SalaGerarRequest,
    SalaOut,
    SalasListResponse,
    TemasResponse,
    QuestaoUpdate,
    TentativaIn,
    TentativaResultado,
    TrilhaEtapaOut,
    TrilhaGerarRequest,
    TrilhaOut,
    TrilhasListResponse,
)
from app.trilha_service import (
    avancar_etapa_trilha,
    gerar_sala,
    gerar_trilha,
    obter_sala_hoje,
)
from app.trilha_storage import (
    delete_trilha,
    get_sala,
    get_trilha,
    list_salas_trilha,
    list_trilhas,
    update_atividade_status,
    update_etapa_status,
)
from app.storage import (
    delete_documento,
    export_geracao_csv,
    get_banco_estatisticas,
    get_documento,
    get_geracao_with_questoes,
    get_questao_with_tentativas,
    list_banco_questoes,
    list_documentos,
    registrar_tentativa,
    update_questao,
)
from fastapi.responses import JSONResponse, PlainTextResponse, Response

from app.api_errors import _is_console_encode_error, raise_http_for_exception

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


@app.exception_handler(UnicodeEncodeError)
async def _handle_unicode_encode_error(_request, _exc: UnicodeEncodeError):
    return JSONResponse(
        status_code=500,
        content={
            "detail": (
                "Erro de codificacao no servidor. Feche todos os python run.py antigos "
                "e inicie apenas: .venv\\Scripts\\python.exe run.py"
            )
        },
    )


@app.exception_handler(ValueError)
async def _handle_value_error(_request, exc: ValueError):
    if _is_console_encode_error(exc):
        return JSONResponse(
            status_code=500,
            content={
                "detail": (
                    "Erro de codificacao no servidor. Feche todos os python run.py antigos "
                    "e inicie apenas: .venv\\Scripts\\python.exe run.py"
                )
            },
        )
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.get("/api/build")
def api_build():
    """Confirme que o servidor carregou o build novo (evita processo antigo na porta 8000)."""
    import app.generator as gen

    sample = ""
    try:
        import inspect

        src = inspect.getsource(gen.generate_from_text)
        sample = "enviando ao Bedrock" if "enviando ao Bedrock" in src else "legacy"
    except Exception:
        sample = "unknown"
    return {
        "build": getattr(app_pkg, "_BUILD", "unknown"),
        "generator_log_marker": sample,
    }


@app.middleware("http")
async def _guard_encoding_middleware(request, call_next):
    try:
        return await call_next(request)
    except Exception as exc:
        if _is_console_encode_error(exc):
            return JSONResponse(
                status_code=500,
                content={
                    "detail": (
                        "Erro de codificacao (console Windows). "
                        "Pare o servidor (Ctrl+C), feche outros python run.py e suba de novo: "
                        "python run.py"
                    )
                },
            )
        raise


@app.on_event("startup")
def _cleanup_orphans_on_startup() -> None:
    cleaned = cleanup_orphan_jobs()
    if cleaned:
        from app.console_io import safe_print

        safe_print(f"[startup] {cleaned} job(s) OCR orfao(s) marcados como interrupted.")

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


def _job_to_status(data: dict) -> OcrJobStatus:
    gerar_url = (
        f"/gerar/ocr-job/{data['job_id']}" if data.get("status") == "succeeded" else None
    )
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
        started_at=data.get("started_at"),
        finished_at=data.get("finished_at"),
        elapsed_seconds=data.get("elapsed_seconds"),
        textract_polls=data.get("textract_polls"),
        textract_status=data.get("textract_status"),
        textract_elapsed_seconds=data.get("textract_elapsed_seconds"),
        textract_job_id=data.get("textract_job_id"),
        gerar_questoes_url=gerar_url,
    )


@app.get("/ocr/jobs/{job_id}", response_model=OcrJobStatus)
def status_ocr(job_id: str):
    data = get_job(job_id)
    if not data:
        raise HTTPException(status_code=404, detail="Job não encontrado")
    return _job_to_status(data)


@app.post("/ocr/jobs/{job_id}/cancel", response_model=OcrJobStatus)
def cancelar_ocr_job(job_id: str):
    """
    Sinaliza cancelamento do job OCR. Importante:
    - O Textract da AWS NÃO expõe API de cancelamento de job assíncrono.
    - O processamento na AWS pode continuar até terminar (e ser cobrado);
      apenas paramos de acompanhar e marcamos o job como `cancelled`.
    - Se o job é órfão (sem thread ativa, ex.: servidor reiniciou),
      o status muda para `cancelled` na hora.
    """
    data = get_job(job_id)
    if not data:
        raise HTTPException(status_code=404, detail="Job não encontrado")
    if data.get("status") in ("succeeded", "failed", "cancelled", "interrupted"):
        return _job_to_status(data)
    request_cancel(job_id)
    refreshed = get_job(job_id) or data
    return _job_to_status(refreshed)


@app.get("/ocr/jobs", response_model=OcrJobsList)
def listar_ocr_jobs(only_active: bool = False, limit: int = 50):
    """Lista jobs OCR salvos em disco. Use only_active=true para ver só os em processamento."""
    if limit < 1 or limit > 200:
        raise HTTPException(status_code=400, detail="limit entre 1 e 200")
    all_jobs = list_jobs(only_active=only_active)
    total = len(all_jobs)
    return OcrJobsList(
        jobs=[_job_to_status(j) for j in all_jobs[:limit]],
        total=total,
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
            idioma=body.idioma,
            estilo=body.estilo,
            num_alternativas=body.num_alternativas,
            incluir_explicacao=body.incluir_explicacao,
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
    idioma: str = Form(default="pt"),
    estilo: str = Form(default="clinico"),
    num_alternativas: int = Form(default=5, ge=2, le=6),
    incluir_explicacao: bool = Form(default=True),
):
    data = await _read_pdf_upload(arquivo)
    tipo_list = _parse_tipos(tipos)
    keywords = _parse_keywords(palavras_chave)

    try:
        questoes, meta = generate_from_pdf_bytes(
            data,
            filename=arquivo.filename or "documento.pdf",
            num_questoes_por_chunk=num_questoes_por_chunk,
            tipos=tipo_list,
            dificuldade=dificuldade,
            max_chunks=max_chunks,
            tema=tema,
            palavras_chave=keywords,
            pagina_inicio=pagina_inicio,
            pagina_fim=pagina_fim,
            instrucoes_extras=instrucoes_extras,
            idioma=idioma,
            estilo=estilo,
            num_alternativas=num_alternativas,
            incluir_explicacao=incluir_explicacao,
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
    idioma: str = "pt",
    estilo: str = "clinico",
    num_alternativas: int = 5,
    incluir_explicacao: bool = True,
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
            idioma=idioma,
            estilo=estilo,
            num_alternativas=num_alternativas,
            incluir_explicacao=incluir_explicacao,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Job OCR não encontrado") from None
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e

    return GerarResponse(questoes=questoes, meta=meta)


@app.post("/gerar/documento/{documento_id}", response_model=GerarResponse)
def gerar_de_documento(
    documento_id: int,
    num_questoes_por_chunk: int = 2,
    num_questoes: Optional[int] = None,
    tipos: str = "multipla_escolha",
    dificuldade: Optional[str] = None,
    max_chunks: Optional[int] = None,
    tema: Optional[str] = None,
    palavras_chave: Optional[str] = None,
    pagina_inicio: Optional[int] = None,
    pagina_fim: Optional[int] = None,
    instrucoes_extras: Optional[str] = None,
    idioma: str = "pt",
    estilo: str = "clinico",
    num_alternativas: int = 5,
    incluir_explicacao: bool = True,
):
    """Reusa um documento já salvo no histórico (atalho — não precisa reenviar o PDF)."""
    n_por_chunk = num_questoes if num_questoes is not None else num_questoes_por_chunk
    tipo_list = _parse_tipos(tipos)
    keywords = _parse_keywords(palavras_chave)
    try:
        questoes, meta = generate_from_documento_id(
            documento_id,
            num_questoes_por_chunk=n_por_chunk,
            tipos=tipo_list,
            dificuldade=dificuldade,
            max_chunks=max_chunks,
            tema=tema,
            palavras_chave=keywords,
            pagina_inicio=pagina_inicio,
            pagina_fim=pagina_fim,
            instrucoes_extras=instrucoes_extras,
            idioma=idioma,
            estilo=estilo,
            num_alternativas=num_alternativas,
            incluir_explicacao=incluir_explicacao,
        )
    except Exception as e:
        raise_http_for_exception(e)
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


@app.get("/banco/questoes")
def banco_listar(
    documento_id: Optional[int] = None,
    geracao_id: Optional[int] = None,
    tipo: Optional[str] = None,
    dificuldade: Optional[str] = None,
    idioma: Optional[str] = None,
    estilo: Optional[str] = None,
    so_erradas: bool = False,
    so_nao_respondidas: bool = False,
    busca: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
):
    """Lista questões do banco com filtros, paginação e contagem de tentativas/acertos."""
    if limit < 1 or limit > 200:
        raise HTTPException(status_code=400, detail="limit entre 1 e 200")
    if offset < 0:
        raise HTTPException(status_code=400, detail="offset deve ser >= 0")
    return list_banco_questoes(
        documento_id=documento_id,
        geracao_id=geracao_id,
        tipo=tipo,
        dificuldade=dificuldade,
        idioma=idioma,
        estilo=estilo,
        so_erradas=so_erradas,
        so_nao_respondidas=so_nao_respondidas,
        busca=busca,
        limit=limit,
        offset=offset,
    )


@app.get("/banco/questoes/{questao_id}")
def banco_detalhe(questao_id: int):
    q = get_questao_with_tentativas(questao_id)
    if not q:
        raise HTTPException(status_code=404, detail="Questão não encontrada")
    return q


@app.patch("/banco/questoes/{questao_id}")
def banco_atualizar_questao(questao_id: int, body: QuestaoUpdate):
    """Edita enunciado, alternativas, gabarito, explicações etc. e persiste no banco."""
    updates = body.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail="Envie ao menos um campo para atualizar")
    try:
        q = update_questao(questao_id, updates)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not q:
        raise HTTPException(status_code=404, detail="Questão não encontrada")
    return q


@app.post("/banco/questoes/{questao_id}/tentativas", response_model=TentativaResultado)
def banco_responder(questao_id: int, body: TentativaIn):
    """Registra a resposta do usuário. Compara com o gabarito e devolve se acertou + explicações."""
    try:
        resultado = registrar_tentativa(
            questao_id=questao_id,
            resposta_usuario=body.resposta,
            tempo_resposta_ms=body.tempo_resposta_ms,
            comentario=body.comentario,
        )
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return TentativaResultado(**resultado)


@app.get("/banco/estatisticas")
def banco_estatisticas():
    """Resumo do banco: totais, distribuição e top 10 questões mais erradas."""
    return get_banco_estatisticas()


# --- Trilha de estudos ---


@app.post("/trilhas/gerar", response_model=TrilhaOut)
def trilha_gerar(body: TrilhaGerarRequest):
    """
    Gera trilha completa com IA (temas + plano diário) a partir de um documento OCR.

    Aceita documento com OCR, cache de PDF nativo, job OCR (`ocr_job_id`) ou `texto` colado no body.
    """
    try:
        trilha = gerar_trilha(
            documento_id=body.documento_id,
            objetivo=body.objetivo,
            semanas=body.semanas,
            horas_por_dia=body.horas_por_dia,
            dias_por_semana=body.dias_por_semana,
            max_temas=body.max_temas,
            instrucoes_extras=body.instrucoes_extras,
            ocr_job_id=body.ocr_job_id,
            texto=body.texto,
        )
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    return TrilhaOut(**trilha)


@app.get("/trilhas", response_model=TrilhasListResponse)
def trilhas_listar(documento_id: Optional[int] = None, limit: int = 50):
    if limit < 1 or limit > 200:
        raise HTTPException(status_code=400, detail="limit entre 1 e 200")
    items = list_trilhas(documento_id=documento_id, limit=limit)
    return TrilhasListResponse(trilhas=items, total=len(items))


@app.get("/trilhas/{trilha_id}", response_model=TrilhaOut)
def trilha_detalhe(trilha_id: int):
    trilha = get_trilha(trilha_id)
    if not trilha:
        raise HTTPException(status_code=404, detail="Trilha não encontrada")
    return TrilhaOut(**trilha)


@app.delete("/trilhas/{trilha_id}")
def trilha_excluir(trilha_id: int):
    if not delete_trilha(trilha_id):
        raise HTTPException(status_code=404, detail="Trilha não encontrada")
    return {"deleted": True, "trilha_id": trilha_id}


@app.post("/trilhas/{trilha_id}/sala", response_model=SalaOut)
def trilha_gerar_sala(trilha_id: int, body: Optional[SalaGerarRequest] = None):
    """Gera (ou reutiliza) sala de estudo para a etapa atual ou etapa_id informada."""
    req = body or SalaGerarRequest()
    try:
        sala = gerar_sala(
            trilha_id,
            etapa_id=req.etapa_id,
            regenerar=req.regenerar,
            instrucoes_extras=req.instrucoes_extras,
        )
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    return SalaOut(**sala)


@app.get("/trilhas/{trilha_id}/sala/hoje", response_model=SalaOut)
def trilha_sala_hoje(trilha_id: int, regenerar: bool = False):
    """Retorna a sala criada hoje ou gera uma nova se ainda não existir."""
    try:
        if regenerar:
            sala = gerar_sala(trilha_id, regenerar=True)
        else:
            sala = obter_sala_hoje(trilha_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    return SalaOut(**sala)


@app.get("/trilhas/{trilha_id}/salas", response_model=SalasListResponse)
def trilha_listar_salas(trilha_id: int, limit: int = 30):
    if not get_trilha(trilha_id, include_etapas=False):
        raise HTTPException(status_code=404, detail="Trilha não encontrada")
    if limit < 1 or limit > 100:
        raise HTTPException(status_code=400, detail="limit entre 1 e 100")
    return SalasListResponse(salas=list_salas_trilha(trilha_id, limit=limit))


@app.post("/trilhas/{trilha_id}/avancar", response_model=TrilhaOut)
def trilha_avancar(trilha_id: int):
    """Marca etapa atual como concluída e avança para a próxima."""
    try:
        trilha = avancar_etapa_trilha(trilha_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return TrilhaOut(**trilha)


@app.get("/salas/{sala_id}", response_model=SalaOut)
def sala_detalhe(sala_id: int):
    sala = get_sala(sala_id)
    if not sala:
        raise HTTPException(status_code=404, detail="Sala não encontrada")
    return SalaOut(**sala)


@app.patch("/trilhas/etapas/{etapa_id}", response_model=TrilhaEtapaOut)
def trilha_etapa_atualizar(etapa_id: int, body: EtapaStatusUpdate):
    etapa = update_etapa_status(etapa_id, body.status)
    if not etapa:
        raise HTTPException(status_code=404, detail="Etapa não encontrada")
    return TrilhaEtapaOut(**etapa)


@app.patch("/salas/atividades/{atividade_id}")
def sala_atividade_atualizar(atividade_id: int, body: AtividadeStatusUpdate):
    try:
        ativ = update_atividade_status(atividade_id, body.status)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not ativ:
        raise HTTPException(status_code=404, detail="Atividade não encontrada")
    return ativ


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
