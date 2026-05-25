const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

let fileOcr = null;
let filePdf = null;
let ocrJobId = null;
let pollTimer = null;

function activeFile() {
  return $("#panel-ocr").classList.contains("active") ? fileOcr : filePdf;
}

$$(".tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    $$(".tab").forEach((t) => t.classList.remove("active"));
    $$(".panel").forEach((p) => p.classList.remove("active"));
    btn.classList.add("active");
    $(`#panel-${btn.dataset.panel}`).classList.add("active");
    hideStatus();
    clearResults();
    if (btn.dataset.panel === "historico") loadHistorico();
  });
});

function setupDrop(zoneId, inputId, onFile) {
  const zone = $(zoneId);
  const input = $(inputId);
  const nameEl = zone.querySelector(".name");

  zone.addEventListener("click", () => input.click());
  input.addEventListener("change", () => {
    if (input.files[0]) onFile(input.files[0], nameEl);
  });
  zone.addEventListener("dragover", (e) => {
    e.preventDefault();
    zone.classList.add("dragover");
  });
  zone.addEventListener("dragleave", () => zone.classList.remove("dragover"));
  zone.addEventListener("drop", (e) => {
    e.preventDefault();
    zone.classList.remove("dragover");
    const f = e.dataTransfer.files[0];
    if (f && f.name.toLowerCase().endsWith(".pdf")) onFile(f, nameEl);
    else showStatus("Envie apenas arquivos PDF.", "error");
  });
}

setupDrop("#drop-ocr", "#file-ocr", (f, el) => {
  fileOcr = f;
  el.textContent = f.name;
});
setupDrop("#drop-pdf", "#file-pdf", (f, el) => {
  filePdf = f;
  el.textContent = f.name;
});

function showStatus(msg, type = "processing", showProgress = false) {
  const box = $("#status");
  box.className = `status-box show ${type}`;
  box.innerHTML =
    msg + (showProgress ? '<div class="progress-bar"><span></span></div>' : "");
}

function hideStatus() {
  $("#status").className = "status-box";
}

let genTimer = null;
let genStart = 0;

function startGenTimer(baseMsg) {
  stopGenTimer();
  genStart = Date.now();
  const tick = () => {
    const sec = Math.floor((Date.now() - genStart) / 1000);
    const mm = String(Math.floor(sec / 60)).padStart(2, "0");
    const ss = String(sec % 60).padStart(2, "0");
    showStatus(
      `${baseMsg}<br><small>Tempo decorrido: <strong>${mm}:${ss}</strong> · cada trecho leva ~30–60s. Veja o terminal do servidor para progresso por trecho.</small>`,
      "processing",
      true
    );
  };
  tick();
  genTimer = setInterval(tick, 1000);
}

function stopGenTimer() {
  if (genTimer) {
    clearInterval(genTimer);
    genTimer = null;
  }
}

function clearResults() {
  $("#results").innerHTML = "";
}

function getOpts() {
  return {
    num_questoes_por_chunk: $("#num_questoes").value || 2,
    tipos: $("#tipos").value,
    max_chunks: $("#max_chunks").value || "",
    dificuldade: $("#dificuldade").value || "",
    tema: ($("#tema").value || "").trim(),
    palavras_chave: ($("#palavras_chave").value || "").trim(),
    pagina_inicio: $("#filtro_pag_ini").value || "",
    pagina_fim: $("#filtro_pag_fim").value || "",
    instrucoes_extras: ($("#instrucoes_extras").value || "").trim(),
    idioma: $("#idioma").value || "pt",
    estilo: $("#estilo").value || "clinico",
    num_alternativas: $("#num_alternativas").value || "5",
    incluir_explicacao: $("#incluir_explicacao").value === "true",
  };
}

