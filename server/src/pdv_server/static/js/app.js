// ──────────────────────────────────────────────
// API (escopada pela rede ativa -- window.REDE_ID injetado pelo template)
// ──────────────────────────────────────────────
function API(caminho) {
  return `/api/${window.REDE_ID}${caminho}`;
}

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

let pollReplicacaoTimer = null;

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
  for (const v of ["dashboard", "agente", "pdv", "replicacao", "config"]) {
    const el = document.getElementById(`view-${v}`);
    if (el) el.style.display = v === nome ? "flex" : "none";
  }
  document.querySelectorAll(".menu-item").forEach(el => {
    el.classList.toggle("active", el.dataset.view === nome);
  });
  if (nome === "dashboard") carregarDashboard();
}
window.mostrarView = mostrarView;

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
    const bar = prog && prog.progresso != null
      ? `<div class="pdv-progress"><div class="bar-wrap"><div class="bar" style="width:${prog.progresso}%"></div></div>${etapa}</div>`
      : "";
    const versaoPdv = key === "pdv" && pdv.versao ? `<div class="pdv-versao">v${pdv.versao}</div>` : "";
    const versaoAgente = key === "agente"
      ? `<div class="pdv-versao-agente">agente v${ping && ping.versao_agente ? ping.versao_agente : "—"}</div>`
      : "";
    return `
      <div class="pdv-card ${sel ? 'selected' : ''}" onclick="togglePDV('${key}','${pdv.id}')">
        <div class="pdv-name">${pdv.nome || pdv.id}</div>
        <div class="pdv-ip">${pdv.ip}</div>
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
  const fd = new FormData();
  fd.append("arquivo", arquivo);
  await fetch(API("/upload_agente"), { method: "POST", body: fd });
  input.value = "";
  await verificarAgenteDisponivel();
}
window.uploadAgente = uploadAgente;

function atualizarBotaoAgente() {
  const btn = document.getElementById("btnAtualizarAgente");
  if (!btn) return;
  btn.disabled = seletores.agente.selecionados.size === 0;
}

async function iniciarAtualizacaoAgente() {
  const loja_id = seletores.agente.lojaAtiva;
  const pdv_ids = Array.from(seletores.agente.selecionados);
  if (!loja_id || pdv_ids.length === 0) return;
  const r = await fetch(API("/atualizar_agente"), {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ loja_id, pdv_ids }),
  });
  const dados = await r.json();
  alert(dados.mensagem || dados.erro || "Concluído");
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
  el.innerHTML = arquivos.map(a => `
    <div class="file-item ${a.nome === arquivoSelecionado ? 'selected' : ''}" onclick="selecionarArquivo('${a.nome}')">
      <div>
        <div class="file-name">${a.nome} ${a.versao ? `<span class="file-versao">v${a.versao}</span>` : ''}</div>
        <div class="file-meta text-muted">${a.tamanho_mb} MB — ${a.data}</div>
      </div>
      <div class="file-actions">
        <button class="btn-del" onclick="event.stopPropagation(); deletarArquivo('${a.nome}')">🗑️</button>
      </div>
    </div>
  `).join("");
}

function selecionarArquivo(nome) {
  arquivoSelecionado = arquivoSelecionado === nome ? null : nome;
  carregarArquivos();
  atualizarBotaoPdv();
}
window.selecionarArquivo = selecionarArquivo;

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

  if (aviso) aviso.innerHTML = "";

  if (pdv && arquivo) {
    if (!arquivo.versao) {
      if (aviso) aviso.innerHTML = `<div class="card aviso-versao aviso-indefinido">⚠️ Não foi possível identificar a versão do arquivo <strong>${arquivo.nome}</strong> pelo nome. Renomeie incluindo a versão (ex: VRPdvPro_7.1.0.zip).</div>`;
    } else if (pdv.versao && ehDowngrade(arquivo.versao, pdv.versao)) {
      bloqueadoPorVersao = true;
      if (aviso) aviso.innerHTML = `<div class="card aviso-versao aviso-bloqueado">⛔ Downgrade bloqueado: o pacote é a versão <strong>${arquivo.versao}</strong>, mas o PDV ${pdv.id} já está na <strong>${pdv.versao}</strong>. Atualizar para uma versão anterior pode corromper o banco.</div>`;
    } else if (pdv.versao) {
      if (aviso) aviso.innerHTML = `<div class="card aviso-versao aviso-ok">✅ ${pdv.id}: ${pdv.versao} → ${arquivo.versao}</div>`;
    }
  }

  btn.disabled = !arquivoSelecionado || seletores.pdv.selecionados.size === 0 || bloqueadoPorVersao;
}

async function iniciarAtualizacao() {
  const loja_id = seletores.pdv.lojaAtiva;
  const pdv_ids = Array.from(seletores.pdv.selecionados);
  if (!loja_id || !arquivoSelecionado || pdv_ids.length === 0) return;

  const r = await fetch(API("/atualizar"), {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ loja_id, pdv_ids, arquivo: arquivoSelecionado }),
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

async function verificarReplicacaoSelecionados() {
  const loja_id = seletores.replicacao.lojaAtiva;
  const pdv_ids = Array.from(seletores.replicacao.selecionados);
  if (!loja_id || pdv_ids.length === 0) return;

  const r = await fetch(API("/replicacao/verificar"), {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ loja_id, pdv_ids }),
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
window.verificarReplicacaoSelecionados = verificarReplicacaoSelecionados;

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
function _formatarUptime(seg) {
  const d = Math.floor(seg / 86400);
  const h = Math.floor((seg % 86400) / 3600);
  const m = Math.floor((seg % 3600) / 60);
  if (d > 0) return `${d}d ${h}h`;
  if (h > 0) return `${h}h ${m}min`;
  return `${m}min`;
}

function renderSysinfo(dados) {
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

function atualizarKpiErpDb(dados) {
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

function atualizarKpiIntegrador(dados) {
  const el = document.getElementById("kpiIntegrador");
  const sub = document.getElementById("kpiIntegradorSub");
  const card = document.getElementById("kpiCardIntegrador");
  if (!el) return;
  const rotulos = {
    ok: "🟢 OK", atencao: "🟡 Atenção", erro: "🔴 Erro",
    offline: "🔴 Offline", nao_configurado: "— Não configurado",
  };
  el.textContent = rotulos[dados.status] || "—";
  if (sub) sub.textContent = dados.status === "ok" || dados.status === "nao_configurado" ? "" : (dados.erro || "");
  if (card) {
    card.classList.remove("kpi-card--ok", "kpi-card--error", "kpi-card--warning");
    if (dados.status === "ok") card.classList.add("kpi-card--ok");
    else if (dados.status === "atencao") card.classList.add("kpi-card--warning");
    else if (dados.status !== "nao_configurado") card.classList.add("kpi-card--error");
  }
}

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
    const pdvs = grupo.pdvs.map(ecf => {
      const pdvId = `PDV-${ecf}`;
      const pdvDescoberto = lojaDescoberta ? lojaDescoberta.pdvs.find(p => p.id === pdvId) : null;
      const statusPing = ping[pdvId];
      let status;
      if (!pdvDescoberto) status = "sem_comunicacao";
      else if (statusPing && statusPing.online) status = "online";
      else status = "offline";
      return { ecf, pdvId, pdv: pdvDescoberto, status };
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
      return `
        <div class="pdv-card" style="cursor:default;">
          <div class="pdv-name">${nome}</div>
          ${ip}
          ${versao}
          <div class="badge ${classe}"><span class="dot"></span>${texto}</div>
          ${replicacao}
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
    lojasList.map(l => fetch(API(`/ping_loja/${l.id}`)).then(r => r.json()).catch(() => ({})))
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

  const elUltima = document.getElementById("kpiUltima");
  if (elUltima) {
    elUltima.textContent = historico.length === 0 ? "—"
      : historico[0].tem_divergencia ? "⚠️ Com divergência" : "✔ Sem divergência";
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

  // ── Fase 2: checks lentos em background, não bloqueiam a UI ─────────
  _dashFase2(lojas, historico);
}

