// ──────────────────────────────────────────────
// ESTADO GLOBAL
// ──────────────────────────────────────────────
let lojas = [];
let arquivoSelecionado = null;
let eventSource = null;
let statusPDVs = {}; // progresso de atualizacao de zip (somente view "pdv"), por pdvId

const KEYS = ["agente", "pdv", "replicacao"];
const seletores = {};
for (const k of KEYS) {
  seletores[k] = { lojaAtiva: null, selecionados: new Set(), ping: {} };
}

let pollReplicacaoTimer = null;

// ──────────────────────────────────────────────
// NAVEGACAO (sidebar + views)
// ──────────────────────────────────────────────
function mostrarView(nome) {
  for (const v of ["dashboard", "agente", "pdv", "replicacao"]) {
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
    return `
      <div class="pdv-card ${sel ? 'selected' : ''}" onclick="togglePDV('${key}','${pdv.id}')">
        <div class="pdv-name">${pdv.nome || pdv.id}</div>
        <div class="pdv-ip">${pdv.ip}</div>
        ${badge}
        ${bar}
      </div>
    `;
  }).join("");
}

function togglePDV(key, pdvId) {
  const sel = seletores[key].selecionados;
  if (sel.has(pdvId)) sel.delete(pdvId); else sel.add(pdvId);
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
    const r = await fetch(`/api/ping_loja/${loja.id}`);
    const dados = await r.json();
    for (const pdv of loja.pdvs) {
      seletores[key].ping[pdv.id] = dados[pdv.id] || { online: false };
    }
  } catch (e) { /* mantem offline */ }
  renderPDVs(key);
}
window.verificarOnline = verificarOnline;

async function carregarLojas() {
  const r = await fetch("/api/lojas");
  lojas = await r.json();
  for (const key of KEYS) renderLojaTabs(key);
}

async function redescobrir() {
  await fetch("/api/lojas/atualizar", { method: "POST" });
  await carregarLojas();
}
window.redescobrir = redescobrir;

