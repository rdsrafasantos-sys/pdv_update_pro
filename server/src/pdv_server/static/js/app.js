// ──────────────────────────────────────────────
// API (escopada pela rede ativa -- window.REDE_ID injetado pelo template)
// ──────────────────────────────────────────────
function API(caminho) {
  return `/api/${window.REDE_ID}${caminho}`;
}

function fazerLogout() {
  fetch('/logout', { method: 'POST' }).finally(() => { window.location.href = '/login'; });
}
window.fazerLogout = fazerLogout;

// ──────────────────────────────────────────────
// ESTADO GLOBAL
// ──────────────────────────────────────────────
let lojas = [];
let arquivoSelecionado = null;
let arquivosPorNome = {}; // nome -> {nome, tamanho_mb, data, versao}
let eventSource = null;
let statusPDVs = {}; // progresso de atualizacao de zip (somente view "pdv"), por pdvId

const KEYS = ["agente", "pdv", "replicacao"];
const seletores = {};
for (const k of KEYS) {
  seletores[k] = { lojaAtiva: null, selecionados: new Set(), ping: {} };
}

let _modelosPorEcf = {}; // ecf (int) -> {modelo_id, modelo} — populado pelo ERP

function categoriaModelo(modelo_id) {
  const MAP = {
    1: { label: "PDV",          cor: "#3b82f6", bg: "#3b82f620" },
    2: { label: "SELFCHECKOUT", cor: "#8b5cf6", bg: "#8b5cf620" },
    3: { label: "PDV TOUCH",    cor: "#06b6d4", bg: "#06b6d420" },
    4: { label: "PDV SMART",    cor: "#f97316", bg: "#f9731620" },
    5: { label: "CAIXA",        cor: "#22c55e", bg: "#22c55e20" },
  };
  return MAP[modelo_id] || { label: "PDV", cor: "#6b7280", bg: "#6b728020" };
}

function tagModelo(modelo_id, modelo) {
  if (!modelo_id && !modelo) return "";
  const cat = categoriaModelo(modelo_id);
  const label = modelo || cat.label;
  return `<span class="modelo-tag" style="--tag-cor:${cat.cor};--tag-bg:${cat.bg}">${label}</span>`;
}

let pollReplicacaoTimer = null;
let _sysinfoTimer = null;
const _SYSINFO_INTERVALO_MS = 20000;
let _sysinfoLoja = {};
const _SYSINFO_PDV_INTERVALO_MS = 30000;

// ──────────────────────────────────────────────
// CACHE DE LOJAS (localStorage, TTL 60s)
// Evita fetch ao Mongo em visitas subsequentes — renderiza instantâneo
// e atualiza em background.
// ──────────────────────────────────────────────
const _CACHE_LOJAS_TTL = 60000;

function _cacheLojasSalvar(data) {
  try {
    localStorage.setItem(`pdv_lojas_${window.REDE_ID}`, JSON.stringify({ ts: Date.now(), data }));
  } catch (e) {}
}

function _cacheLojasBuscar() {
  try {
    const raw = localStorage.getItem(`pdv_lojas_${window.REDE_ID}`);
    if (!raw) return null;
    const { ts, data } = JSON.parse(raw);
    if (Date.now() - ts < _CACHE_LOJAS_TTL) return data;
  } catch (e) {}
  return null;
}

// ──────────────────────────────────────────────
// LOADING OVERLAY DO DASHBOARD
// ──────────────────────────────────────────────
function _dashMostrarLoading() {
  const el = document.getElementById("dashLoadingOverlay");
  if (!el) return;
  el.classList.remove("fechando");
  el.style.display = "flex";
  _dashProgressoLoading(5, "Buscando lojas e histórico...");
}

function _dashProgressoLoading(pct, msg) {
  const bar = document.getElementById("dashLoadingBar");
  const msgEl = document.getElementById("dashLoadingMsg");
  if (bar) bar.style.width = `${pct}%`;
  if (msgEl && msg) msgEl.textContent = msg;
}

function _dashOcultarLoading() {
  _dashProgressoLoading(100, "Pronto!");
  setTimeout(() => {
    const el = document.getElementById("dashLoadingOverlay");
    if (!el) return;
    el.classList.add("fechando");
    setTimeout(() => { el.style.display = "none"; el.classList.remove("fechando"); }, 300);
  }, 400);
}

// ──────────────────────────────────────────────
// NAVEGACAO (sidebar + views)
// ──────────────────────────────────────────────
function mostrarView(nome) {
  for (const v of ["dashboard", "atualizacoes", "replicacao", "config", "fiscal"]) {
    const el = document.getElementById(`view-${v}`);
    if (el) el.style.display = v === nome ? "flex" : "none";
  }
  document.querySelectorAll(".menu-item").forEach(el => {
    el.classList.toggle("active", el.dataset.view === nome);
  });
  if (nome === "dashboard") { carregarDashboard(); _iniciarPolingSysinfo(); }
  else if (nome === "fiscal") { carregarPendenciasFiscais(); }
  else if (nome === "atualizacoes") { intUpdCarregar(); }
  else { _pararPolingSysinfo(); _pararPolingSysinfoLojas(); }
}

function mostrarAtualizacoesTab(nome) {
  document.querySelectorAll(".cfg-tab-content[id^='atu-tab-']").forEach(el => el.classList.remove("active"));
  document.querySelectorAll(".cfg-tab-btn[data-atu-tab]").forEach(el => el.classList.remove("active"));
  const painel = document.getElementById("atu-tab-" + nome);
  if (painel) painel.classList.add("active");
  const btn = document.querySelector(`.cfg-tab-btn[data-atu-tab="${nome}"]`);
  if (btn) btn.classList.add("active");
}
window.mostrarAtualizacoesTab = mostrarAtualizacoesTab;
window.mostrarView = mostrarView;

function mostrarConfigTab(nome) {
  document.querySelectorAll(".cfg-tab-content").forEach(el => el.classList.remove("active"));
  document.querySelectorAll(".cfg-tab-btn").forEach(el => el.classList.remove("active"));
  const painel = document.getElementById("cfg-tab-" + nome);
  if (painel) painel.classList.add("active");
  const btn = document.querySelector(`.cfg-tab-btn[data-cfg-tab="${nome}"]`);
  if (btn) btn.classList.add("active");
}
window.mostrarConfigTab = mostrarConfigTab;

function mostrarFiscalTab(nome) {
  document.querySelectorAll(".cfg-tab-content[id^='fiscal-tab-']").forEach(el => el.classList.remove("active"));
  document.querySelectorAll(".cfg-tab-btn[data-fiscal-tab]").forEach(el => el.classList.remove("active"));
  const painel = document.getElementById("fiscal-tab-" + nome);
  if (painel) painel.classList.add("active");
  const btn = document.querySelector(`.cfg-tab-btn[data-fiscal-tab="${nome}"]`);
  if (btn) btn.classList.add("active");
}
window.mostrarFiscalTab = mostrarFiscalTab;

// ──────────────────────────────────────────────
// SIDEBAR: secoes recolhiveis (clicar no titulo expande/recolhe)
// ──────────────────────────────────────────────
function toggleMenuGroup(tituloEl) {
  const grupo = tituloEl.parentElement;
  grupo.classList.toggle("collapsed");
  const grupos = {};
  document.querySelectorAll(".menu-group").forEach(g => {
    grupos[g.querySelector(".menu-section").textContent.trim()] = g.classList.contains("collapsed");
  });
  localStorage.setItem("menuGruposRecolhidos", JSON.stringify(grupos));
}
window.toggleMenuGroup = toggleMenuGroup;

function restaurarEstadoMenu() {
  const salvo = localStorage.getItem("menuGruposRecolhidos");
  if (!salvo) return;
  try {
    const grupos = JSON.parse(salvo);
    document.querySelectorAll(".menu-group").forEach(g => {
      const titulo = g.querySelector(".menu-section").textContent.trim();
      g.classList.toggle("collapsed", !!grupos[titulo]);
    });
  } catch (e) { /* ignora estado invalido */ }
}

// ──────────────────────────────────────────────
// SELETOR DE LOJA/PDV (parametrizado por key: "agente" | "pdv" | "replicacao")
// ──────────────────────────────────────────────
function renderLojaTabs(key) {
  const el = document.getElementById(`tabs-${key}`);
  if (!el) return;
  if (lojas.length === 0) {
    el.innerHTML = '<div class="empty">Nenhuma loja encontrada.</div>';
    return;
  }
  if (!seletores[key].lojaAtiva) seletores[key].lojaAtiva = lojas[0].id;
  el.innerHTML = lojas.map(l => `
    <div class="tab ${l.id === seletores[key].lojaAtiva ? 'active' : ''}" onclick="selecionarLoja('${key}','${l.id}')">
      ${l.nome} <span class="text-muted">(${l.pdvs.length})</span>
    </div>
  `).join("");
  renderPDVs(key);
}

function selecionarLoja(key, lojaId) {
  seletores[key].lojaAtiva = lojaId;
  seletores[key].selecionados = new Set();
  renderLojaTabs(key);
  if (key === "pdv") atualizarBotaoPdv();
  if (key === "agente") atualizarBotaoAgente();
}
window.selecionarLoja = selecionarLoja;

function renderPDVs(key) {
  const el = document.getElementById(`pdvgrid-${key}`);
  if (!el) return;
  const loja = lojas.find(l => l.id === seletores[key].lojaAtiva);
  if (!loja || loja.pdvs.length === 0) {
    el.innerHTML = '<div class="empty">Nenhum PDV nesta loja.</div>';
    return;
  }
  el.innerHTML = loja.pdvs.map(pdv => {
    const sel = seletores[key].selecionados.has(pdv.id);
    const ping = seletores[key].ping[pdv.id];
    const prog = key === "pdv" ? statusPDVs[pdv.id] : null;
    let badge = "";
    if (prog) {
      badge = `<div class="badge ${prog.status}"><span class="dot ${prog.status === 'atualizando' ? 'pulse' : ''}"></span>${prog.status}</div>`;
    } else if (ping) {
      badge = ping.online
        ? `<div class="badge online"><span class="dot"></span>online</div>`
        : `<div class="badge offline"><span class="dot"></span>offline</div>`;
    }
    const etapa = prog && prog.etapa ? `<div class="pdv-etapa">${prog.etapa}</div>` : "";
    const erroMsg = prog && prog.status === "error" && prog.erro
      ? `<div class="pdv-etapa" style="color:var(--red);font-size:10px;word-break:break-all;">${prog.erro}</div>` : "";
    const bar = prog && prog.progresso != null
      ? `<div class="pdv-progress"><div class="bar-wrap"><div class="bar" style="width:${prog.progresso}%"></div></div>${etapa}${erroMsg}</div>`
      : "";
    const versaoPdv = key === "pdv" && pdv.versao ? `<div class="pdv-versao">v${pdv.versao}</div>` : "";
    const versaoAgente = key === "agente"
      ? `<div class="pdv-versao-agente">agente v${ping && ping.versao_agente ? ping.versao_agente : "—"}</div>`
      : "";
    const ecfNum = parseInt(pdv.id.replace("PDV-", ""), 10);
    const mInfo = _modelosPorEcf[ecfNum];
    const modelTag = mInfo ? tagModelo(mInfo.modelo_id, mInfo.modelo) : "";
    return `
      <div class="pdv-card ${sel ? 'selected' : ''}" onclick="togglePDV('${key}','${pdv.id}')">
        <div class="pdv-name">${pdv.nome || pdv.id}</div>
        <div class="pdv-ip">${pdv.ip}</div>
        ${modelTag}
        ${versaoPdv}
        ${badge}
        ${versaoAgente}
        ${bar}
      </div>
    `;
  }).join("");
}