function appendOptParams(params, o) {
  if (o.tema) params.set("tema", o.tema);
  if (o.palavras_chave) params.set("palavras_chave", o.palavras_chave);
  if (o.pagina_inicio) params.set("pagina_inicio", o.pagina_inicio);
  if (o.pagina_fim) params.set("pagina_fim", o.pagina_fim);
  if (o.instrucoes_extras) params.set("instrucoes_extras", o.instrucoes_extras);
  params.set("idioma", o.idioma);
  params.set("estilo", o.estilo);
  params.set("num_alternativas", o.num_alternativas);
  params.set("incluir_explicacao", o.incluir_explicacao ? "true" : "false");
}

function appendOptForm(fd, o) {
  if (o.tema) fd.append("tema", o.tema);
  if (o.palavras_chave) fd.append("palavras_chave", o.palavras_chave);
  if (o.pagina_inicio) fd.append("pagina_inicio", o.pagina_inicio);
  if (o.pagina_fim) fd.append("pagina_fim", o.pagina_fim);
  if (o.instrucoes_extras) fd.append("instrucoes_extras", o.instrucoes_extras);
  fd.append("idioma", o.idioma);
  fd.append("estilo", o.estilo);
  fd.append("num_alternativas", o.num_alternativas);
  fd.append("incluir_explicacao", o.incluir_explicacao ? "true" : "false");
}

const ESTILO_LABEL = {
  clinico: "caso clínico",
  diagnostico: "diagnóstico",
  conduta: "conduta",
  farmacologia: "farmacologia",
  cirurgia: "cirurgia",
  pediatria: "pediatria",
  obstetricia: "obstetrícia",
  emergencia: "emergência",
  saude_publica: "saúde pública",
  imagem: "exame/imagem",
  geral: "teórica",
};

function metaResumo(meta) {
  if (!meta) return "";
  const parts = [];
  if (meta.idioma) parts.push(`idioma: ${meta.idioma}`);
  if (meta.estilo) parts.push(`estilo: ${ESTILO_LABEL[meta.estilo] || meta.estilo}`);
  if (meta.num_alternativas) parts.push(`${meta.num_alternativas} alternativas`);
  if (meta.tema) parts.push(`tema: ${meta.tema}`);
  if (meta.questoes_geradas != null) parts.push(`${meta.questoes_geradas} q.`);
  if (meta.modelo) parts.push(meta.modelo);
  return parts.join(" · ");
}

function renderQuestions(data) {
  const { questoes, meta } = data;
  let html = `<h2>${questoes.length} questão(ões)</h2>`;
  const resumo = metaResumo(meta);
  if (resumo) html += `<p class="meta">${escapeHtml(resumo)}</p>`;
  if (meta && meta.erros && meta.erros.length) {
    html += `<p class="meta" style="color:var(--err)">${escapeHtml(
      meta.erros.join(" | ")
    )}</p>`;
  }

  questoes.forEach((q, i) => {
    html += renderQuestionCard(q, i);
  });
  $("#results").innerHTML = html;
}

function renderQuestionCard(q, index) {
  const fonte = q.fonte && q.fonte.pagina_inicio ? ` · pág. ${q.fonte.pagina_inicio}` : "";
  const dif = q.dificuldade ? ` · ${q.dificuldade}` : "";

  const badges = [];
  if (q.idioma) badges.push(q.idioma);
  if (q.estilo) badges.push(ESTILO_LABEL[q.estilo] || q.estilo);
  const badgesHtml = badges.length
    ? `<div class="badges">${badges
        .map((b) => `<span class="badge">${escapeHtml(b)}</span>`)
        .join("")}</div>`
    : "";

  let alts = "";
  if (q.alternativas && q.alternativas.length) {
    const gabarito = String(q.gabarito || "").trim().toUpperCase();
    alts =
      '<ul class="alts">' +
      q.alternativas
        .map((a, j) => {
          const letra = String.fromCharCode(65 + j);
          const correta = letra === gabarito ? " correta" : "";
          return `<li class="alt${correta}"><span class="letra">${letra}</span><span>${escapeHtml(
            a
          )}</span></li>`;
        })
        .join("") +
      "</ul>";
  }

  let detalhes = "";
  const hasExpl = q.explicacao || (q.explicacoes_alternativas && Object.keys(q.explicacoes_alternativas).length);
  if (hasExpl || q.referencia) {
    const expls = renderExplicacoes(q);
    detalhes = `
      <details>
        <summary>Ver resposta comentada</summary>
        <div class="expl-block">${expls}</div>
      </details>`;
  }

  return `
    <div class="card">
      <div class="tipo">${escapeHtml(q.tipo)}${dif}${fonte}</div>
      ${badgesHtml}
      <div class="enunciado">${index + 1}. ${escapeHtml(q.enunciado)}</div>
      ${alts}
      <div class="gabarito">Gabarito: ${escapeHtml(q.gabarito || "")}</div>
      ${detalhes}
    </div>`;
}

