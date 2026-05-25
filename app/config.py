from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

# Carrega .env no os.environ (Bedrock bearer, IAM keys, S3, etc.)
load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    aws_region: str = "us-east-1"
    # Inference profile — use modelo ATIVO (não "Legada") do seu Bedrock → Model catalog
    bedrock_model_id: str = "us.amazon.nova-lite-v1:0"
    # 6000 chars ≈ 1500 tokens — bom equilibrio entre contexto e latência
    chunk_size_chars: int = 6000
    chunk_overlap_chars: int = 800
    max_chunks_per_request: int = 30

    # OCR (Amazon Textract + S3) — exige IAM (Access Key), não só chave Bedrock
    s3_bucket: str = ""
    s3_prefix: str = "ocr-uploads/"
    ocr_jobs_dir: str = "data/ocr_jobs"
    textract_poll_seconds: int = 10
    max_pdf_upload_mb: int = 500
    min_chars_per_page_native: int = 30


settings = Settings()


def effective_bedrock_model_id() -> str:
    """
    Retorna o modelId para InvokeModel.
    Modelos Anthropic em regiões US costumam exigir inference profile (prefixo us.).
    """
    model_id = settings.bedrock_model_id.strip()
    if not model_id:
        model_id = "us.amazon.nova-lite-v1:0"
    if model_id.startswith(("us.", "eu.", "global.", "arn:aws:")):
        return model_id
    region = settings.aws_region
    for prefix in ("anthropic.", "amazon.", "meta.", "mistral."):
        if model_id.startswith(prefix):
            if region.startswith("us-"):
                return f"us.{model_id}"
            if region.startswith("eu-"):
                return f"eu.{model_id}"
    return model_id