function togglePDV(key, pdvId) {
  const sel = seletores[key].selecionados;
  if (key === "pdv") {
    // trava: apenas um PDV por vez nesta tela, para permitir checagem de versao segura
    seletores[key].selecionados = sel.has(pdvId) ? new Set() : new Set([pdvId]);
  } else {
    if (sel.has(pdvId)) sel.delete(pdvId); else sel.add(pdvId);
  }
  renderPDVs(key);
  if (key === "pdv") atualizarBotaoPdv();
  if (key === "agente") atualizarBotaoAgente();
}
window.togglePDV = togglePDV;

function toggleSelecionarTodos(key) {
  const loja = lojas.find(l => l.id === seletores[key].lojaAtiva);
  if (!loja) return;
  const todosIds = loja.pdvs.map(p => p.id);
  const todosSelecionados = todosIds.every(id => seletores[key].selecionados.has(id));
  seletores[key].selecionados = todosSelecionados ? new Set() : new Set(todosIds);
  renderPDVs(key);
  if (key === "pdv") atualizarBotaoPdv();
  if (key === "agente") atualizarBotaoAgente();
}
window.toggleSelecionarTodos = toggleSelecionarTodos;

async function verificarOnline(key) {
  const loja = lojas.find(l => l.id === seletores[key].lojaAtiva);
  if (!loja) return;
  loja.pdvs.forEach(pdv => { seletores[key].ping[pdv.id] = { online: false, checking: true }; });
  renderPDVs(key);
  try {
    const r = await fetch(API(`/ping_loja/${loja.id}`));
    const dados = await r.json();
    for (const pdv of loja.pdvs) {
      seletores[key].ping[pdv.id] = dados[pdv.id] || { online: false };
    }
  } catch (e) { /* mantem offline */ }
  renderPDVs(key);
}
window.verificarOnline = verificarOnline;

async function carregarLojas() {
  const r = await fetch(API("/lojas"));
  lojas = await r.json();
  _cacheLojasSalvar(lojas);
  for (const key of KEYS) renderLojaTabs(key);
}

async function redescobrir() {
  await fetch(API("/lojas/atualizar"), { method: "POST" });
  await carregarLojas();
}
window.redescobrir = redescobrir;

// ──────────────────────────────────────────────
// ATUALIZACAO DE AGENTE (view "agente")
// ──────────────────────────────────────────────
async function verificarAgenteDisponivel() {
  const r = await fetch(API("/versao_agente"));
  const dados = await r.json();
  const badge = document.getElementById("agenteBadge");
  const info = document.getElementById("agenteInfoTexto");
  if (!badge || !info) return;
  if (dados.disponivel) {
    badge.className = "agente-badge disponivel";
    badge.textContent = "disponível";
    info.textContent = `agente.exe (${dados.tamanho_mb} MB) — enviado em ${dados.data}`;
  } else {
    badge.className = "agente-badge indisponivel";
    badge.textContent = "indisponível";
    info.textContent = "Nenhum agente.exe enviado ainda.";
  }
}

async function uploadAgente(input) {
  const arquivo = input.files[0];
  if (!arquivo) return;
  const btn = document.getElementById("btnUploadAgente");
  const info = document.getElementById("agenteInfoTexto");
  if (btn) { btn.disabled = true; btn.textContent = `⏳ Enviando ${(arquivo.size / 1048576).toFixed(1)} MB...`; }
  if (info) info.textContent = "Enviando agente.exe para o servidor...";
  const fd = new FormData();
  fd.append("arquivo", arquivo);
  await fetch(API("/upload_agente"), { method: "POST", body: fd });
  input.value = "";
  if (btn) { btn.disabled = false; btn.textContent = "Enviar novo agente.exe"; }
  await verificarAgenteDisponivel();
}
window.uploadAgente = uploadAgente;

async function uploadStatusPdv(input) {
  const arquivo = input.files[0];
  if (!arquivo) return;
  const btn = document.getElementById("btnUploadStatusPdv");
  const info = document.getElementById("statusPdvInfoTexto");
  if (btn) { btn.disabled = true; btn.textContent = `Enviando ${(arquivo.size / 1048576).toFixed(1)} MB...`; }
  if (info) info.textContent = "Enviando status_pdv.exe...";
  const fd = new FormData();
  fd.append("arquivo", arquivo);
  const r = await fetch(API("/upload_agente"), { method: "POST", body: fd });
  const dados = await r.json();
  input.value = "";
  if (btn) { btn.disabled = false; btn.textContent = "Enviar novo status_pdv.exe"; }
  if (info) info.textContent = dados.mensagem || dados.erro || "Concluido";
}
window.uploadStatusPdv = uploadStatusPdv;

function atualizarBotaoAgente() {
  const btn = document.getElementById("btnAtualizarAgente");
  if (!btn) return;
  btn.disabled = seletores.agente.selecionados.size === 0;
}

async function iniciarAtualizacaoAgente() {
  const loja_id = seletores.agente.lojaAtiva;
  const pdv_ids = Array.from(seletores.agente.selecionados);
  if (!loja_id || pdv_ids.length === 0) return;
  const btn = document.getElementById("btnAtualizarAgente");
  if (btn) { btn.disabled = true; btn.textContent = "⏳ Enviando agente para PDVs..."; }
  const r = await fetch(API("/atualizar_agente"), {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ loja_id, pdv_ids }),
  });
  const dados = await r.json();
  if (btn) { btn.disabled = false; btn.textContent = "🚀 Atualizar Agente nos PDVs Selecionados"; }
  if (dados.erro) { alert(`Erro: ${dados.erro}`); return; }
  const resultados = dados.resultados || {};
  const linhas = Object.entries(resultados)
    .map(([id, res]) => `${id}: ${res.ok ? "✅" : "❌"} ${res.msg || ""}`)
    .join("\n");
  alert(linhas || "Nenhum resultado retornado.");
}
window.iniciarAtualizacaoAgente = iniciarAtualizacaoAgente;

// ──────────────────────────────────────────────
// ATUALIZACAO DE PDV (view "pdv") — upload de .zip + envio
// ──────────────────────────────────────────────
async function fazerUpload(file) {
  const fd = new FormData();
  fd.append("arquivo", file);
  const progEl = document.getElementById("uploadProgress");
  if (progEl) progEl.style.display = "block";
  await fetch(API("/upload"), { method: "POST", body: fd });
  if (progEl) progEl.style.display = "none";
  await carregarArquivos();
}

async function carregarArquivos() {
  const r = await fetch(API("/arquivos"));
  const arquivos = await r.json();
  arquivosPorNome = {};
  arquivos.forEach(a => { arquivosPorNome[a.nome] = a; });
  const el = document.getElementById("fileList");
  if (!el) return;
  if (arquivos.length === 0) {
    el.innerHTML = '<div class="empty">Nenhum arquivo enviado ainda.</div>';
    return;
  }
  const podeLimpar = window.PERMS?.pode_atu_pdv_limpar;
  el.innerHTML = arquivos.map(a => `
    <div class="file-item ${a.nome === arquivoSelecionado ? 'selected' : ''}" onclick="selecionarArquivo('${a.nome}')">
      <div>
        <div class="file-name">${a.nome} ${a.versao ? `<span class="file-versao">v${a.versao}</span>` : ''}</div>
        <div class="file-meta text-muted">${a.tamanho_mb} MB — ${a.data}</div>
      </div>
      <div class="file-actions">
        ${podeLimpar ? `<button class="btn-del" onclick="event.stopPropagation(); deletarArquivo('${a.nome}')">🗑️</button>` : ''}
      </div>
    </div>
  `).join("");
}

let _pdvCompatResult = null;
let _pdvCompatVersaoChecked = null;

function selecionarArquivo(nome) {
  arquivoSelecionado = arquivoSelecionado === nome ? null : nome;
  carregarArquivos();
  const arquivo = arquivosPorNome[arquivoSelecionado];
  const versao = arquivo?.versao || null;
  if (versao !== _pdvCompatVersaoChecked) {
    _pdvCompatResult = null;
    _pdvCompatVersaoChecked = versao;
  }
  atualizarBotaoPdv();
  if (versao) _verificarCompatIntegrador(versao);
}
window.selecionarArquivo = selecionarArquivo;

async function _verificarCompatIntegrador(versao_pdvpro) {
  try {
    const d = await fetch(API(`/compat/verificar?versao_pdvpro=${encodeURIComponent(versao_pdvpro)}`))
      .then(r => r.json()).catch(() => null);
    if (_pdvCompatVersaoChecked === versao_pdvpro) {
      _pdvCompatResult = d ?? { ok: true, bloqueado: false };
      atualizarBotaoPdv();
    }
  } catch (_) {
    if (_pdvCompatVersaoChecked === versao_pdvpro) {
      _pdvCompatResult = { ok: true, bloqueado: false };
      atualizarBotaoPdv();
    }
  }
}

async function deletarArquivo(nome) {
  await fetch(API(`/arquivos/${encodeURIComponent(nome)}`), { method: "DELETE" });
  if (arquivoSelecionado === nome) arquivoSelecionado = null;
  await carregarArquivos();
}
window.deletarArquivo = deletarArquivo;

async function limparTodos() {
  if (!confirm("Remover todos os arquivos enviados?")) return;
  await fetch(API("/arquivos/limpar"), { method: "DELETE" });
  arquivoSelecionado = null;
  await carregarArquivos();
}
window.limparTodos = limparTodos;

function compararVersoes(v1, v2) {
  const t1 = v1.split(".").map(Number);
  const t2 = v2.split(".").map(Number);
  const max = Math.max(t1.length, t2.length);
  for (let i = 0; i < max; i++) {
    const a = t1[i] || 0, b = t2[i] || 0;
    if (a !== b) return a < b ? -1 : 1;
  }
  return 0;
}

function ehDowngrade(versaoZip, versaoPdvAtual) {
  if (!versaoZip || !versaoPdvAtual) return false;
  return compararVersoes(versaoZip, versaoPdvAtual) < 0;
}

function pdvSelecionadoAtual() {
  const loja = lojas.find(l => l.id === seletores.pdv.lojaAtiva);
  if (!loja) return null;
  const id = Array.from(seletores.pdv.selecionados)[0];
  return loja.pdvs.find(p => p.id === id) || null;
}