async function _dashFase2(lojasList, historico) {
  const [erpDbStatus, integradorStatus, pdvsAtivosErp, statusPorLoja, sysinfoData, erpStatsData] = await Promise.all([
    fetch(API("/erp_db/status")).then(r => r.json()).catch(() => ({ online: false })),
    fetch(API("/integrador/status")).then(r => r.json()).catch(() => ({ status: "erro", erro: "Falha ao consultar." })),
    fetch(API("/erp_db/pdvs_ativos")).then(r => r.json()).catch(() => ({ erro: "Falha ao consultar.", lojas: [] })),
    statusOnlinePorLoja(lojasList),
    fetch(API("/sysinfo")).then(r => r.json()).catch(() => null),
    fetch(API("/erp_db/stats")).then(r => r.json()).catch(() => null),
  ]);

  atualizarKpiErpDb(erpDbStatus);
  atualizarKpiIntegrador(integradorStatus);

  const elSysinfo = document.getElementById("kpiSysinfo");
  if (elSysinfo) elSysinfo.innerHTML = renderSysinfo(sysinfoData);

  const elErpStats = document.getElementById("kpiErpDbStats");
  if (elErpStats) elErpStats.innerHTML = renderErpStats(erpStatsData);

  const dashAlertas = document.getElementById("dashAlertas");
  if (dashAlertas) {
    const alertas = [];
    if (!erpDbStatus.online) {
      const detalhe = erpDbStatus.erro ? ` — ${erpDbStatus.erro}` : "";
      alertas.push(`<div class="dash-alerta dash-alerta--erro">🔴 <span><b>Banco de dados ERP offline.</b> O painel não consegue conectar ao servidor ERP${detalhe}. Verifique se o serviço está rodando e a configuração de acesso em Configurações.</span></div>`);
    }
    if (integradorStatus.status !== "ok" && integradorStatus.status !== "nao_configurado") {
      const cls = integradorStatus.status === "atencao" ? "dash-alerta--atencao" : "dash-alerta--erro";
      const icone = integradorStatus.status === "atencao" ? "⚠️" : "🔴";
      const titulo = integradorStatus.status === "offline" ? "Integrador offline." : integradorStatus.status === "atencao" ? "Integrador com atenção." : "Integrador com erro.";
      const detalhe = integradorStatus.erro ? ` — ${integradorStatus.erro}` : "";
      alertas.push(`<div class="dash-alerta ${cls}">${icone} <span><b>${titulo}</b>${detalhe}</span></div>`);
    }
    dashAlertas.innerHTML = alertas.join("");
  }

  const usarErp = !pdvsAtivosErp.erro;
  const lojasErp = pdvsAtivosErp.lojas || [];
  const lojasCruzadas = cruzarLojasErpComOnline(lojasErp, lojasList, statusPorLoja);

  if (usarErp) {
    const totalAtivosErp = lojasCruzadas.reduce((acc, l) => acc + l.pdvs.length, 0);
    const totalOnlineErp = lojasCruzadas.reduce((acc, l) => acc + l.pdvs.filter(p => p.status === "online").length, 0);
    const elLojas = document.getElementById("kpiLojas");
    const elPdvs = document.getElementById("kpiPdvs");
    const elPdvsSub = document.getElementById("kpiPdvsSub");
    if (elLojas) elLojas.textContent = lojasCruzadas.length;
    if (elPdvs) elPdvs.textContent = totalOnlineErp;
    if (elPdvsSub) elPdvsSub.textContent = `de ${totalAtivosErp} ativo(s) no ERP`;
  }

  const elOnline = document.getElementById("dashOnlinePorLoja");
  if (elOnline) {
    const ultimaPorPdv = ultimaReplicacaoPorPdv(historico);
    const ocultarOffline = localStorage.getItem("dashOcultarOffline") === "1";
    _dashUltimoEstado = { lojasCruzadas, ultimaPorPdv, erroErp: pdvsAtivosErp.erro };
    elOnline.innerHTML = renderLojasEPdvs(lojasCruzadas, ultimaPorPdv, pdvsAtivosErp.erro, ocultarOffline);
  }

  _dashOcultarLoading();
}

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