function renderExplicacoes(q) {
  let out = "";
  if (q.explicacao) {
    out += `<p><strong>Por que ${escapeHtml(q.gabarito || "")} é a correta:</strong> ${escapeHtml(
      q.explicacao
    )}</p>`;
  }
  const expl = q.explicacoes_alternativas;
  if (expl && Object.keys(expl).length) {
    const gabarito = String(q.gabarito || "").trim().toUpperCase();
    out +=
      '<ul class="expl-alts">' +
      Object.keys(expl)
        .sort()
        .map((letra) => {
          const correta = letra.toUpperCase() === gabarito ? " correta" : "";
          return `<li class="expl-alt${correta}"><strong>${escapeHtml(
            letra
          )}.</strong> ${escapeHtml(expl[letra])}</li>`;
        })
        .join("") +
      "</ul>";
  }
  if (q.referencia) {
    out += `<div class="ref">Referência: "${escapeHtml(q.referencia)}"</div>`;
  }
  return out;
}

function escapeHtml(s) {
  const d = document.createElement("div");
  d.textContent = s;
  return d.innerHTML;
}

async function apiError(res) {
  const err = await res.json().catch(() => ({}));
  const detail = err.detail;
  const msg = Array.isArray(detail)
    ? detail.map((d) => d.msg || JSON.stringify(d)).join("; ")
    : detail || res.statusText || "Erro na API";
  throw new Error(msg);
}

$("#btn-ocr-start").addEventListener("click", async () => {
  const selectedFile = fileOcr;
  if (!selectedFile) {
    showStatus("Selecione um PDF primeiro.", "error");
    return;
  }
  clearResults();
  ocrJobId = null;
  if (pollTimer) clearInterval(pollTimer);

  const fd = new FormData();
  fd.append("arquivo", selectedFile);
  const pi = $("#pagina_inicio").value;
  const pf = $("#pagina_fim").value;
  if (pi) fd.append("pagina_inicio", pi);
  if (pf) fd.append("pagina_fim", pf);

  $("#btn-ocr-start").disabled = true;
  showStatus("Enviando PDF e iniciando OCR no Textract…", "processing", true);

  try {
    const res = await fetch("/ocr/pdf", { method: "POST", body: fd });
    if (!res.ok) await apiError(res);
    const data = await res.json();
    ocrJobId = data.job_id;
    showStatus(
      `OCR iniciado. Job: <code>${ocrJobId}</code><br>${data.message}`,
      "processing",
      true
    );
    $("#btn-gerar-ocr").disabled = true;
    $("#btn-descobrir-temas").disabled = true;
    clearTemas();
    startPolling();
  } catch (e) {
    showStatus(e.message, "error");
  } finally {
    $("#btn-ocr-start").disabled = false;
  }
});

function startPolling() {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(pollOcrJob, 5000);
  pollOcrJob();
}