function atualizarBotaoPdv() {
  const btn = document.getElementById("btnAtualizarPdv");
  const aviso = document.getElementById("pdvVersaoAviso");
  if (!btn) return;

  const pdv = pdvSelecionadoAtual();
  const arquivo = arquivosPorNome[arquivoSelecionado];
  let bloqueadoPorVersao = false;
  let html = "";

  if (pdv && arquivo) {
    if (!arquivo.versao) {
      html += `<div class="card aviso-versao aviso-indefinido">⚠️ Não foi possível identificar a versão do arquivo <strong>${arquivo.nome}</strong> pelo nome. Renomeie incluindo a versão (ex: VRPdvPro_7.1.0.zip).</div>`;
    } else if (pdv.versao && ehDowngrade(arquivo.versao, pdv.versao)) {
      bloqueadoPorVersao = true;
      html += `<div class="card aviso-versao aviso-bloqueado">⛔ Downgrade bloqueado: o pacote é a versão <strong>${arquivo.versao}</strong>, mas o PDV ${pdv.id} já está na <strong>${pdv.versao}</strong>. Atualizar para uma versão anterior pode corromper o banco.</div>`;
    } else if (pdv.versao) {
      html += `<div class="card aviso-versao aviso-ok">✅ ${pdv.id}: ${pdv.versao} → ${arquivo.versao}</div>`;
    }
  }

  // Resultado da verificação de compatibilidade com integrador
  if (arquivo?.versao) {
    if (_pdvCompatVersaoChecked === arquivo.versao && _pdvCompatResult) {
      const c = _pdvCompatResult;
      if (c.bloqueado) {
        bloqueadoPorVersao = true;
        html += `<div class="card aviso-versao aviso-bloqueado">⛔ Integrador incompatível: ${c.aviso}</div>`;
      } else if (!c.ok && c.aviso) {
        html += `<div class="card aviso-versao aviso-indefinido">⚠️ ${c.aviso}</div>`;
      } else if (c.ok && c.versao_min) {
        html += `<div class="card aviso-versao aviso-ok">✅ Integrador compatível — atual: ${c.versao_atual || "?"}, mínimo: ${c.versao_min}</div>`;
      } else if (c.ok && !c.versao_min && !c.erro) {
        html += `<div class="card aviso-versao aviso-neutro" style="opacity:.7">ℹ️ Tabela de compatibilidade indisponível para v${arquivo.versao} — verifique o integrador manualmente.</div>`;
      }
    } else if (_pdvCompatVersaoChecked === arquivo.versao && !_pdvCompatResult) {
      bloqueadoPorVersao = true;
      html += `<div class="card aviso-versao aviso-indefinido" style="opacity:.7">⏳ Verificando compatibilidade com o integrador...</div>`;
    }
  }

  if (aviso) aviso.innerHTML = html;
  btn.disabled = !arquivoSelecionado || seletores.pdv.selecionados.size === 0 || bloqueadoPorVersao;
}

async function iniciarAtualizacao() {
  const loja_id = seletores.pdv.lojaAtiva;
  const pdv_ids = Array.from(seletores.pdv.selecionados);
  if (!loja_id || !arquivoSelecionado || pdv_ids.length === 0) return;

  const r = await fetch(API("/atualizar"), {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      loja_id, pdv_ids, arquivo: arquivoSelecionado,
      versao_integrador: _pdvCompatResult?.versao_atual ?? null,
    }),
  });
  const dados = await r.json();
  if (dados.erro) { alert(dados.erro); return; }

  if (eventSource) eventSource.close();
  eventSource = new EventSource(API(`/status_stream/${loja_id}`));
  eventSource.onmessage = (ev) => {
    const dados = JSON.parse(ev.data);
    statusPDVs = dados;
    renderPDVs("pdv");
  };
}
window.iniciarAtualizacao = iniciarAtualizacao;

function configurarDropZone() {
  const area = document.getElementById("uploadArea");
  const input = document.getElementById("uploadInput");
  if (!area || !input) return;
  area.addEventListener("click", () => input.click());
  input.addEventListener("change", () => { if (input.files[0]) fazerUpload(input.files[0]); });
  area.addEventListener("dragover", (e) => { e.preventDefault(); area.classList.add("selected"); });
  area.addEventListener("dragleave", () => area.classList.remove("selected"));
  area.addEventListener("drop", (e) => {
    e.preventDefault();
    area.classList.remove("selected");
    if (e.dataTransfer.files[0]) fazerUpload(e.dataTransfer.files[0]);
  });
}

// ──────────────────────────────────────────────
// REPLICACAO (view "replicacao")
// ──────────────────────────────────────────────
function linhaColecao(pdvId, nome, c) {
  if (c.erro) {
    return `<div class="empty" style="text-align:left;color:#dc2626;">⚠️ ${nome}: ${c.erro}</div>`;
  }
  if (!c.tem_divergencia) {
    return `<div class="text-muted" style="text-decoration:line-through;opacity:.5;font-size:12px;">✔ ${nome} — sem divergência</div>`;
  }
  const link = `/r/${window.REDE_ID}/replicacao/detalhe/${seletores.replicacao.lojaAtiva}/${pdvId}/${nome}`;
  return `
    <div style="font-size:12px;">
      <strong>⚠️ ${nome}</strong> —
      faltando: ${c.faltando_no_pdv_total || 0},
      extras: ${c.extras_no_pdv_total || 0},
      alterados: ${c.alterados_total || 0}
      — <a href="${link}" target="_blank" style="color:var(--vr-orange);">ver detalhe</a>
    </div>
  `;
}

function atualizarBlocoReplicacao(pdvId, dados) {
  const el = document.getElementById(`repResultado-${pdvId}`);
  if (!el) return;
  if (dados.status === "erro") {
    el.innerHTML = `
      <div class="empty" style="color:#dc2626;">Erro: ${dados.erro}</div>
      <button class="btn-verify" onclick="reiniciarMongoPdv('${pdvId}')">🔄 Reiniciar Mongo do PDV</button>
      <div id="reiniciarMongoResultado-${pdvId}" style="margin-top:6px;font-size:12px;"></div>
    `;
    return;
  }
  const colecoes = (dados.resultado || {}).colecoes || {};
  const linhas = Object.entries(colecoes).map(([nome, c]) => linhaColecao(pdvId, nome, c)).join("");
  const status = dados.status === "executando"
    ? '<div class="text-muted" style="font-size:11px;">Verificando...</div>'
    : '<div class="text-muted" style="font-size:11px;">Concluído</div>';
  el.innerHTML = linhas + status;
}

let _tabelasDisponiveis = [];

async function abrirSeletorTabelas() {
  const loja_id = seletores.replicacao.lojaAtiva;
  const pdv_ids = Array.from(seletores.replicacao.selecionados);
  if (!loja_id || pdv_ids.length === 0) return;

  if (_tabelasDisponiveis.length === 0) {
    const r = await fetch(API("/replicacao/tabelas"));
    _tabelasDisponiveis = await r.json();
  }

  const lista = document.getElementById("listaTabelas");
  lista.innerHTML = _tabelasDisponiveis.map(t => `
    <label style="display:flex;align-items:center;gap:8px;font-size:12.5px;cursor:pointer;padding:4px 6px;border-radius:6px;" class="check-list-item">
      <input type="checkbox" class="tab-check" value="${t}" checked style="accent-color:var(--accent);width:14px;height:14px;">
      ${t}
    </label>
  `).join("");

  document.getElementById("tabMarcaTodas").checked = true;
  document.getElementById("modalTabelas").style.display = "flex";
}

function fecharSeletorTabelas() {
  document.getElementById("modalTabelas").style.display = "none";
}

function toggleTodasTabelas(marcado) {
  document.querySelectorAll(".tab-check").forEach(cb => { cb.checked = marcado; });
}

async function confirmarVerificacaoTabelas() {
  const tabelas = Array.from(document.querySelectorAll(".tab-check:checked")).map(cb => cb.value);
  if (tabelas.length === 0) { alert("Selecione ao menos uma tabela."); return; }

  fecharSeletorTabelas();

  const loja_id = seletores.replicacao.lojaAtiva;
  const pdv_ids = Array.from(seletores.replicacao.selecionados);
  const tabelasFiltro = tabelas.length === _tabelasDisponiveis.length ? null : tabelas;

  const r = await fetch(API("/replicacao/verificar"), {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ loja_id, pdv_ids, tabelas: tabelasFiltro }),
  });
  const dados = await r.json();
  if (dados.erro) { alert(dados.erro); return; }

  const cont = document.getElementById("repResultados");
  if (cont) {
    cont.innerHTML = pdv_ids.map(id => `
      <div class="card" style="margin-top:8px;">
        <div class="section-title">PDV ${id}</div>
        <div id="repResultado-${id}"><div class="text-muted">Iniciando...</div></div>
      </div>
    `).join("");
  }

  if (pollReplicacaoTimer) clearInterval(pollReplicacaoTimer);
  pollReplicacaoTimer = setInterval(() => pollReplicacao(pdv_ids), 1500);
}

async function verificarReplicacaoSelecionados() {
  await abrirSeletorTabelas();
}
window.verificarReplicacaoSelecionados = verificarReplicacaoSelecionados;
window.abrirSeletorTabelas = abrirSeletorTabelas;

async function pollReplicacao(pdvIds) {
  const loja_id = seletores.replicacao.lojaAtiva;
  let todosConcluidos = true;
  for (const pdvId of pdvIds) {
    const r = await fetch(API(`/replicacao/status/${loja_id}/${pdvId}`));
    const dados = await r.json();
    atualizarBlocoReplicacao(pdvId, dados);
    if (dados.status === "executando" || dados.status === "idle") todosConcluidos = false;
  }
  if (todosConcluidos && pollReplicacaoTimer) {
    clearInterval(pollReplicacaoTimer);
    pollReplicacaoTimer = null;
    carregarHistoricoReplicacao();
  }
}

async function reiniciarMongoPdv(pdvId) {
  const loja_id = seletores.replicacao.lojaAtiva;
  const out = document.getElementById(`reiniciarMongoResultado-${pdvId}`);
  if (out) out.innerHTML = '<span class="text-muted">Reiniciando o Mongo do PDV, aguarde (pode levar ~30s)...</span>';

  try {
    const r = await fetch(API(`/pdv/${loja_id}/${pdvId}/reiniciar_mongo`), { method: "POST" });
    const dados = await r.json();
    if (out) {
      out.innerHTML = dados.ok
        ? `<span style="color:#16a34a;">✅ ${dados.mensagem}</span>`
        : `<span style="color:#dc2626;">⛔ ${dados.erro}</span>`;
    }
  } catch (e) {
    if (out) out.innerHTML = `<span style="color:#dc2626;">⛔ Falha ao contatar o servidor: ${e}</span>`;
  }
}
window.reiniciarMongoPdv = reiniciarMongoPdv;

async function carregarConfigReplicacaoAuto() {
  const r = await fetch(API("/replicacao/config"));
  const cfg = await r.json();
  const hab = document.getElementById("repAutoHabilitado");
  const intervalo = document.getElementById("repAutoIntervalo");
  if (hab) hab.checked = !!cfg.habilitado;
  if (intervalo) intervalo.value = cfg.intervalo_minutos || 60;
}

async function salvarConfigReplicacaoAuto() {
  const hab = document.getElementById("repAutoHabilitado");
  const intervalo = document.getElementById("repAutoIntervalo");
  await fetch(API("/replicacao/config"), {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      habilitado: hab ? hab.checked : false,
      intervalo_minutos: intervalo ? parseInt(intervalo.value, 10) : 60,
      pdvs: "todos",
    }),
  });
}
window.salvarConfigReplicacaoAuto = salvarConfigReplicacaoAuto;