// ──────────────────────────────────────────────
// ATUALIZACAO DE AGENTE (view "agente")
// ──────────────────────────────────────────────
async function verificarAgenteDisponivel() {
  const r = await fetch("/api/versao_agente");
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
  await fetch("/api/upload_agente", { method: "POST", body: fd });
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
  const r = await fetch("/api/atualizar_agente", {
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
  await fetch("/api/upload", { method: "POST", body: fd });
  if (progEl) progEl.style.display = "none";
  await carregarArquivos();
}

async function carregarArquivos() {
  const r = await fetch("/api/arquivos");
  const arquivos = await r.json();
  const el = document.getElementById("fileList");
  if (!el) return;
  if (arquivos.length === 0) {
    el.innerHTML = '<div class="empty">Nenhum arquivo enviado ainda.</div>';
    return;
  }
  el.innerHTML = arquivos.map(a => `
    <div class="file-item ${a.nome === arquivoSelecionado ? 'selected' : ''}" onclick="selecionarArquivo('${a.nome}')">
      <div>
        <div class="file-name">${a.nome}</div>
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
  await fetch(`/api/arquivos/${encodeURIComponent(nome)}`, { method: "DELETE" });
  if (arquivoSelecionado === nome) arquivoSelecionado = null;
  await carregarArquivos();
}
window.deletarArquivo = deletarArquivo;

async function limparTodos() {
  if (!confirm("Remover todos os arquivos enviados?")) return;
  await fetch("/api/arquivos/limpar", { method: "DELETE" });
  arquivoSelecionado = null;
  await carregarArquivos();
}
window.limparTodos = limparTodos;

function atualizarBotaoPdv() {
  const btn = document.getElementById("btnAtualizarPdv");
  if (!btn) return;
  btn.disabled = !arquivoSelecionado || seletores.pdv.selecionados.size === 0;
}

async function iniciarAtualizacao() {
  const loja_id = seletores.pdv.lojaAtiva;
  const pdv_ids = Array.from(seletores.pdv.selecionados);
  if (!loja_id || !arquivoSelecionado || pdv_ids.length === 0) return;

  const r = await fetch("/api/atualizar", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ loja_id, pdv_ids, arquivo: arquivoSelecionado }),
  });
  const dados = await r.json();
  if (dados.erro) { alert(dados.erro); return; }

  if (eventSource) eventSource.close();
  eventSource = new EventSource(`/api/status_stream/${loja_id}`);
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
  const link = `/replicacao/detalhe/${seletores.replicacao.lojaAtiva}/${pdvId}/${nome}`;
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
    el.innerHTML = `<div class="empty" style="color:#dc2626;">Erro: ${dados.erro}</div>`;
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

  const r = await fetch("/api/replicacao/verificar", {
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
    const r = await fetch(`/api/replicacao/status/${loja_id}/${pdvId}`);
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

async function carregarConfigReplicacaoAuto() {
  const r = await fetch("/api/replicacao/config");
  const cfg = await r.json();
  const hab = document.getElementById("repAutoHabilitado");
  const intervalo = document.getElementById("repAutoIntervalo");
  if (hab) hab.checked = !!cfg.habilitado;
  if (intervalo) intervalo.value = cfg.intervalo_minutos || 60;
}

async function salvarConfigReplicacaoAuto() {
  const hab = document.getElementById("repAutoHabilitado");
  const intervalo = document.getElementById("repAutoIntervalo");
  await fetch("/api/replicacao/config", {
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
  const r = await fetch("/api/replicacao/historico");
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
// DASHBOARD
// ──────────────────────────────────────────────
function svgGraficoBarras(historico) {
  const ultimos = historico.slice(0, 10).reverse();
  if (ultimos.length === 0) {
    return '<div class="empty">Sem histórico suficiente para o gráfico ainda.</div>';
  }
  const W = 480, H = 140, pad = 20;
  const max = Math.max(1, ...ultimos.map(h => Object.values(h.pdvs || {}).filter(p => p.tem_divergencia).length));
  const larguraBarra = (W - pad * 2) / ultimos.length;
  const barras = ultimos.map((h, i) => {
    const qtd = Object.values(h.pdvs || {}).filter(p => p.tem_divergencia).length;
    const alturaMax = H - pad * 2;
    const altura = qtd === 0 ? 2 : (qtd / max) * alturaMax;
    const x = pad + i * larguraBarra + 4;
    const y = H - pad - altura;
    const cor = qtd > 0 ? "#dc2626" : "#16a34a";
    return `<rect x="${x}" y="${y}" width="${larguraBarra - 8}" height="${altura}" rx="3" fill="${cor}">
      <title>${h.timestamp}: ${qtd} PDV(s) com divergência</title>
    </rect>`;
  }).join("");
  return `
    <div class="chart-wrap">
      <svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}">${barras}</svg>
      <div class="chart-legend">
        <span><span class="dot" style="background:#16a34a;"></span> sem divergência</span>
        <span><span class="dot" style="background:#dc2626;"></span> com divergência</span>
      </div>
    </div>
  `;
}

async function carregarDashboard() {
  const [lojasResp, cfg, historico] = await Promise.all([
    fetch("/api/lojas").then(r => r.json()).catch(() => []),
    fetch("/api/replicacao/config").then(r => r.json()).catch(() => ({})),
    fetch("/api/replicacao/historico").then(r => r.json()).catch(() => []),
  ]);
  lojas = lojasResp;

  const totalLojas = lojas.length;
  const totalPdvs = lojas.reduce((acc, l) => acc + l.pdvs.length, 0);

  const elLojas = document.getElementById("kpiLojas");
  const elPdvs = document.getElementById("kpiPdvs");
  const elAuto = document.getElementById("kpiAuto");
  const elUltima = document.getElementById("kpiUltima");
  if (elLojas) elLojas.textContent = totalLojas;
  if (elPdvs) elPdvs.textContent = totalPdvs;
  if (elAuto) elAuto.textContent = cfg.habilitado ? `Ativa (${cfg.intervalo_minutos}min)` : "Desativada";

  if (elUltima) {
    if (historico.length === 0) {
      elUltima.textContent = "—";
    } else {
      const h0 = historico[0];
      elUltima.textContent = h0.tem_divergencia ? "⚠️ Com divergência" : "✔ Sem divergência";
    }
  }

  const grafico = document.getElementById("dashGrafico");
  if (grafico) grafico.innerHTML = svgGraficoBarras(historico);
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

  await carregarLojas();
  await verificarAgenteDisponivel();
  await carregarArquivos();
  await carregarConfigReplicacaoAuto();
  await carregarHistoricoReplicacao();
  configurarDropZone();
  mostrarView("dashboard");
}

document.addEventListener("DOMContentLoaded", init);