async function pollOcrJob() {
  if (!ocrJobId) return;
  try {
    const res = await fetch(`/ocr/jobs/${ocrJobId}`);
    if (!res.ok) await apiError(res);
    const j = await res.json();
    const phase = j.phase ? ` (${j.phase})` : "";
    if (j.status === "succeeded") {
      clearInterval(pollTimer);
      pollTimer = null;
      showStatus(
        `OCR concluído! ${j.paginas_ocr || "?"} páginas · ${(j.caracteres || 0).toLocaleString()} caracteres.`,
        "ok"
      );
      $("#btn-gerar-ocr").disabled = false;
      $("#btn-descobrir-temas").disabled = false;
    } else if (j.status === "failed") {
      clearInterval(pollTimer);
      pollTimer = null;
      showStatus(`OCR falhou: ${j.error || "erro desconhecido"}`, "error");
    } else {
      showStatus(`Status: <strong>${j.status}</strong>${phase}`, "processing", true);
    }
  } catch (e) {
    showStatus(e.message, "error");
  }
}

$("#btn-gerar-ocr").addEventListener("click", async () => {
  if (!ocrJobId) {
    showStatus("Inicie o OCR antes.", "error");
    return;
  }
  const o = getOpts();
  const params = new URLSearchParams({
    num_questoes_por_chunk: o.num_questoes_por_chunk,
    tipos: o.tipos,
  });
  if (o.max_chunks) params.set("max_chunks", o.max_chunks);
  if (o.dificuldade) params.set("dificuldade", o.dificuldade);
  appendOptParams(params, o);

  $("#btn-gerar-ocr").disabled = true;
  const focoMsg = o.tema ? ` (foco: "${escapeHtml(o.tema)}")` : "";
  const maxMsg = o.max_chunks ? ` · até ${o.max_chunks} trecho(s)` : " · SEM limite de trechos (pode demorar muito)";
  startGenTimer(`Gerando questões com Bedrock${focoMsg}${maxMsg}…`);

  try {
    const res = await fetch(`/gerar/ocr-job/${ocrJobId}?${params}`, { method: "POST" });
    if (!res.ok) await apiError(res);
    const data = await res.json();
    stopGenTimer();
    showStatus(`Questões geradas em ${Math.floor((Date.now() - genStart) / 1000)}s!`, "ok");
    renderQuestions(data);
  } catch (e) {
    stopGenTimer();
    showStatus(e.message, "error");
  } finally {
    $("#btn-gerar-ocr").disabled = false;
  }
});

$("#btn-descobrir-temas").addEventListener("click", async () => {
  if (!ocrJobId) {
    showStatus("Conclua o OCR antes.", "error");
    return;
  }
  $("#btn-descobrir-temas").disabled = true;
  showStatus("Pedindo ao Bedrock para listar temas do material…", "processing", true);
  try {
    const res = await fetch(`/temas/ocr-job/${ocrJobId}?max_topics=10`);
    if (!res.ok) await apiError(res);
    const data = await res.json();
    renderTemas(data.temas || []);
    showStatus(
      `${(data.temas || []).length} temas sugeridos. Clique para preencher o foco.`,
      "ok"
    );
  } catch (e) {
    showStatus(e.message, "error");
  } finally {
    $("#btn-descobrir-temas").disabled = false;
  }
});

function clearTemas() {
  const box = $("#temas-sugeridos");
  if (box) box.innerHTML = "";
}

function renderTemas(temas) {
  const box = $("#temas-sugeridos");
  if (!box) return;
  if (!temas.length) {
    box.innerHTML = '<span class="hint">Nenhum tema retornado.</span>';
    return;
  }
  box.innerHTML = temas
    .map(
      (t, i) =>
        `<button type="button" class="tema-chip" data-i="${i}" title="${escapeHtml(
          (t.palavras_chave || []).join(", ")
        )}">${escapeHtml(t.titulo)}</button>`
    )
    .join("");
  box.querySelectorAll(".tema-chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      box.querySelectorAll(".tema-chip").forEach((c) => c.classList.remove("active"));
      chip.classList.add("active");
      const t = temas[parseInt(chip.dataset.i, 10)];
      $("#tema").value = t.titulo;
      $("#palavras_chave").value = (t.palavras_chave || []).join(", ");
    });
  });
}