async function carregarHistoricoReplicacao() {
  const r = await fetch(API("/replicacao/historico"));
  const historico = await r.json();
  const el = document.getElementById("repHistorico");
  if (!el) return;
  if (historico.length === 0) {
    el.innerHTML = '<div class="empty">Nenhuma verificação registrada ainda.</div>';
    return;
  }
  el.innerHTML = historico.slice(0, 10).map(h => `
    <div class="text-muted" style="font-size:12px;padding:4px 0;">
      ${h.tem_divergencia ? '⚠️' : '✔'} ${h.timestamp} — ${h.tipo}
      ${h.tem_divergencia ? '<strong style="color:#dc2626;">divergência encontrada</strong>' : 'sem divergência'}
    </div>
  `).join("");
}

// ──────────────────────────────────────────────
// CONFIGURACOES: BANCO DE DADOS DO ERP (PostgreSQL)
// ──────────────────────────────────────────────
async function carregarConfigErpDb() {
  const r = await fetch(API("/erp_db/config"));
  const cfg = await r.json();
  const host = document.getElementById("erpDbHost");
  const porta = document.getElementById("erpDbPorta");
  const usuario = document.getElementById("erpDbUsuario");
  const senha = document.getElementById("erpDbSenha");
  const banco = document.getElementById("erpDbBanco");
  if (host) host.value = cfg.host || "";
  if (porta) porta.value = cfg.porta || 5432;
  if (usuario) usuario.value = cfg.usuario || "";
  if (senha) senha.value = cfg.senha || "";
  if (banco) banco.value = cfg.banco || "";
}

async function salvarConfigErpDb() {
  const dados = {
    host: document.getElementById("erpDbHost").value.trim(),
    porta: parseInt(document.getElementById("erpDbPorta").value, 10) || 5432,
    usuario: document.getElementById("erpDbUsuario").value.trim(),
    senha: document.getElementById("erpDbSenha").value,
    banco: document.getElementById("erpDbBanco").value.trim(),
  };
  await fetch(API("/erp_db/config"), {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(dados),
  });
  const el = document.getElementById("erpDbResultado");
  if (el) { el.textContent = "Configuração salva."; el.style.color = ""; }
}
window.salvarConfigErpDb = salvarConfigErpDb;

async function testarConexaoErpDb() {
  const el = document.getElementById("erpDbResultado");
  if (el) { el.textContent = "Testando conexão..."; el.style.color = ""; }
  const r = await fetch(API("/erp_db/status"));
  const dados = await r.json();
  if (el) {
    el.textContent = dados.online ? "✔ Conexão bem-sucedida." : `✖ Falha na conexão: ${dados.erro || "erro desconhecido"}`;
    el.style.color = dados.online ? "#16a34a" : "#dc2626";
  }
  atualizarKpiErpDb(dados);
}
window.testarConexaoErpDb = testarConexaoErpDb;

// ──────────────────────────────────────────────
// METRICAS DE SAUDE DA MAQUINA (sysinfo + erp stats)
// ──────────────────────────────────────────────
async function _carregarLojasErp() {
  const el = document.getElementById("kpiLojasLista");
  if (!el) return;
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 10000); // 10s timeout
  try {
    const dados = await fetch(API("/erp_db/lojas"), { signal: ctrl.signal })
      .then(r => r.json());
    clearTimeout(timer);
    if (dados.erro || !dados.lojas || dados.lojas.length === 0) {
      el.innerHTML = "";
      return;
    }
    el.innerHTML = dados.lojas.map(l => `
      <div class="kpi-loja-item">
        <div class="kpi-loja-nome">${l.nome || l.apelido || "—"}</div>
        ${l.apelido ? `<div class="kpi-loja-apelido">📍 ${l.apelido}</div>` : ""}
        ${l.cnpj ? `<div class="kpi-loja-cnpj">${_formatarCnpj(l.cnpj)}</div>` : ""}
      </div>`).join("");
  } catch (e) {
    clearTimeout(timer);
    el.innerHTML = "";
  }
}

function _formatarCnpj(cnpj) {
  const s = String(cnpj).replace(/\D/g, "").padStart(14, "0");
  return s.length === 14
    ? `${s.slice(0,2)}.${s.slice(2,5)}.${s.slice(5,8)}/${s.slice(8,12)}-${s.slice(12)}`
    : cnpj;
}

function _formatarUptime(seg) {
  const d = Math.floor(seg / 86400);
  const h = Math.floor((seg % 86400) / 3600);
  const m = Math.floor((seg % 3600) / 60);
  if (d > 0) return `${d}d ${h}h`;
  if (h > 0) return `${h}h ${m}min`;
  return `${m}min`;
}

async function _tickSysinfo() {
  const dados = await fetch(API("/sysinfo")).then(r => r.json()).catch(() => null);
  const el = document.getElementById("kpiSysinfo");
  if (!el || !dados || dados.erro) return;
  const agora = new Date().toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  el.innerHTML = renderSysinfo(dados, agora);
}

function _iniciarPolingSysinfo() {
  _pararPolingSysinfo();
  _sysinfoTimer = setInterval(_tickSysinfo, _SYSINFO_INTERVALO_MS);
}

function _pararPolingSysinfo() {
  if (_sysinfoTimer) { clearInterval(_sysinfoTimer); _sysinfoTimer = null; }
}

function renderSysinfoMini(dados) {
  if (!dados || dados.erro) return "";
  const bar = (pct) => {
    const cor = pct < 70 ? "var(--green)" : pct < 85 ? "var(--amber)" : "var(--red)";
    return `<div class="pdv-sysinfo-bar-wrap"><div class="pdv-sysinfo-bar" style="width:${Math.min(pct,100)}%;background:${cor};"></div></div>`;
  };
  const row = (label, pct) => `<div class="pdv-sysinfo-row"><span class="pdv-sysinfo-label">${label}</span>${bar(pct)}<span class="pdv-sysinfo-pct">${pct}%</span></div>`;
  return `<div class="pdv-sysinfo-mini">${row("CPU", dados.cpu_pct)}${row("RAM", dados.mem_pct)}${row("Disk", dados.disco_pct)}</div>`;
}

async function _tickSysinfoLoja(lojaId, pdvIds) {
  const dados = await fetch(API(`/sysinfo_loja/${lojaId}`)).then(r => r.json()).catch(() => ({}));
  for (const pdvId of pdvIds) {
    const el = document.getElementById(`pdvsysinfo-${pdvId}`);
    if (el && dados[pdvId] && !dados[pdvId].erro) el.innerHTML = renderSysinfoMini(dados[pdvId]);
  }
}

function _iniciarPolingSysinfoLojas(lojasCruzadas) {
  _pararPolingSysinfoLojas();
  for (const loja of lojasCruzadas) {
    const lojaId = `loja${String(loja.id_loja).padStart(2, "0")}`;
    const pdvsOnline = loja.pdvs.filter(p => p.status === "online").map(p => p.pdvId);
    if (pdvsOnline.length === 0) continue;
    _tickSysinfoLoja(lojaId, pdvsOnline);
    _sysinfoLoja[lojaId] = setInterval(() => _tickSysinfoLoja(lojaId, pdvsOnline), _SYSINFO_PDV_INTERVALO_MS);
  }
}

function _pararPolingSysinfoLojas() {
  for (const timer of Object.values(_sysinfoLoja)) clearInterval(timer);
  _sysinfoLoja = {};
}

function renderSysinfo(dados, timestamp) {
  if (!dados || dados.erro) return "";
  const bar = (pct) => {
    const cor = pct < 70 ? "var(--green)" : pct < 85 ? "var(--amber)" : "var(--red)";
    return `<div class="kpi-metrica-bar" style="width:${Math.min(pct,100)}%;background:${cor};"></div>`;
  };
  const row = (label, pct, extra) => `
    <div class="kpi-metrica-row">
      <span class="kpi-metrica-label">${label}</span>
      <div class="kpi-metrica-bar-wrap">${bar(pct)}</div>
      <span class="kpi-metrica-pct">${pct}%</span>
      ${extra ? `<span style="font-size:10px;color:var(--text-faint);white-space:nowrap;">${extra}</span>` : ""}
    </div>`;
  const memExtra = `${dados.mem_usado_mb >= 1024 ? (dados.mem_usado_mb/1024).toFixed(1)+"GB" : dados.mem_usado_mb+"MB"}/${dados.mem_total_mb >= 1024 ? (dados.mem_total_mb/1024).toFixed(0)+"GB" : dados.mem_total_mb+"MB"}`;
  const diskExtra = `${dados.disco_usado_gb}/${dados.disco_total_gb}GB`;
  return `<div class="kpi-metricas">
    ${row("CPU", dados.cpu_pct)}
    ${row("RAM", dados.mem_pct, memExtra)}
    ${row("Disk", dados.disco_pct, diskExtra)}
    ${dados.uptime_seg ? `<div class="kpi-metrica-uptime">⏱ Uptime: ${_formatarUptime(dados.uptime_seg)}</div>` : ""}
    ${timestamp ? `<div class="kpi-metrica-uptime" style="margin-top:4px;opacity:.6;">🔄 ${timestamp}</div>` : ""}
  </div>`;
}

function renderErpStats(dados) {
  if (!dados || dados.erro) return "";
  return `<div class="kpi-metricas">
    <div class="kpi-stat-row"><span>Tamanho do BD</span><span>${dados.tamanho_bd}</span></div>
    <div class="kpi-stat-row"><span>Conexões ativas</span><span>${dados.conexoes_ativas}</span></div>
    <div class="kpi-stat-row"><span>Versão PostgreSQL</span><span>${dados.versao}</span></div>
  </div>`;
}

// ──────────────────────────────────────────────
// CONFIGURACOES: INTEGRADOR VR
// ──────────────────────────────────────────────
async function carregarConfigIntegrador() {
  const r = await fetch(API("/integrador/config"));
  const cfg = await r.json();
  const ip = document.getElementById("integradorIp");
  const porta = document.getElementById("integradorPorta");
  const mongoIp = document.getElementById("integradorMongoIp");
  const mongoPorta = document.getElementById("integradorMongoPorta");
  if (ip) ip.value = cfg.ip || "";
  if (porta) porta.value = cfg.porta || "";
  if (mongoIp) mongoIp.value = cfg.mongo_ip || "";
  if (mongoPorta) mongoPorta.value = cfg.mongo_porta || 27016;
  _preencherConfigIntegradorSsh(cfg);
}

async function salvarConfigIntegrador() {
  const dados = {
    ip: document.getElementById("integradorIp").value.trim(),
    porta: parseInt(document.getElementById("integradorPorta").value, 10) || 0,
    mongo_ip: document.getElementById("integradorMongoIp").value.trim(),
    mongo_porta: parseInt(document.getElementById("integradorMongoPorta").value, 10) || 27016,
  };
  await fetch(API("/integrador/config"), {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(dados),
  });
  const el = document.getElementById("integradorResultado");
  if (el) { el.textContent = "Configuração salva."; el.style.color = ""; }
}
window.salvarConfigIntegrador = salvarConfigIntegrador;

function textoStatusIntegrador(dados) {
  if (dados.status === "ok") return "✔ Integrador online e replicando normalmente.";
  if (dados.status === "atencao") return `⚠️ ${dados.erro || "Replicação pode estar parada."}`;
  if (dados.status === "erro") return `✖ ${dados.erro || "Integrador com erro."}`;
  if (dados.status === "offline") return `✖ ${dados.erro || "Integrador offline."}`;
  return "Integrador ainda não configurado.";
}