$("#btn-pdf-gerar").addEventListener("click", async () => {
  const selectedFile = filePdf;
  if (!selectedFile) {
    showStatus("Selecione um PDF primeiro.", "error");
    return;
  }
  clearResults();
  const o = getOpts();
  const fd = new FormData();
  fd.append("arquivo", selectedFile);
  fd.append("num_questoes_por_chunk", o.num_questoes_por_chunk);
  fd.append("tipos", o.tipos);
  if (o.max_chunks) fd.append("max_chunks", o.max_chunks);
  if (o.dificuldade) fd.append("dificuldade", o.dificuldade);
  appendOptForm(fd, o);

  $("#btn-pdf-gerar").disabled = true;
  const maxMsg = o.max_chunks ? ` · até ${o.max_chunks} trecho(s)` : " · SEM limite de trechos (pode demorar muito)";
  startGenTimer(`Extraindo texto e gerando questões${maxMsg}…`);

  try {
    const res = await fetch("/gerar/pdf", { method: "POST", body: fd });
    if (!res.ok) await apiError(res);
    const data = await res.json();
    stopGenTimer();
    showStatus(`Pronto! ${Math.floor((Date.now() - genStart) / 1000)}s`, "ok");
    renderQuestions(data);
  } catch (e) {
    stopGenTimer();
    showStatus(e.message, "error");
  } finally {
    $("#btn-pdf-gerar").disabled = false;
  }
});

async function loadHistorico() {
  const box = $("#historico-lista");
  box.innerHTML = '<p class="hint">Carregando…</p>';
  try {
    const res = await fetch("/historico/documentos?limit=50");
    if (!res.ok) await apiError(res);
    const data = await res.json();
    const docs = data.documentos || [];
    if (!docs.length) {
      box.innerHTML = '<p class="hint">Nenhum documento ainda. Faça um OCR ou envie um PDF.</p>';
      return;
    }
    box.innerHTML = "";
    for (const d of docs) {
      const el = document.createElement("div");
      el.className = "doc-card";
      el.innerHTML = `
        <h3>${escapeHtml(d.nome_arquivo)}</h3>
        <div class="doc-meta">
          ${d.paginas || "?"} págs · ${(d.caracteres || 0).toLocaleString()} caracteres ·
          ${d.geracoes_count || 0} gerações · ${d.questoes_total || 0} questões ·
          ${formatDate(d.criado_em)}
        </div>
        <div class="hint">Carregando gerações…</div>`;
      box.appendChild(el);
      try {
        const r2 = await fetch(`/historico/documentos/${d.id}`);
        if (r2.ok) {
          const det = await r2.json();
          renderDocDetail(el, det);
        }
      } catch {}
    }
  } catch (e) {
    box.innerHTML = `<p class="hint" style="color:var(--err)">${escapeHtml(e.message)}</p>`;
  }
}

function formatDate(s) {
  if (!s) return "";
  const d = new Date(s.includes("T") ? s : s.replace(" ", "T") + "Z");
  if (isNaN(d.getTime())) return s;
  return d.toLocaleString("pt-BR");
}