function corStatusIntegrador(status) {
  if (status === "ok") return "#16a34a";
  if (status === "atencao") return "#d97706";
  return "#dc2626";
}

async function testarStatusIntegrador() {
  const el = document.getElementById("integradorResultado");
  const colEl = document.getElementById("integradorColecoes");
  if (el) { el.textContent = "Verificando..."; el.style.color = ""; }
  const dados = await fetch(API("/integrador/status")).then(r => r.json());
  if (el) { el.textContent = textoStatusIntegrador(dados); el.style.color = corStatusIntegrador(dados.status); }
  if (colEl) {
    const linhas = Object.entries(dados.colecoes || {});
    colEl.innerHTML = linhas.map(([nome, info]) => `
      <div class="text-muted" style="font-size:11px;padding:2px 0;">
        ${nome}: ${info.total} documento(s) — última inserção ${info.ultima_insercao || "—"}
      </div>
    `).join("");
  }
  atualizarKpiIntegrador(dados);
}
window.testarStatusIntegrador = testarStatusIntegrador;


// ──────────────────────────────────────────────
// DASHBOARD
// ──────────────────────────────────────────────
// Classifica cada PDV de uma entrada do historico em ok / divergente / erro,
// para o grafico empilhado mostrar a composicao real de cada execucao (e nao
// so "teve ou nao divergencia").
function _composicaoExecucao(entrada) {
  const pdvs = Object.values(entrada.pdvs || {});
  let ok = 0, divergente = 0, erro = 0;
  for (const p of pdvs) {
    if (!p.ok) erro++;
    else if (p.tem_divergencia) divergente++;
    else ok++;
  }
  return { ok, divergente, erro, total: pdvs.length };
}

function svgGraficoBarras(historico) {
  const ultimos = historico.slice(0, 12).reverse();
  if (ultimos.length === 0) {
    return '<div class="empty">Sem histórico suficiente para o gráfico ainda. Rode uma verificação manual ou habilite a automática.</div>';
  }

  const W = 560, H = 180, padTop = 10, padBottom = 36, padX = 10;
  const composicoes = ultimos.map(_composicaoExecucao);
  const max = Math.max(1, ...composicoes.map(c => c.total));
  const alturaMax = H - padTop - padBottom;
  const larguraBarra = (W - padX * 2) / ultimos.length;
  const corOk = "#16a34a", corDivergente = "#d97706", corErro = "#dc2626";

  const barras = ultimos.map((h, i) => {
    const c = composicoes[i];
    const x = padX + i * larguraBarra + 3;
    const larguraReal = larguraBarra - 6;
    let yAtual = H - padBottom;
    const segmentos = [
      [c.ok, corOk, "sem divergência"],
      [c.divergente, corDivergente, "com divergência"],
      [c.erro, corErro, "erro na verificação"],
    ];
    const rects = segmentos.map(([qtd, cor, rotulo]) => {
      if (qtd === 0) return "";
      const altura = (qtd / max) * alturaMax;
      yAtual -= altura;
      return `<rect x="${x}" y="${yAtual.toFixed(1)}" width="${larguraReal}" height="${altura.toFixed(1)}" fill="${cor}"><title>${h.timestamp} (${h.tipo}): ${qtd} PDV(s) ${rotulo}</title></rect>`;
    }).join("");
    const rotuloX = x + larguraReal / 2;
    const dataResumida = h.timestamp.slice(5, 16).replace(" ", "\n");
    return `
      ${rects}
      <text x="${rotuloX}" y="${H - padBottom + 14}" font-size="9" fill="currentColor" opacity=".65" text-anchor="middle">${h.timestamp.slice(5, 10)}</text>
      <text x="${rotuloX}" y="${H - padBottom + 25}" font-size="9" fill="currentColor" opacity=".65" text-anchor="middle">${h.timestamp.slice(11, 16)}</text>
    `;
  }).join("");

  // Painel de insights: tendencia geral + ranking dos PDVs que mais divergem
  // no recorte analisado -- e a parte que da valor de analise, alem do grafico.
  const totalExecucoes = historico.length;
  const comDivergencia = historico.filter(h => h.tem_divergencia).length;
  const taxaDivergencia = totalExecucoes ? Math.round((comDivergencia / totalExecucoes) * 100) : 0;

  const contagemPorPdv = {};
  for (const h of historico) {
    for (const [pdvId, info] of Object.entries(h.pdvs || {})) {
      if (info.tem_divergencia) contagemPorPdv[pdvId] = (contagemPorPdv[pdvId] || 0) + 1;
    }
  }
  const ranking = Object.entries(contagemPorPdv).sort((a, b) => b[1] - a[1]).slice(0, 5);
  const rankingHtml = ranking.length > 0
    ? ranking.map(([pdvId, qtd]) => `
        <div class="dash-ranking-item">
          <span>${pdvId}</span>
          <span class="text-muted">${qtd}x divergiu</span>
        </div>
      `).join("")
    : '<div class="text-muted" style="font-size:12px;">Nenhuma divergência registrada no histórico.</div>';

  return `
    <div class="chart-wrap">
      <svg width="100%" height="${H}" viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMinYMid meet">${barras}</svg>
      <div class="chart-legend">
        <span><span class="dot" style="background:${corOk};"></span> sem divergência</span>
        <span><span class="dot" style="background:${corDivergente};"></span> com divergência</span>
        <span><span class="dot" style="background:${corErro};"></span> erro na verificação</span>
      </div>
    </div>
    <div class="dash-insights">
      <div class="dash-stat">
        <div class="dash-stat-valor">${totalExecucoes}</div>
        <div class="dash-stat-label">verificações no histórico</div>
      </div>
      <div class="dash-stat">
        <div class="dash-stat-valor" style="color:${taxaDivergencia > 0 ? corDivergente : corOk};">${taxaDivergencia}%</div>
        <div class="dash-stat-label">execuções com divergência</div>
      </div>
      <div class="dash-ranking">
        <div class="dash-stat-label" style="margin-bottom:6px;">PDVs que mais divergem</div>
        ${rankingHtml}
      </div>
    </div>
  `;
}

function ultimaReplicacaoPorPdv(historico) {
  const mapa = {};
  for (const entrada of historico) {
    for (const [pdvId, info] of Object.entries(entrada.pdvs || {})) {
      if (!(pdvId in mapa)) {
        mapa[pdvId] = { timestamp: entrada.timestamp, ok: info.ok, tem_divergencia: info.tem_divergencia };
      }
    }
  }
  return mapa;
}

function cardReplicacaoPdv(ult) {
  if (!ult) return '<div class="dash-pdv-replicacao semdados">Sem verificação registrada</div>';
  if (!ult.ok) return `<div class="dash-pdv-replicacao divergente">Erro na última verificação — ${ult.timestamp}</div>`;
  return ult.tem_divergencia
    ? `<div class="dash-pdv-replicacao divergente">⚠️ Divergência — ${ult.timestamp}</div>`
    : `<div class="dash-pdv-replicacao ok">✔ Sem divergência — ${ult.timestamp}</div>`;
}

// Cruza a "fonte da verdade" (PDVs cadastrados ativos no ERP) com o que o sistema
// efetivamente descobriu/conseguiu pingar via MongoDB+agente. Um PDV pode estar
// ativo no cadastro e mesmo assim nao aparecer aqui como online -- depende de
// estar ligado, na rede e com o agente instalado, o que o cadastro nao garante.
function cruzarLojasErpComOnline(lojasErp, lojasDescoberta, statusPorLoja) {
  return lojasErp.map(grupo => {
    const lojaId = `loja${String(grupo.id_loja).padStart(2, "0")}`;
    const lojaDescoberta = lojasDescoberta.find(l => l.id === lojaId);
    const ping = statusPorLoja[lojaId] || {};
    const pdvs = grupo.pdvs.map(pdvErp => {
      const ecf = typeof pdvErp === "object" ? pdvErp.ecf : pdvErp;
      const modelo_id = typeof pdvErp === "object" ? pdvErp.modelo_id : null;
      const modelo = typeof pdvErp === "object" ? pdvErp.modelo : null;
      const pdvId = `PDV-${ecf}`;
      const pdvDescoberto = lojaDescoberta ? lojaDescoberta.pdvs.find(p => p.id === pdvId) : null;
      const statusPing = ping[pdvId];
      let status;
      if (!pdvDescoberto) status = "sem_comunicacao";
      else if (statusPing && statusPing.online) status = "online";
      else status = "offline";
      return { ecf, pdvId, pdv: pdvDescoberto, status, modelo_id, modelo };
    });
    return { id_loja: grupo.id_loja, nome: grupo.loja || lojaId, pdvs };
  });
}

function rotuloStatusPdv(status) {
  if (status === "online") return { texto: "online", classe: "online" };
  if (status === "offline") return { texto: "offline", classe: "offline" };
  return { texto: "sem comunicação", classe: "offline" };
}

function renderLojasEPdvs(lojasCruzadas, ultimaPorPdv, erroErp, ocultarOffline) {
  if (erroErp) {
    return `<div class="empty">Não foi possível consultar os PDVs ativos no ERP: ${erroErp}</div>`;
  }
  if (lojasCruzadas.length === 0) {
    return '<div class="empty">Nenhum PDV ativo cadastrado no ERP.</div>';
  }
  return lojasCruzadas.map(loja => {
    const online = loja.pdvs.filter(p => p.status === "online").length;
    const total = loja.pdvs.length;
    const pdvsExibidos = ocultarOffline ? loja.pdvs.filter(p => p.status === "online") : loja.pdvs;
    const cards = pdvsExibidos.map(p => {
      const { texto, classe } = rotuloStatusPdv(p.status);
      const nome = p.pdv && p.pdv.nome ? p.pdv.nome : p.pdvId;
      const ip = p.pdv ? `<div class="pdv-ip">${p.pdv.ip}</div>` : '<div class="pdv-ip text-muted">IP desconhecido</div>';
      const versao = p.pdv && p.pdv.versao ? `<div class="pdv-versao">v${p.pdv.versao}</div>` : "";
      const replicacao = p.status === "online" ? cardReplicacaoPdv(ultimaPorPdv[p.pdvId]) : "";
      const sysinfoSlot = p.status === "online" ? `<div id="pdvsysinfo-${p.pdvId}"></div>` : "";
      const cat = categoriaModelo(p.modelo_id);
      const modelTag = tagModelo(p.modelo_id, p.modelo);
      return `
        <div class="pdv-card" style="cursor:default;border-left:3px solid ${cat.cor};">
          <div class="pdv-name">${nome}</div>
          ${ip}
          ${modelTag}
          ${versao}
          <div class="badge ${classe}"><span class="dot"></span>${texto}</div>
          ${replicacao}
          ${sysinfoSlot}
        </div>
      `;
    }).join("");
    return `
      <div class="dash-loja-grupo">
        <div class="dash-loja-titulo">${loja.nome} (${online}/${total} online — ${total} ativo(s) cadastrado(s) no ERP)</div>
        ${pdvsExibidos.length > 0 ? `<div class="dash-pdv-grid">${cards}</div>` : '<div class="empty">Nenhum PDV online nesta loja.</div>'}
      </div>
    `;
  }).join("");
}

let _dashUltimoEstado = null; // cache do ultimo cruzamento renderizado, para reagir ao checkbox sem novo fetch

function alternarOcultarOffline() {
  const checkbox = document.getElementById("dashOcultarOffline");
  const ocultar = checkbox ? checkbox.checked : false;
  localStorage.setItem("dashOcultarOffline", ocultar ? "1" : "0");
  const elOnline = document.getElementById("dashOnlinePorLoja");
  if (elOnline && _dashUltimoEstado) {
    elOnline.innerHTML = renderLojasEPdvs(
      _dashUltimoEstado.lojasCruzadas, _dashUltimoEstado.ultimaPorPdv, _dashUltimoEstado.erroErp, ocultar
    );
  }
}
window.alternarOcultarOffline = alternarOcultarOffline;

async function statusOnlinePorLoja(lojasList) {
  const resultados = await Promise.all(
    lojasList.map(l => {
      const ctrl = new AbortController();
      setTimeout(() => ctrl.abort(), 10000);
      return fetch(API(`/ping_loja/${l.id}`), { signal: ctrl.signal })
        .then(r => r.json()).catch(() => ({}));
    })
  );
  const mapa = {};
  lojasList.forEach((loja, i) => { mapa[loja.id] = resultados[i]; });
  return mapa;
}

async function carregarDashboard() {
  _dashMostrarLoading();

  // ── Fase 1: lojas (cache → instantâneo / fetch → até 3s) + histórico ─
  const lojasCache = _cacheLojasBuscar();
  const [lojasResp, historico] = await Promise.all([
    lojasCache ? Promise.resolve(lojasCache)
               : fetch(API("/lojas")).then(r => r.json()).catch(() => []),
    fetch(API("/replicacao/historico")).then(r => r.json()).catch(() => []),
  ]);

  // Atualiza cache em background se usamos o cache (stale-while-revalidate)
  if (lojasCache) {
    fetch(API("/lojas")).then(r => r.json()).then(d => { lojas = d; _cacheLojasSalvar(d); }).catch(() => {});
  } else {
    _cacheLojasSalvar(lojasResp);
  }

  lojas = lojasResp;
  _dashProgressoLoading(50, "Verificando ERP, integrador e PDVs online...");

  // Gráfico e última verificação — dados locais, sempre rápidos
  const grafico = document.getElementById("dashGrafico");
  if (grafico) grafico.innerHTML = svgGraficoBarras(historico);

  const elUltimaRow = document.getElementById("kpiUltimaVerifRow");
  if (elUltimaRow) {
    if (historico.length === 0) {
      elUltimaRow.innerHTML = "";
    } else {
      const div = historico[0].tem_divergencia;
      const cor = div ? "var(--amber)" : "var(--green)";
      const txt = div ? "⚠️ Com divergência" : "✔ Sem divergência";
      elUltimaRow.innerHTML = `<div class="kpi-integrador-verif" style="color:${cor}">${txt}</div>`;
    }
  }

  // KPIs básicos de lojas/PDVs (sem cruzamento ERP ainda)
  const elLojas = document.getElementById("kpiLojas");
  const elPdvs = document.getElementById("kpiPdvs");
  const elPdvsSub = document.getElementById("kpiPdvsSub");
  if (elLojas) elLojas.textContent = lojas.length;
  if (elPdvs) elPdvs.textContent = lojas.reduce((a, l) => a + l.pdvs.length, 0);
  if (elPdvsSub) elPdvsSub.textContent = "";

  // Placeholder enquanto os checks lentos rodam em background
  const ocultarOffline = localStorage.getItem("dashOcultarOffline") === "1";
  const checkbox = document.getElementById("dashOcultarOffline");
  if (checkbox) checkbox.checked = ocultarOffline;
  const elOnline = document.getElementById("dashOnlinePorLoja");
  if (elOnline) {
    elOnline.innerHTML = lojas.length === 0
      ? '<div class="empty">Nenhuma loja. O integrador pode estar offline.</div>'
      : lojas.map(l => `
          <div class="dash-loja-grupo">
            <div class="dash-loja-titulo">${l.nome} — verificando online...</div>
            <div class="dash-pdv-grid">${l.pdvs.map(p =>
              `<div class="pdv-card" style="cursor:default;">
                <div class="pdv-name">${p.nome || p.id}</div>
                <div class="pdv-ip text-muted">${p.ip || ""}</div>
                <div class="badge"><span class="dot"></span>Verificando...</div>
              </div>`).join("")}
            </div>
          </div>`).join("");
  }

  // Overlay fecha ao fim da Fase 1 — Fase 2 roda em background
  _dashOcultarLoading();
  _dashFase2(lojas, historico);
}

function _dashFase2(lojasList, historico) {
  // Helper: fetch com timeout proprio por AbortController
  function _f(url, ms) {
    const c = new AbortController();
    setTimeout(() => c.abort(), ms || 8000);
    return fetch(url, { signal: c.signal }).then(r => r.json()).catch(() => null);
  }

  // Grupo A: ERP status + integrador + sysinfo + erp stats
  // Cada um independente, atualiza o UI quando chega
  _f(API("/erp_db/status")).then(d => {
    atualizarKpiErpDb(d || { online: false });
    _atualizarBannerAlertas();
  });
  _f(API("/integrador/status")).then(d => {
    atualizarKpiIntegrador(d || { status: "erro", erro: "Falha." });
    _atualizarBannerAlertas();
  });
  // Versão do integrador via SSH — chamada separada pois pode ser lenta
  fetch(API("/integrador/versao_atual")).then(r => r.json()).catch(() => null).then(d => {
    const el = document.getElementById("kpiIntegradorVersao");
    if (!el) return;
    if (d?.versao) {
      el.innerHTML = `<div class="kpi-integrador-versao">🏷 ${d.versao}</div>`;
    }
  });
  _f(API("/sysinfo")).then(d => {
    const el = document.getElementById("kpiSysinfo");
    if (el && d) {
      const agora = new Date().toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
      el.innerHTML = renderSysinfo(d, agora);
    }
  });
  _f(API("/erp_db/stats")).then(d => {
    const el = document.getElementById("kpiErpDbStats");
    if (el) el.innerHTML = renderErpStats(d);
  });
  _f(API("/erp_db/pendencias_fiscais"), 12000).then(d => {
    if (d) { _fiscalCache = d; _renderKpiFiscal(d); }
  });

  // Grupo B: PDVs ativos ERP + pings — dependem um do outro, mas não bloqueiam o resto
  Promise.all([
    _f(API("/erp_db/pdvs_ativos"), 10000),
    statusOnlinePorLoja(lojasList),
  ]).then(([pdvsAtivosErp, statusPorLoja]) => {
    pdvsAtivosErp = pdvsAtivosErp || { erro: "timeout", lojas: [] };
    const lojasErp = pdvsAtivosErp.lojas || [];
    // Atualiza mapa global ecf→modelo para renderPDVs (seletores) usar
    lojasErp.forEach(g => g.pdvs.forEach(p => {
      if (typeof p === "object") _modelosPorEcf[p.ecf] = { modelo_id: p.modelo_id, modelo: p.modelo };
    }));
    const lojasCruzadas = cruzarLojasErpComOnline(lojasErp, lojasList, statusPorLoja);
    const ultimaPorPdv = ultimaReplicacaoPorPdv(historico);
    const ocultarOffline = localStorage.getItem("dashOcultarOffline") === "1";

    if (!pdvsAtivosErp.erro) {
      const totalAtivos = lojasCruzadas.reduce((a, l) => a + l.pdvs.length, 0);
      const totalOnline = lojasCruzadas.reduce((a, l) => a + l.pdvs.filter(p => p.status === "online").length, 0);
      const elLojas = document.getElementById("kpiLojas");
      const elPdvs = document.getElementById("kpiPdvs");
      const elPdvsSub = document.getElementById("kpiPdvsSub");
      if (elLojas) elLojas.textContent = lojasCruzadas.length;
      if (elPdvs) elPdvs.textContent = totalOnline;
      if (elPdvsSub) elPdvsSub.textContent = `de ${totalAtivos} ativo(s) no ERP`;
      const elPdvsDetalhe = document.getElementById("kpiPdvsDetalhe");
      if (elPdvsDetalhe) {
        const off = totalAtivos - totalOnline;
        const linhasLoja = lojasCruzadas.map(loja => {
          const on = loja.pdvs.filter(p => p.status === "online").length;
          const of2 = loja.pdvs.length - on;
          return `<div class="kpi-pdv-loja">
            <div class="kpi-pdv-loja-nome">${loja.nome}</div>
            <div class="kpi-pdv-loja-stats">
              <span class="kpi-pdv-loja-on">● ${on} online</span>
              <span class="kpi-pdv-loja-off">● ${of2} offline</span>
            </div>
          </div>`;
        }).join("");
        elPdvsDetalhe.innerHTML = `${linhasLoja}
          <div class="kpi-pdv-linha" style="margin-top:4px;padding-top:6px;border-top:1px solid var(--border);">
            <span class="kpi-pdv-grupo"><span class="kpi-pdv-dot kpi-pdv-dot--online"></span><span class="kpi-pdv-count">${totalOnline}</span><span class="kpi-pdv-label">online</span></span>
            <span class="kpi-pdv-grupo"><span class="kpi-pdv-dot kpi-pdv-dot--offline"></span><span class="kpi-pdv-count">${off}</span><span class="kpi-pdv-label">offline</span></span>
            <span class="kpi-pdv-total">${totalAtivos} total</span>
          </div>`;
      }
    }

    const elOnline = document.getElementById("dashOnlinePorLoja");
    if (elOnline) {
      _dashUltimoEstado = { lojasCruzadas, ultimaPorPdv, erroErp: pdvsAtivosErp.erro };
      elOnline.innerHTML = renderLojasEPdvs(lojasCruzadas, ultimaPorPdv, pdvsAtivosErp.erro, ocultarOffline);
      _iniciarPolingSysinfoLojas(lojasCruzadas);
    }
  });

  // Grupo C: lojas ERP (mais lento, lazy) — mostra placeholder imediatamente
  const elLojasLista = document.getElementById("kpiLojasLista");
  if (elLojasLista) elLojasLista.innerHTML = '<div class="kpi-loja-apelido" style="color:var(--text-faint);font-style:italic;">Buscando informações...</div>';
  _carregarLojasErp();
}