function renderDocDetail(el, doc) {
  const hint = el.querySelector(".hint");
  if (hint) hint.remove();
  const gers = doc.geracoes || [];
  if (!gers.length) {
    const empty = document.createElement("div");
    empty.className = "hint";
    empty.textContent = "Nenhuma geração salva ainda.";
    el.appendChild(empty);
    return;
  }
  for (const g of gers) {
    const row = document.createElement("div");
    row.className = "ger-row";
    const tema = g.tema ? `<span class="badge">tema: ${escapeHtml(g.tema)}</span>` : "";
    const dif = g.dificuldade ? `<span class="badge">${escapeHtml(g.dificuldade)}</span>` : "";
    const pags =
      g.pagina_inicio || g.pagina_fim
        ? `<span class="badge">pgs ${g.pagina_inicio || "?"}–${g.pagina_fim || "?"}</span>`
        : "";
    row.innerHTML = `
      <span>#${g.id} · ${g.questoes_count} questões</span>
      ${tema} ${dif} ${pags}
      <span class="badge">${formatDate(g.criado_em)}</span>
      <div class="ger-actions">
        <button type="button" class="linkbtn" data-action="ver" data-id="${g.id}">Ver</button>
        <a class="linkbtn" href="/historico/geracoes/${g.id}/csv" download>CSV</a>
      </div>`;
    el.appendChild(row);
  }
  el.querySelectorAll('[data-action="ver"]').forEach((b) => {
    b.addEventListener("click", () => verGeracao(parseInt(b.dataset.id, 10)));
  });
  if (doc.ocr_job_id) {
    const reuse = document.createElement("div");
    reuse.className = "ger-row";
    reuse.innerHTML = `
      <span class="hint">OCR job: <code>${doc.ocr_job_id}</code></span>
      <div class="ger-actions">
        <button type="button" class="linkbtn" data-action="reuse" data-job="${doc.ocr_job_id}">Reusar para gerar mais</button>
        <button type="button" class="linkbtn danger" data-action="del" data-id="${doc.id}">Excluir</button>
      </div>`;
    el.appendChild(reuse);
    reuse.querySelector('[data-action="reuse"]').addEventListener("click", () => {
      ocrJobId = doc.ocr_job_id;
      $$(".tab").forEach((t) => t.classList.remove("active"));
      $$(".panel").forEach((p) => p.classList.remove("active"));
      document.querySelector('.tab[data-panel="ocr"]').classList.add("active");
      $("#panel-ocr").classList.add("active");
      $("#btn-gerar-ocr").disabled = false;
      $("#btn-descobrir-temas").disabled = false;
      showStatus(
        `Reusando OCR job <code>${doc.ocr_job_id}</code> (sem custo Textract). Ajuste os filtros e clique em "3. Gerar questões".`,
        "ok"
      );
    });
    reuse.querySelector('[data-action="del"]').addEventListener("click", async () => {
      if (!confirm("Excluir este documento e todas as gerações?")) return;
      const r = await fetch(`/historico/documentos/${doc.id}`, { method: "DELETE" });
      if (r.ok) loadHistorico();
    });
  }
}

async function verGeracao(geracaoId) {
  try {
    const res = await fetch(`/historico/geracoes/${geracaoId}`);
    if (!res.ok) await apiError(res);
    const g = await res.json();
    renderQuestions({ questoes: g.questoes || [], meta: g.meta || {} });
    showStatus(`Geração #${geracaoId} carregada do histórico.`, "ok");
    window.scrollTo({ top: document.body.scrollHeight, behavior: "smooth" });
  } catch (e) {
    showStatus(e.message, "error");
  }
}

$("#btn-historico-reload").addEventListener("click", loadHistorico);

fetch("/health")
  .then((r) => r.json())
  .then((h) => {
    if (!h.s3_bucket_configured) {
      showStatus(
        "Aviso: S3_BUCKET não configurado no .env — aba OCR não vai funcionar.",
        "error"
      );
      return;
    }
    if (h.regions_match === false) {
      showStatus(
        `Região errada: .env AWS_REGION=${h.aws_region}, bucket está em ${h.s3_bucket_region}. ` +
          "Corrija o .env e reinicie o servidor (Ctrl+C → python run.py).",
        "error"
      );
      return;
    }
    if (h.status === "ok") {
      console.log("AWS OK", h.aws_region, h.s3_bucket);
    }
  })
  .catch(() => {});