// Estado dos cards de ERP/integrador para o banner (atualizado independentemente)
let _erpStatus = null, _integradorStatus = null;
function atualizarKpiErpDb(dados) {
  _erpStatus = dados;
  const el = document.getElementById("kpiErpDb");
  const sub = document.getElementById("kpiErpDbSub");
  const card = document.getElementById("kpiCardErpDb");
  if (!el) return;
  el.textContent = dados.online ? "🟢 Online" : "🔴 Offline";
  if (sub) sub.textContent = dados.online ? "" : (dados.erro || "Sem conexão com o servidor ERP");
  if (card) {
    card.classList.remove("kpi-card--ok", "kpi-card--error", "kpi-card--warning");
    card.classList.add(dados.online ? "kpi-card--ok" : "kpi-card--error");
  }
}
function atualizarKpiIntegrador(dados) {
  _integradorStatus = dados;
  const el = document.getElementById("kpiIntegrador");
  const sub = document.getElementById("kpiIntegradorSub");
  const card = document.getElementById("kpiCardIntegrador");
  if (!el) return;
  const rotulos = { ok: "🟢 OK", atencao: "🟡 Atenção", erro: "🔴 Erro", offline: "🔴 Offline", nao_configurado: "— Não configurado" };
  el.textContent = rotulos[dados.status] || "—";
  if (sub) sub.textContent = dados.status === "ok" || dados.status === "nao_configurado" ? "" : (dados.erro || "");
  if (card) {
    card.classList.remove("kpi-card--ok", "kpi-card--error", "kpi-card--warning");
    if (dados.status === "ok") card.classList.add("kpi-card--ok");
    else if (dados.status === "atencao") card.classList.add("kpi-card--warning");
    else if (dados.status !== "nao_configurado") card.classList.add("kpi-card--error");
  }
}
function _atualizarBannerAlertas() {
  const dashAlertas = document.getElementById("dashAlertas");
  if (!dashAlertas) return;
  const alertas = [];
  if (_erpStatus && !_erpStatus.online) {
    const d = _erpStatus.erro ? ` — ${_erpStatus.erro}` : "";
    alertas.push(`<div class="dash-alerta dash-alerta--erro">🔴 <span><b>Banco de dados ERP offline.</b>${d}</span></div>`);
  }
  if (_integradorStatus && _integradorStatus.status !== "ok" && _integradorStatus.status !== "nao_configurado") {
    const cls = _integradorStatus.status === "atencao" ? "dash-alerta--atencao" : "dash-alerta--erro";
    const icone = _integradorStatus.status === "atencao" ? "⚠️" : "🔴";
    const titulo = _integradorStatus.status === "offline" ? "Integrador offline." : _integradorStatus.status === "atencao" ? "Integrador com atenção." : "Integrador com erro.";
    const d = _integradorStatus.erro ? ` — ${_integradorStatus.erro}` : "";
    alertas.push(`<div class="dash-alerta ${cls}">${icone} <span><b>${titulo}</b>${d}</span></div>`);
  }
  dashAlertas.innerHTML = alertas.join("");
}

// ──────────────────────────────────────────────
// PENDÊNCIAS FISCAIS
// ──────────────────────────────────────────────
let _fiscalCache = null;

async function carregarPendenciasFiscais() {
  try {
    const r = await fetch(API("/erp_db/pendencias_fiscais"));
    const dados = await r.json();
    _fiscalCache = dados;
    _renderKpiFiscal(dados);
    _renderFiscalView(dados);
  } catch (_) { /* ERP pode não estar configurado */ }
}

function _renderKpiFiscal(dados) {
  const totalDias = dados.consistencia?.total ?? 0;
  const totalNfce = dados.nfce?.total ?? 0;
  const card = document.getElementById("kpiCardFiscal");

  // ─── Consistência por loja ───────────────────
  const elConsist = document.getElementById("kpiFiscalConsistencia");
  if (elConsist) {
    if (dados.erro || !dados.consistencia?.por_loja?.length) {
      elConsist.innerHTML = `<div class="kpi-fiscal-linha">
        <span class="kpi-fiscal-dot" style="background:${totalDias === 0 ? "var(--green)" : "var(--amber)"}"></span>
        <span class="kpi-fiscal-num">${totalDias}</span>
        <span class="kpi-fiscal-label">dias sem fechar</span></div>`;
    } else {
      const linhas = dados.consistencia.por_loja.map(l => {
        const ok = l.dias_pendentes === 0;
        const cor = ok ? "var(--green)" : "var(--amber)";
        const txt = ok
          ? `<span style="color:var(--green);font-size:11px;">✔ em dia</span>`
          : `<span style="color:var(--amber);font-weight:700;font-size:13px">${l.dias_pendentes}</span><span class="kpi-fiscal-label"> dias</span>`;
        return `<div class="kpi-fiscal-loja-linha">
          <span class="kpi-fiscal-dot" style="background:${cor}"></span>
          <span class="kpi-fiscal-loja-nome">${l.loja}</span>
          ${txt}
        </div>`;
      }).join("");
      elConsist.innerHTML = `<div class="kpi-fiscal-secao-titulo">Consistência</div>${linhas}`;
    }
  }

  // ─── NFC-e por loja ──────────────────────────
  const elNfce = document.getElementById("kpiFiscalNfce");
  if (elNfce) {
    if (dados.erro || !dados.nfce?.por_loja?.length) {
      elNfce.innerHTML = `<div class="kpi-fiscal-linha">
        <span class="kpi-fiscal-dot" style="background:${totalNfce === 0 ? "var(--green)" : (totalNfce > 20 ? "var(--red)" : "var(--amber)")}"></span>
        <span class="kpi-fiscal-num">${totalNfce}</span>
        <span class="kpi-fiscal-label">NFC-e pendentes</span></div>`;
    } else {
      const linhas = dados.nfce.por_loja.map(l => {
        const ok = l.pendentes === 0;
        const cor = ok ? "var(--green)" : (l.pendentes > 20 ? "var(--red)" : "var(--amber)");
        const txt = ok
          ? `<span style="color:var(--green);font-size:11px;">✔ em dia</span>`
          : `<span style="color:${cor};font-weight:700;font-size:13px">${l.pendentes}</span><span class="kpi-fiscal-label"> pendentes</span>`;
        return `<div class="kpi-fiscal-loja-linha">
          <span class="kpi-fiscal-dot" style="background:${cor}"></span>
          <span class="kpi-fiscal-loja-nome">${l.loja}</span>
          ${txt}
        </div>`;
      }).join("");
      elNfce.innerHTML = `<div class="kpi-fiscal-secao-titulo">NFC-e</div>${linhas}`;
    }
  }

  // ─── Cor do card ─────────────────────────────
  if (card) {
    card.classList.remove("kpi-card--ok", "kpi-card--warning", "kpi-card--error");
    if (!dados.erro) {
      if (totalDias === 0 && totalNfce === 0) card.classList.add("kpi-card--ok");
      else if (totalNfce > 20 || totalDias > 5) card.classList.add("kpi-card--error");
      else card.classList.add("kpi-card--warning");
    }
  }
}

// Agrupa array por campo string, mantendo ordem de primeira aparição
function _agrupar(arr, campo) {
  const mapa = new Map();
  for (const item of arr) {
    const chave = item[campo];
    if (!mapa.has(chave)) mapa.set(chave, []);
    mapa.get(chave).push(item);
  }
  return mapa;
}

// Gera um id único para blocos expansíveis
let _fid = 0;
function _fuid() { return "fi" + (++_fid); }

function _toggleFiscal(id) {
  const el = document.getElementById(id);
  if (!el) return;
  const aberto = el.style.display !== "none";
  el.style.display = aberto ? "none" : "block";
  const btn = el.previousElementSibling?.querySelector(".fiscal-chevron");
  if (btn) btn.textContent = aberto ? "▶" : "▼";
}
window._toggleFiscal = _toggleFiscal;

function _toggleDateRows(trHeader, grpId) {
  const rows = trHeader.closest("table").querySelectorAll(`tr[data-grp="${grpId}"]`);
  const aberto = rows.length > 0 && rows[0].style.display !== "none";
  rows.forEach(r => { r.style.display = aberto ? "none" : ""; });
  const ch = trHeader.querySelector(".fiscal-chevron");
  if (ch) ch.textContent = aberto ? "▶" : "▼";
}
window._toggleDateRows = _toggleDateRows;

function _renderFiscalView(dados) {
  const elC = document.getElementById("fiscalConsistenciaTabela");
  const elN = document.getElementById("fiscalNfceTabela");
  if (!elC && !elN) return;

  if (dados.erro) {
    const msg = `<div class="empty">Erro ao consultar o ERP: ${dados.erro}</div>`;
    if (elC) elC.innerHTML = msg;
    if (elN) elN.innerHTML = msg;
    return;
  }

  // ── Consistência: loja > tabela única com datas ────────────────────
  if (elC) {
    const dias = dados.consistencia.dias;
    if (dias.length === 0) {
      elC.innerHTML = '<div class="empty ok-empty">✔ Todos os dias com consistência finalizada.</div>';
    } else {
      const porLoja = _agrupar(dias, "loja");
      let html = "";
      for (const [loja, itens] of porLoja) {
        const idBloco = _fuid();
        const rows = itens.map(d => `<tr><td class="fiscal-td-data">${d.data}</td></tr>`).join("");
        html += `
          <div class="fiscal-grupo-header" onclick="_toggleFiscal('${idBloco}')">
            <span class="fiscal-chevron">▼</span>
            <span class="fiscal-grupo-nome">${loja}</span>
            <span class="fiscal-grupo-badge">${itens.length} dia${itens.length > 1 ? "s" : ""}</span>
          </div>
          <div id="${idBloco}" class="fiscal-grupo-corpo">
            <table class="fiscal-table fiscal-table--sub">
              <tbody>${rows}</tbody>
            </table>
          </div>`;
      }
      elC.innerHTML = html;
    }
  }

  // ── NFC-e: loja > tabela única, datas como linhas colspan ─────────
  if (elN) {
    const pend = dados.nfce.pendentes;
    if (pend.length === 0) {
      elN.innerHTML = '<div class="empty ok-empty">✔ Nenhum NFC-e pendente de transmissão.</div>';
    } else {
      const porLoja = _agrupar(pend, "loja");
      let html = "";
      for (const [loja, itenLoja] of porLoja) {
        const idLoja = _fuid();
        const porData = _agrupar(itenLoja, "data");
        let tbody = "";
        for (const [data, cupons] of porData) {
          const idData = _fuid();
          const totalValor = cupons.reduce((s, c) => s + c.valor, 0);
          const totalStr = `R$ ${totalValor.toFixed(2).replace(".", ",")}`;
          // Linha de data — clique toggle nas linhas do grupo
          tbody += `<tr class="fiscal-tr-data" onclick="_toggleDateRows(this,'${idData}')">
            <td colspan="5">
              <span class="fiscal-chevron">▼</span>
              <span class="fiscal-tr-data-label">${data}</span>
              <span class="fiscal-grupo-badge">${cupons.length} cupom${cupons.length > 1 ? "s" : ""} · ${totalStr}</span>
            </td>
          </tr>`;
          // Linhas dos cupons
          for (const p of cupons) {
            const sit = p.situacao.toLowerCase().replace(/ /g, "_");
            const cont = p.contingencia
              ? `<span class="modelo-tag" style="--tag-cor:#f97316;--tag-bg:#f9731620;margin-left:4px;font-size:9px;">CONT.</span>` : "";
            tbody += `<tr data-grp="${idData}">
              <td class="text-muted">${p.ecf}</td>
              <td class="mono">${p.numerocupom}</td>
              <td><span class="fiscal-sit fiscal-sit--${sit}">${p.situacao}</span>${cont}</td>
              <td class="mono">R$ ${p.valor.toFixed(2).replace(".", ",")}</td>
              <td class="text-muted" style="font-size:11px;">${p.motivo}</td>
            </tr>`;
          }
        }
        html += `
          <div class="fiscal-grupo-header" onclick="_toggleFiscal('${idLoja}')">
            <span class="fiscal-chevron">▼</span>
            <span class="fiscal-grupo-nome">${loja}</span>
            <span class="fiscal-grupo-badge">${itenLoja.length} cupom${itenLoja.length > 1 ? "s" : ""}</span>
          </div>
          <div id="${idLoja}" class="fiscal-grupo-corpo">
            <table class="fiscal-table fiscal-table--sub">
              <thead><tr><th>ECF</th><th>Cupom</th><th>Situação</th><th>Valor</th><th>Motivo</th></tr></thead>
              <tbody>${tbody}</tbody>
            </table>
          </div>`;
      }
      elN.innerHTML = html;
    }
  }
}

// ──────────────────────────────────────────────
// CONFIG INTEGRADOR — SSH
// ──────────────────────────────────────────────
async function salvarConfigIntegradorSsh() {
  const dados = {
    ssh_ip:      document.getElementById("integradorSshIp")?.value.trim() || "",
    ssh_porta:   parseInt(document.getElementById("integradorSshPorta")?.value, 10) || 22,
    ssh_usuario: document.getElementById("integradorSshUsuario")?.value.trim() || "",
    ssh_senha:   document.getElementById("integradorSshSenha")?.value || "",
  };
  await fetch(API("/integrador/config"), {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(dados),
  });
  const el = document.getElementById("integradorSshResultado");
  if (el) { el.textContent = "✔ Configuração SSH salva."; el.style.color = "var(--green)"; }
}
window.salvarConfigIntegradorSsh = salvarConfigIntegradorSsh;

function _preencherConfigIntegradorSsh(cfg) {
  const f = (id, val) => { const el = document.getElementById(id); if (el) el.value = val ?? ""; };
  f("integradorSshIp", cfg.ssh_ip);
  f("integradorSshPorta", cfg.ssh_porta || 22);
  f("integradorSshUsuario", cfg.ssh_usuario);
  // senha não preenche por segurança
}

// ──────────────────────────────────────────────
// ATUALIZAÇÃO DO INTEGRADOR
// ──────────────────────────────────────────────
let _intUpdStream = null;

async function intUpdCarregar() {
  // Preenche campos SSH da config
  const cfg = await fetch(API("/integrador/config")).then(r => r.json()).catch(() => null);
  if (cfg) _preencherConfigIntegradorSsh(cfg);

  const temSsh = cfg?.ssh_ip && cfg?.ssh_usuario;
  const aviso = document.getElementById("intUpdAviso");
  const btn = document.getElementById("btnIntUpd");
  if (aviso) aviso.style.display = temSsh ? "none" : "block";
  if (btn) btn.disabled = !temSsh;

  if (temSsh) {
    intUpdVerificarVersao();
    intContainerCarregar();
  }
}
window.intUpdCarregar = intUpdCarregar;

async function intUpdVerificarVersao() {
  const badge = document.getElementById("intUpdVersaoAtual");
  if (badge) { badge.textContent = "..."; badge.className = "intupd-versao-badge"; }
  const d = await fetch(API("/integrador/versao_atual")).then(r => r.json()).catch(() => null);
  if (!badge) return;
  if (!d || d.erro) {
    badge.textContent = d?.erro || "Erro";
    badge.className = "intupd-versao-badge intupd-versao-badge--erro";
    return;
  }
  badge.textContent = d.versao;
  badge.className = "intupd-versao-badge intupd-versao-badge--ok";
  // Sugerir próxima versão no campo de input
  const input = document.getElementById("intUpdNovaVersao");
  if (input && !input.value) {
    const prox = _sugerirProximaVersao(d.versao);
    if (prox) input.placeholder = `ex: ${prox}`;
  }
}
window.intUpdVerificarVersao = intUpdVerificarVersao;

function _sugerirProximaVersao(versao) {
  const m = versao?.match(/^v?(\d+)\.(\d+)\.(\d+)/);
  if (!m) return null;
  return `v${m[1]}.${m[2]}.${parseInt(m[3]) + 1}`;
}

function intUpdIniciar() {
  const input = document.getElementById("intUpdNovaVersao");
  const nova = input?.value.trim();
  if (!nova) { alert("Informe a versão alvo."); return; }

  // Fechar stream anterior se existir
  if (_intUpdStream) { _intUpdStream.close(); _intUpdStream = null; }

  // Mostrar cards de progresso e log
  const cardProg = document.getElementById("intUpdCardProgresso");
  const cardLog = document.getElementById("intUpdCardLog");
  const elPassos = document.getElementById("intUpdPassos");
  const elLog = document.getElementById("intUpdLog");
  if (cardProg) cardProg.style.display = "block";
  if (cardLog) cardLog.style.display = "block";
  if (elPassos) elPassos.innerHTML = "";
  if (elLog) elLog.innerHTML = "";

  const btn = document.getElementById("btnIntUpd");
  if (btn) btn.disabled = true;

  const url = API(`/integrador/atualizar_stream?versao=${encodeURIComponent(nova)}`);
  _intUpdStream = new EventSource(url);

  _intUpdStream.onmessage = (ev) => {
    const d = JSON.parse(ev.data);

    if (d.tipo === "passo") {
      const div = document.createElement("div");
      div.className = "intupd-passo" + (d.texto.startsWith("✅") ? " intupd-passo--ok" : d.texto.startsWith("✔") ? " intupd-passo--ok" : d.texto.startsWith("ℹ") ? " intupd-passo--info" : "");
      div.textContent = d.texto;
      if (elPassos) elPassos.appendChild(div);
      elPassos?.lastElementChild?.scrollIntoView({ behavior: "smooth" });

    } else if (d.tipo === "log") {
      const pre = document.createElement("div");
      pre.className = "intupd-log-linha";
      pre.textContent = d.texto;
      if (elLog) { elLog.appendChild(pre); elLog.scrollTop = elLog.scrollHeight; }

    } else if (d.tipo === "erro") {
      const div = document.createElement("div");
      div.className = "intupd-passo intupd-passo--erro";
      div.textContent = "✖ " + d.texto;
      if (elPassos) elPassos.appendChild(div);

    } else if (d.tipo === "fim") {
      _intUpdStream.close();
      _intUpdStream = null;
      if (btn) btn.disabled = false;
      if (d.sucesso && d.versao) {
        const badge = document.getElementById("intUpdVersaoAtual");
        if (badge) { badge.textContent = d.versao; badge.className = "intupd-versao-badge intupd-versao-badge--ok"; }
      }
    }
  };

  _intUpdStream.onerror = () => {
    _intUpdStream?.close();
    _intUpdStream = null;
    if (btn) btn.disabled = false;
    const div = document.createElement("div");
    div.className = "intupd-passo intupd-passo--erro";
    div.textContent = "✖ Conexão com o servidor perdida.";
    if (elPassos) elPassos.appendChild(div);
  };
}
window.intUpdIniciar = intUpdIniciar;

// ──────────────────────────────────────────────
// GERENCIAMENTO DO CONTAINER DO INTEGRADOR
// ──────────────────────────────────────────────
let _intContainerRodando = null;

function _escHtml(str) {
  return String(str).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
}

async function intContainerCarregar() {
  const badge = document.getElementById("intContainerStatus");
  const btn = document.getElementById("btnIntContainerAcao");
  if (badge) { badge.textContent = "..."; badge.className = "intupd-versao-badge"; }
  if (btn) btn.disabled = true;

  const d = await fetch(API("/integrador/container_status")).then(r => r.json()).catch(() => null);
  if (!badge) return;

  if (!d || !d.ok) {
    badge.textContent = d?.erro || "Erro SSH";
    badge.className = "intupd-versao-badge intupd-versao-badge--erro";
    return;
  }
  _intContainerRodando = d.rodando;
  _intAtualizarBotaoContainer(d.rodando, d.status);
}
window.intContainerCarregar = intContainerCarregar;

function _intAtualizarBotaoContainer(rodando, status) {
  const badge = document.getElementById("intContainerStatus");
  const btn = document.getElementById("btnIntContainerAcao");
  if (badge) {
    badge.textContent = status;
    badge.className = "intupd-versao-badge " + (rodando ? "intupd-versao-badge--ok" : "intupd-versao-badge--erro");
  }
  if (btn) {
    btn.textContent = rodando ? "⏹ Stop" : "▶ Start";
    btn.disabled = false;
    btn.style.background = rodando ? "var(--red,#ef4444)" : "var(--green,#22c55e)";
    btn.style.borderColor = rodando ? "var(--red,#ef4444)" : "var(--green,#22c55e)";
  }
}

async function intContainerStartStop() {
  const btn = document.getElementById("btnIntContainerAcao");
  if (!btn || btn.disabled) return;
  const acao = _intContainerRodando ? "stop" : "start";
  btn.disabled = true;
  btn.textContent = acao === "stop" ? "Parando..." : "Iniciando...";

  await fetch(API("/integrador/container_acao"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ acao })
  }).catch(() => null);

  await intContainerCarregar();
}
window.intContainerStartStop = intContainerStartStop;

async function intContainerVerLogs() {
  const wrap = document.getElementById("intContainerLogWrap");
  const logEl = document.getElementById("intContainerLog");
  const btnLogs = document.getElementById("btnIntContainerLogs");
  if (!wrap) return;

  if (wrap.style.display !== "none") {
    wrap.style.display = "none";
    if (btnLogs) btnLogs.textContent = "📋 Ver logs";
    return;
  }

  wrap.style.display = "block";
  if (btnLogs) btnLogs.textContent = "🔼 Fechar logs";
  if (logEl) logEl.innerHTML = '<div class="intupd-log-linha" style="opacity:.6">Carregando logs...</div>';

  const d = await fetch(API("/integrador/logs?linhas=100")).then(r => r.json()).catch(() => null);
  if (!logEl) return;

  if (!d || !d.ok) {
    logEl.innerHTML = `<div class="intupd-log-linha intupd-passo--erro">Erro: ${_escHtml(d?.erro || "falha ao buscar logs")}</div>`;
    return;
  }
  logEl.innerHTML = d.linhas.map(l => `<div class="intupd-log-linha">${_escHtml(l)}</div>`).join("");
  logEl.scrollTop = logEl.scrollHeight;
}
window.intContainerVerLogs = intContainerVerLogs;

// ──────────────────────────────────────────────
// TEMA
// ──────────────────────────────────────────────
function toggleMode() {
  const app = document.getElementById("app");
  const claro = app.classList.contains("light");
  app.classList.toggle("light", !claro);
  app.classList.toggle("dark", claro);
  localStorage.setItem("theme", claro ? "dark" : "light");
}
window.toggleMode = toggleMode;

// ──────────────────────────────────────────────
// INIT
// ──────────────────────────────────────────────
async function init() {
  const app = document.getElementById("app");
  const temaSalvo = localStorage.getItem("theme") || "dark";
  app.classList.add(temaSalvo);
  const toggleInput = document.getElementById("themeToggle");
  if (toggleInput) toggleInput.checked = temaSalvo === "light";
  restaurarEstadoMenu();
  configurarDropZone();

  // Mostra o overlay imediatamente, antes de qualquer await,
  // para o usuario ver feedback instantâneo ao abrir o painel.
  _dashMostrarLoading();

  // Todas as chamadas de init em paralelo — reduz o tempo total
  // de sum(cada_call) para max(call_mais_lento).
  // carregarLojas() salva no cache para carregarDashboard() usar
  // sem novo fetch.
  await Promise.all([
    carregarLojas(),
    verificarAgenteDisponivel(),
    carregarArquivos(),
    carregarConfigReplicacaoAuto(),
    carregarHistoricoReplicacao(),
    carregarConfigErpDb(),
    carregarConfigIntegrador(),
  ]);

  mostrarView("dashboard");
}

document.addEventListener("DOMContentLoaded", init);
